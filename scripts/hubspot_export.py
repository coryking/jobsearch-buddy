"""One-shot migration of jsb's activity_log into HubSpot.

Modes:
  dry-run   group rows into pursuits, classify, and print the report + merge list (no writes)
  probe     create one throwaway deal to learn which date properties HubSpot lets us backdate
  import    create companies, contacts, deals, notes, meetings via the batch APIs
  wipe      delete every company/contact/deal/note/meeting in the portal (fresh-portal re-runs)

Run on a box that reaches the jsb Postgres and 1Password:
  uv run python scripts/hubspot_export.py dry-run
  uv run python scripts/hubspot_export.py import
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import date

import httpx

from jobbuddy.store import JobStore

TODAY = date.today()
OPEN_WINDOW_DAYS = 60
REAPPLY_GAP_DAYS = 60
KEYLESS_MERGE_DAYS = 30
STAGE_IDS = {"Applied": "4305280713", "Screen": "4305280714", "Interview": "4305280715", "closedlost": "closedlost"}
ALIASES = {"saloni": "saloni sonpal", "brad": "bradley johnson"}
MEETING_URL = re.compile(r"linkedin\.com/in/|meet\.google|calendar|zoom\.us")
BASE = "https://api.hubapi.com"

SQL = """
select a.id, a.log_date::text as date, a.company, a.role, coalesce(a.job_id,'') job_id,
       coalesce(a.action,'') action, coalesce(a.person,'') person, coalesce(a.location,'') location,
       coalesce(a.status,'') status, coalesce(a.url,'') url, coalesce(a.notes,'') notes,
       coalesce(min(c.slug),'') reg_slug,
       coalesce(min(j.title),'') j_title, coalesce(min(j.location),'') j_location,
       coalesce(min(j.salary),'') j_salary, coalesce(min(j.url),'') j_url
from activity_log a
left join companies c on lower(c.name)=lower(a.company) or c.slug=lower(a.company)
left join jobs j on j.job_id=a.job_id and j.job_id<>''
group by a.id order by a.log_date, a.id
"""


def d(s: str) -> date:
    return date.fromisoformat(s)


# ---------------------------------------------------------------- grouping

def norm_role(s: str) -> str:
    s = s.lower().replace("&amp;", "&").replace("senior+", "senior")
    s = re.sub(r"\(r-?\d+\)|\[pipeline\]|\br\d+\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    stripped = re.sub(r"\b(senior|sr|staff|principal|lead|ii|iii|technical|manager|product|the|of|and|a)\b", " ", s)
    return " ".join(stripped.split()) or " ".join(s.split())


def norm_url(u: str) -> str:
    return u.split("?")[0].rstrip("/").lower()


def posting_url(r: dict) -> str:
    return "" if (not r["url"] or MEETING_URL.search(r["url"])) else norm_url(r["url"])


class Groups:
    def __init__(self, rows: list[dict]):
        self.rows = {r["id"]: r for r in rows}
        self.parent = {r["id"]: r["id"] for r in rows}
        self.merge_log: list[tuple[str, dict, dict]] = []

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def job_ids(self, x) -> set[str]:
        root = self.find(x)
        return {r["job_id"] for r in self.rows.values() if self.find(r["id"]) == root and r["job_id"]}

    def keyless(self, x) -> bool:
        root = self.find(x)
        return not any(r["job_id"] or posting_url(r) for r in self.rows.values() if self.find(r["id"]) == root)

    def union(self, a, b, why: str) -> None:
        if self.find(a) == self.find(b):
            return
        ja, jb = self.job_ids(a), self.job_ids(b)
        if ja and jb and ja != jb:
            return  # two distinct reqs never merge, whatever the titles say
        self.merge_log.append((why, self.rows[a], self.rows[b]))
        self.parent[self.find(a)] = self.find(b)

    def groups(self) -> list[list[dict]]:
        g = defaultdict(list)
        for r in self.rows.values():
            g[self.find(r["id"])].append(r)
        out = list(g.values())
        for grp in out:
            grp.sort(key=lambda r: (r["date"], int(r["id"])))
        return out


def group_rows(rows: list[dict]) -> Groups:
    G = Groups(rows)
    by_co = defaultdict(list)
    for r in rows:
        by_co[r["company"].lower().strip()].append(r)
    for rs in by_co.values():
        idx = defaultdict(list)
        for r in rs:
            if r["job_id"]:
                idx[("id", r["job_id"])].append(r["id"])
            if posting_url(r):
                idx[("url", posting_url(r))].append(r["id"])
        for ids in idx.values():
            for i in ids[1:]:
                G.union(ids[0], i, "same job_id/url")
        ridx = defaultdict(list)
        for r in rs:
            nr = norm_role(r["role"])
            if nr:
                ridx[nr].append(r["id"])
        for ids in ridx.values():
            for i in ids[1:]:
                G.union(ids[0], i, "same role title")
        keyless = sorted((r for r in rs if not r["job_id"] and not posting_url(r)), key=lambda r: r["date"])
        for a, b in zip(keyless, keyless[1:]):
            both_apps = a["action"] == "Application" and b["action"] == "Application"
            close = (d(b["date"]) - d(a["date"])).days <= KEYLESS_MERGE_DAYS
            if close and not both_apps and G.keyless(a["id"]) and G.keyless(b["id"]):
                G.union(a["id"], b["id"], f"keyless within {KEYLESS_MERGE_DAYS}d")
    return G


def split_episodes(group: list[dict]) -> list[list[dict]]:
    out, cur, last_app = [], [], None
    for r in group:
        if r["action"] == "Application" and last_app and (d(r["date"]) - last_app).days > REAPPLY_GAP_DAYS:
            out.append(cur)
            cur = []
        if r["action"] == "Application":
            last_app = d(r["date"])
        cur.append(r)
    out.append(cur)
    return out


# ---------------------------------------------------------------- classification

OUTCOMES = [
    ("Withdrawn", r"withdr|i declined|declined the (offer|role|interview)|turned (it |them )?down|not interested|passed on (it|the role)|walked away"),
    ("Req closed", r"req(uisition)? (is |was )?(closed|dead|gone|filled|pulled)|no longer (open|posted|available|accepting)|posting (was |has been )?(removed|taken down)|confirmed dead|closed the req|role (was )?(filled|closed)|position filled"),
    ("Rejected", r"reject|\bdeclin(ed|e)\b(?! the)|not (be )?moving forward|other candidates|not selected|unsuccessful|won'?t be (moving|proceeding)|no longer under consideration|decided not to (move|proceed)|\bpass(ed)? on (me|my)|\bpass\b|not a fit|not the right fit"),
]
STAGE_ORD = {"Interview": 3, "Screen": 2, "Application": 1}


def outcome(text: str) -> str:
    for name, pat in OUTCOMES:
        if re.search(pat, text, re.I):
            return name
    return ""


def best_role(ep: list[dict]) -> str:
    cands = [r["j_title"] for r in ep if r["j_title"]] + [r["role"] for r in ep if r["role"] and r["role"] != "ROLE_NOT_NAMED"]
    return max(cands, key=len) if cands else ""


def midpoint(s: str) -> float | None:
    s = s.replace(",", "")
    nums = []
    for x, k in re.findall(r"(\d{5,6}(?:\.\d+)?|\d{2,3}(?:\.\d+)?)\s*(k|K)?", s):
        v = float(x) * (1000 if k else 1)
        if 50_000 <= v <= 1_000_000:
            nums.append(v)
    if len(nums) >= 2:
        return round((nums[0] + nums[1]) / 2)
    return round(nums[0]) if nums else None


def role_type(title: str) -> str:
    t = title.lower()
    if re.search(r"director|head of|\bvp\b|vice president", t):
        return "director"
    if re.search(r"product manager|product lead|\bpm\b|program manager|product owner", t):
        return "pm"
    if re.search(r"engineer|developer|\bsde\b|software|member of technical staff", t):
        return "swe"
    return ""


def source_channel(ep: list[dict]) -> str:
    acts = {r["action"] for r in ep}
    urls = " ".join(r["url"] + " " + r["j_url"] for r in ep).lower()
    notes = " ".join(r["notes"] for r in ep).lower()
    if "Referral" in acts:
        return "referral"
    if re.search(r"hacker ?news|\bhn\b|who is hiring|whoishiring", notes):
        return "hn_email"
    if re.search(r"recruiter reached out|reached out to me|inmail", notes):
        return "inbound"
    if re.search(r"\bdice\b|staffing|randstad|inspyr", notes):
        return "agency"
    if "linkedin.com" in urls:
        return "linkedin"
    if re.search(r"greenhouse|ashby|lever\.co|workday|rippling|jobvite|smartrecruiters|icims|bamboohr", urls):
        return "ats"
    return ""


def resume_sent(ep: list[dict]) -> str:
    for r in ep:
        m = re.search(r"([\w/.-]+\.typ)(?:[^\n]{0,40}?\b([0-9a-f]{7,10})\b)?", r["notes"])
        if m:
            return m.group(1) + (f" @ {m.group(2)}" if m.group(2) else "")
        m = re.search(r"output/[\w-]+\.pdf", r["notes"])
        if m:
            return m.group(0)
    return ""


def classify(ep: list[dict]) -> dict | None:
    mx = max(STAGE_ORD.get(r["action"], 0) for r in ep)
    if mx == 0:
        return None
    stage = {3: "Interview", 2: "Screen", 1: "Applied"}[mx]
    outs = [(r["date"], outcome(r["notes"] + " || " + r["status"])) for r in ep]
    outs = [o for o in outs if o[1]]
    last = d(ep[-1]["date"])
    if outs:
        disp = outs[-1][1]
    elif (TODAY - last).days <= OPEN_WINDOW_DAYS:
        disp = "OPEN"
    else:
        disp = "No response"
    apps = [r for r in ep if r["action"] == "Application"]
    title = best_role(ep)
    company = ep[0]["company"]
    sal = next((r["j_salary"] for r in ep if r["j_salary"]), "")
    return {
        "company": company,
        "dealname": f"{company} — {title}" if title else f"{company} — (role not named)",
        "stage": stage,
        "disposition": disp,
        "applied_date": apps[0]["date"] if apps else ep[0]["date"],
        "closedate": ep[-1]["date"] if disp != "OPEN" else "",
        "amount": midpoint(sal) if sal else None,
        "job_url": next((r["url"] for r in ep if posting_url(r)), "") or next((r["j_url"] for r in ep if r["j_url"]), ""),
        "job_id": next((r["job_id"] for r in ep if r["job_id"]), ""),
        "source_channel": source_channel(ep),
        "role_type": role_type(title),
        "resume_sent": resume_sent(ep),
        "rows": ep,
    }


# ---------------------------------------------------------------- people

def split_people(p: str) -> list[str]:
    p = re.sub(r"\s+", " ", p.strip())
    if "(" in p:
        return [p]
    return [x.strip() for x in re.split(r"\s*/\s*|\s+and\s+|\s*&\s*", p) if x.strip()]


def parse_person(p: str) -> dict:
    m = re.match(r'^"?(?P<name>[^("]+?)"?\s*(?:\((?P<paren>[^)]*)\))?\s*$', p)
    name = (m.group("name") if m else p).strip()
    paren = (m.group("paren") if m and m.group("paren") else "").strip()
    email = re.search(r"[\w.+-]+@[\w.-]+\.\w+", p)
    key = ALIASES.get(name.lower(), name.lower())
    parts = key.split()
    return {
        "key": key,
        "firstname": parts[0].title() if parts else "",
        "lastname": " ".join(parts[1:]).title() if len(parts) > 1 else "",
        "jobtitle": re.sub(r"[\w.+-]+@[\w.-]+\.\w+", "", paren).strip(" ,-—/") if paren else "",
        "email": email.group(0) if email else "",
        "hiring_role": "recruiter" if re.search(r"recruit|talent|sourcer", paren, re.I) else "",
    }


# ---------------------------------------------------------------- pipeline

def load_rows() -> list[dict]:
    with JobStore() as s, s.conn.cursor() as cur:
        cur.execute(SQL)
        rows = [dict(r) for r in cur.fetchall()]
    rows = [r for r in rows if r["company"].strip()]
    for r in rows:
        r["id"] = str(r["id"])
    return rows


def build(rows: list[dict]):
    G = group_rows(rows)
    deals, contact_only = [], []
    for grp in G.groups():
        for ep in split_episodes(grp):
            c = classify(ep)
            (deals if c else contact_only).append(c or ep)
    people: dict[str, dict] = {}
    for r in rows:
        for p in split_people(r["person"]) if r["person"] else []:
            info = parse_person(p)
            h = people.setdefault(info["key"], {**info, "companies": set(), "spellings": set()})
            for f in ("jobtitle", "email", "hiring_role"):
                h[f] = h[f] or info[f]
            h["companies"].add(r["company"])
            h["spellings"].add(p)
    return G, deals, contact_only, people


def report(G, deals, contact_only, people) -> None:
    print(f"rows: {len(G.rows)}  pursuits: {len(deals)}  contact-only episodes: {len(contact_only)}")
    print("stage:", dict(Counter(x["stage"] for x in deals)))
    print("disposition:", dict(Counter(x["disposition"] for x in deals)))
    print(f"amount set on {sum(1 for x in deals if x['amount'])} deals; source_channel on {sum(1 for x in deals if x['source_channel'])}; resume_sent on {sum(1 for x in deals if x['resume_sent'])}")
    print(f"companies: {len({x['company'] for x in deals} | {e[0]['company'] for e in contact_only})}  contacts: {len(people)}")
    print("\nOPEN deals:")
    for x in sorted((x for x in deals if x["disposition"] == "OPEN"), key=lambda x: x["applied_date"]):
        print(f"  {x['applied_date']} {x['stage']:9s} {x['dealname'][:70]}")
    print("\nrole-title / keyless merges to eyeball:")
    for why, a, b in G.merge_log:
        if why == "same job_id/url":
            continue
        print(f"  [{why}] {a['company']}: {a['date']} {a['action']} {a['role'][:35]!r}  +  {b['date']} {b['action']} {b['role'][:35]!r}")
    print("\ncontacts with >1 spelling:")
    for h in people.values():
        if len(h["spellings"]) > 1:
            print(f"  {h['firstname']} {h['lastname']}: {sorted(h['spellings'])}")


# ---------------------------------------------------------------- hubspot client

class Hub:
    def __init__(self):
        tok = os.environ.get("HUBSPOT_SERVICE_KEY") or subprocess.check_output(
            ["op", "read", "op://Automation/hubspot-jobsearch/credential"], text=True).strip()
        self.c = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {tok}"}, timeout=60)
        self._assoc: dict[tuple[str, str], int] = {}

    def req(self, method: str, path: str, **kw):
        for attempt in range(5):
            r = self.c.request(method, path, **kw)
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 400:
                sys.exit(f"{method} {path} -> {r.status_code}: {r.text[:800]}")
            return r.json() if r.content else {}
        sys.exit("rate-limited five times in a row")

    def assoc_type(self, frm: str, to: str) -> int:
        k = (frm, to)
        if k not in self._assoc:
            labels = self.req("GET", f"/crm/v4/associations/{frm}/{to}/labels")["results"]
            hs = [x for x in labels if x["category"] == "HUBSPOT_DEFINED"]
            primary = [x for x in hs if x.get("label") == "Primary"]
            plain = [x for x in hs if not x.get("label")]
            self._assoc[k] = (primary or plain)[0]["typeId"]
        return self._assoc[k]

    def batch_create(self, obj: str, inputs: list[dict]) -> list[dict]:
        out = []
        for i in range(0, len(inputs), 100):
            out += self.req("POST", f"/crm/v3/objects/{obj}/batch/create", json={"inputs": inputs[i:i + 100]})["results"]
            time.sleep(0.5)
        return out

    def list_ids(self, obj: str) -> list[str]:
        ids, after = [], None
        while True:
            res = self.req("GET", f"/crm/v3/objects/{obj}", params={"limit": 100, **({"after": after} if after else {})})
            ids += [x["id"] for x in res["results"]]
            after = res.get("paging", {}).get("next", {}).get("after")
            if not after:
                return ids

    def batch_archive(self, obj: str, ids: list[str]) -> None:
        for i in range(0, len(ids), 100):
            self.req("POST", f"/crm/v3/objects/{obj}/batch/archive", json={"inputs": [{"id": x} for x in ids[i:i + 100]]})
            time.sleep(0.3)


def assoc(hub: Hub, frm: str, to: str, ids: list[str]) -> list[dict]:
    return [{"to": {"id": i}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": hub.assoc_type(frm, to)}]} for i in ids if i]


def ts(day: str) -> str:
    return f"{day}T19:00:00Z"  # noon Pacific, so the local date matches the log date


# ---------------------------------------------------------------- modes

def do_probe(hub: Hub) -> None:
    props = {"dealname": "PROBE — delete me", "pipeline": "default", "dealstage": STAGE_IDS["Applied"],
             "createdate": ts("2025-06-01"), "applied_date": "2025-06-01", "closedate": ts("2025-07-01")}
    r = hub.req("POST", "/crm/v3/objects/deals", json={"properties": props})
    got = hub.req("GET", f"/crm/v3/objects/deals/{r['id']}", params={"properties": "createdate,applied_date,closedate,hs_v2_date_entered_current_stage"})["properties"]
    print({k: got.get(k) for k in ("createdate", "applied_date", "closedate", "hs_v2_date_entered_current_stage")})
    hub.req("DELETE", f"/crm/v3/objects/deals/{r['id']}")
    print("probe deal deleted")


def do_wipe(hub: Hub) -> None:
    for obj in ("notes", "meetings", "tasks", "deals", "contacts", "companies"):
        ids = hub.list_ids(obj)
        hub.batch_archive(obj, ids)
        print(f"archived {len(ids)} {obj}")


def do_import(hub: Hub, G, deals, contact_only, people, backdate_create: bool) -> None:
    if hub.list_ids("deals"):
        sys.exit("portal already has deals — run `wipe --yes` first (fresh portal only) or import into a clean portal")

    companies = sorted({x["company"] for x in deals} | {e[0]["company"] for e in contact_only})
    slug_of = {r["company"]: r["reg_slug"] for r in G.rows.values() if r["reg_slug"]}
    res = hub.batch_create("companies", [{"properties": {"name": c, "jsb_slug": slug_of.get(c, "")}} for c in companies])
    co_id = {c: r["id"] for c, r in zip(companies, res)}
    print(f"companies: {len(co_id)}")

    keys = list(people)
    res = hub.batch_create("contacts", [{
        "properties": {k: v for k, v in {"firstname": people[p]["firstname"], "lastname": people[p]["lastname"],
                                          "jobtitle": people[p]["jobtitle"], "email": people[p]["email"],
                                          "hiring_role": people[p]["hiring_role"]}.items() if v},
        "associations": assoc(hub, "contacts", "companies", [co_id[c] for c in people[p]["companies"] if c in co_id]),
    } for p in keys])
    person_id = {p: r["id"] for p, r in zip(keys, res)}
    print(f"contacts: {len(person_id)}")

    def contacts_in(rows: list[dict]) -> list[str]:
        ids = []
        for r in rows:
            for p in split_people(r["person"]) if r["person"] else []:
                ids.append(person_id[parse_person(p)["key"]])
        return list(dict.fromkeys(ids))

    inputs = []
    for x in deals:
        props = {"dealname": x["dealname"], "pipeline": "default",
                 "dealstage": STAGE_IDS[x["stage"]] if x["disposition"] == "OPEN" else STAGE_IDS["closedlost"],
                 "applied_date": x["applied_date"], "job_url": x["job_url"], "job_id": x["job_id"],
                 "source_channel": x["source_channel"], "role_type": x["role_type"], "resume_sent": x["resume_sent"]}
        if x["amount"]:
            props["amount"] = str(x["amount"])
        if x["closedate"]:
            props["closedate"] = ts(x["closedate"])
        if x["disposition"] not in ("OPEN",):
            props["closed_lost_reason"] = x["disposition"]
        if backdate_create:
            props["createdate"] = ts(x["rows"][0]["date"])
        inputs.append({"properties": {k: v for k, v in props.items() if v},
                       "associations": assoc(hub, "deals", "companies", [co_id[x["company"]]]) + assoc(hub, "deals", "contacts", contacts_in(x["rows"]))})
    res = hub.batch_create("deals", inputs)
    for x, r in zip(deals, res):
        x["hs_id"] = r["id"]
    print(f"deals: {len(res)}")

    notes, meetings = [], []
    def engagement(r: dict, deal_id: str | None):
        body_bits = [f"[{r['action']}] {r['role']}".strip(), r["notes"]]
        if r["person"]:
            body_bits.append(f"Person: {r['person']}")
        if r["url"]:
            body_bits.append(f"URL: {r['url']}")
        if r["location"]:
            body_bits.append(f"Location: {r['location']}")
        if r["status"]:
            body_bits.append(f"Status: {r['status']}")
        body = "\n".join(b for b in body_bits if b)
        links = assoc(hub, "notes" if r["action"] not in ("Screen", "Interview") else "meetings", "companies", [co_id[r["company"]]])
        links += assoc(hub, "notes" if r["action"] not in ("Screen", "Interview") else "meetings", "contacts", contacts_in([r]))
        if deal_id:
            links += assoc(hub, "notes" if r["action"] not in ("Screen", "Interview") else "meetings", "deals", [deal_id])
        if r["action"] in ("Screen", "Interview"):
            meetings.append({"properties": {"hs_timestamp": ts(r["date"]), "hs_meeting_title": f"{r['action']}: {r['company']} — {r['role']}".strip(" —"),
                                            "hs_meeting_body": body, "hs_meeting_outcome": "COMPLETED"}, "associations": links})
        else:
            notes.append({"properties": {"hs_timestamp": ts(r["date"]), "hs_note_body": body}, "associations": links})
    for x in deals:
        for r in x["rows"]:
            engagement(r, x["hs_id"])
    for ep in contact_only:
        for r in ep:
            engagement(r, None)
    print(f"notes: {len(hub.batch_create('notes', notes))}  meetings: {len(hub.batch_create('meetings', meetings))}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["dry-run", "probe", "import", "wipe"])
    ap.add_argument("--yes", action="store_true", help="required for wipe")
    ap.add_argument("--backdate-create", action="store_true", help="set deal createdate to the first row date (only if probe showed HubSpot honors it)")
    a = ap.parse_args()
    if a.mode == "probe":
        return do_probe(Hub())
    if a.mode == "wipe":
        if not a.yes:
            sys.exit("wipe needs --yes")
        return do_wipe(Hub())
    G, deals, contact_only, people = build(load_rows())
    if a.mode == "dry-run":
        return report(G, deals, contact_only, people)
    do_import(Hub(), G, deals, contact_only, people, a.backdate_create)


if __name__ == "__main__":
    main()
