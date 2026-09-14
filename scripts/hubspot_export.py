"""Load the HubSpot migration company files into HubSpot.

The files (one per company, schema in the resume repo's data/hubspot-migration/SCHEMA.md) are the
structured reading of jsb's old activity_log. This loader is deterministic: companies, contacts,
deals, notes and meetings are created through the batch APIs, associated, and stamped with their
provenance (file path and source row ids) in HubSpot's record-source detail fields.

    uv run python scripts/hubspot_export.py dry-run <companies-dir>
    uv run python scripts/hubspot_export.py load <companies-dir> [--skip-existing]
    uv run python scripts/hubspot_export.py wipe --yes        # fresh-portal re-runs only
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE = "https://api.hubapi.com"
STAGE = {"applied": "4305280713", "screen": "4305280714", "interview": "4305280715", "offer": "4305280716",
         "accepted": "4305280717", "closed_lost": "closedlost"}
LOST_REASON = {"rejected": "Rejected", "withdrawn": "Withdrawn", "req_closed": "Req closed", "no_response": "No response"}
WRITER = "hubspot_export.py"


def ts(day: str) -> str:
    return f"{day}T19:00:00Z"  # noon Pacific, so the local date matches the file's date


def split_name(name: str) -> tuple[str, str]:
    parts = name.split()
    return (parts[0], " ".join(parts[1:])) if len(parts) > 1 else (name, "")


def lifecycle(person: dict, pursuits: list[dict]) -> str:
    mine = [p for p in pursuits if person["id"] in p["people"]]
    if any(p["status"] == "accepted" for p in mine):
        return "customer"
    if person["hiring_role"] == "referrer":
        return "evangelist"
    return "opportunity" if mine else "lead"


def midpoint(comp: dict | None) -> str | None:
    if not comp or comp.get("low") is None or comp.get("high") is None:
        return None
    return str(round((comp["low"] + comp["high"]) / 2))


FREEMAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "me.com", "live.com", "aol.com", "proton.me", "protonmail.com"}
MAILERS = ("myworkday.com", "greenhouse", "lever.co", "ashbyhq", "smartrecruiters", "icims", "linkedin.com", "jobvite", "workablemail", "hire.")


def company_domain(d: dict) -> str | None:
    """The domain of a work email belonging to someone employed at this company (org null)."""
    for p in d["people"]:
        if p["email"] and not p["org"]:
            dom = p["email"].split("@")[-1].lower()
            if dom not in FREEMAIL and not any(m in dom for m in MAILERS):
                return dom
    return None


def provenance(file: Path, rows: list[int] | None = None, evidence: list[str] | None = None) -> dict:
    d3 = ", ".join([f"jsb #{r}" for r in (rows or [])] + list(evidence or []))
    return {"hs_object_source_detail_2": f"{WRITER} {file.name}", **({"hs_object_source_detail_3": d3[:255]} if d3 else {})}


class Hub:
    def __init__(self):
        tok = os.environ.get("HUBSPOT_SERVICE_KEY") or subprocess.check_output(
            ["op", "read", "op://Automation/hubspot-jobsearch/credential"], text=True).strip()
        self.c = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {tok}"}, timeout=60)
        self._assoc: dict[tuple[str, str], int] = {}
        self.stamp = True

    def req(self, method: str, path: str, **kw):
        for attempt in range(6):
            r = self.c.request(method, path, **kw)
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 400:
                sys.exit(f"{method} {path} -> {r.status_code}: {r.text[:1000]}")
            return r.json() if r.content else {}
        sys.exit("rate-limited six times in a row")

    def assoc_type(self, frm: str, to: str) -> int:
        k = (frm, to)
        if k not in self._assoc:
            labels = self.req("GET", f"/crm/v4/associations/{frm}/{to}/labels")["results"]
            hs = [x for x in labels if x["category"] == "HUBSPOT_DEFINED"]
            primary = [x for x in hs if x.get("label") == "Primary"]
            plain = [x for x in hs if not x.get("label")]
            self._assoc[k] = (primary or plain)[0]["typeId"]
        return self._assoc[k]

    def link(self, frm: str, to: str, ids: list[str]) -> list[dict]:
        return [{"to": {"id": i}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": self.assoc_type(frm, to)}]} for i in ids if i]

    def create_each(self, obj: str, inputs: list[dict]) -> list[dict]:
        """One POST per record, so the returned ids line up with `inputs`. Batch create returns
        results in arbitrary order, which scrambled every id map built by zip() in v1."""
        out = []
        for x in inputs:
            if not self.stamp:
                x["properties"].pop("hs_object_source_detail_2", None)
                x["properties"].pop("hs_object_source_detail_3", None)
            out.append(self.req("POST", f"/crm/v3/objects/{obj}", json=x))
            time.sleep(0.12)
        return out

    def batch_create(self, obj: str, inputs: list[dict]) -> list[dict]:
        """Order of results is NOT the order of inputs; only use when ids are not needed."""
        out = []
        for i in range(0, len(inputs), 100):
            chunk = inputs[i:i + 100]
            if not self.stamp:
                for x in chunk:
                    x["properties"].pop("hs_object_source_detail_2", None)
                    x["properties"].pop("hs_object_source_detail_3", None)
            out += self.req("POST", f"/crm/v3/objects/{obj}/batch/create", json={"inputs": chunk})["results"]
            time.sleep(0.4)
        return out

    def probe_stamping(self) -> None:
        r = self.c.post("/crm/v3/objects/companies", json={"properties": {"name": "PROBE delete me", "hs_object_source_detail_2": "probe"}})
        if r.status_code >= 400:
            self.stamp = False
            print("record-source stamping not accepted by the API; loading without it:", r.text[:200])
            return
        self.req("DELETE", f"/crm/v3/objects/companies/{r.json()['id']}")

    def list_ids(self, obj: str, props: list[str] | None = None) -> list[dict]:
        out, after = [], None
        while True:
            res = self.req("GET", f"/crm/v3/objects/{obj}", params={"limit": 100, **({"properties": ",".join(props)} if props else {}), **({"after": after} if after else {})})
            out += res["results"]
            after = res.get("paging", {}).get("next", {}).get("after")
            if not after:
                return out

    def batch_archive(self, obj: str, ids: list[str]) -> None:
        for i in range(0, len(ids), 100):
            self.req("POST", f"/crm/v3/objects/{obj}/batch/archive", json={"inputs": [{"id": x} for x in ids[i:i + 100]]})
            time.sleep(0.3)


FACT_PROPS = ("domain", "city", "state", "country", "industry", "linkedin_company_page", "description", "website", "founded_year", "numberofemployees", "type")


def load_file(hub: Hub, file: Path, facts: dict | None = None) -> dict:
    d = json.loads(file.read_text())
    co = d["company"]
    extra = {k: v for k, v in (facts or {}).get(file.name, {}).items() if k in FACT_PROPS and v}
    props = {"name": co["name"], "jsb_slug": co.get("jsb_slug") or "", "domain": company_domain(d), **provenance(file), **extra}
    company = hub.create_each("companies", [{"properties": {k: v for k, v in props.items() if v}}])[0]
    co_id = company["id"]

    people = d["people"]
    contact_inputs = []
    for p in people:
        first, last = split_name(p["name"])
        props = {"firstname": first, "lastname": last, "jobtitle": p["title"], "email": p["email"], "hs_linkedin_url": p["linkedin_url"],
                 "company": p["org"] or co["name"], "hiring_role": p["hiring_role"], "hs_lead_status": p.get("lead_status"),
                 "lifecyclestage": lifecycle(p, d["pursuits"]), **provenance(file, p["source_rows"], p.get("evidence"))}
        contact_inputs.append({"properties": {k: v for k, v in props.items() if v}, "associations": hub.link("contacts", "companies", [co_id])})
    contacts = hub.create_each("contacts", contact_inputs)
    pid = {p["id"]: c["id"] for p, c in zip(people, contacts)}

    deal_inputs = []
    for u in d["pursuits"]:
        title = u["title"] or "(role not named)"
        stage = STAGE[u["stage"]] if u["status"] == "open" else STAGE["accepted"] if u["status"] == "accepted" else STAGE["closed_lost"]
        first_date = u["applied_date"] or min((e["date"] for e in d["events"] if e["pursuit"] == u["id"]), default=None)
        props = {"dealname": f"{co['name']} — {title}", "pipeline": "default", "dealstage": stage,
                 "applied_date": u["applied_date"], "closedate": ts(u["close_date"]) if u["close_date"] else None,
                 "closed_lost_reason": LOST_REASON.get(u["closed_lost_reason"] or "", None),
                 "amount": midpoint(u["comp_posted"]), "job_url": u["job_url"], "job_id": u["job_id"],
                 "source_channel": u["source_channel"], "role_type": u["role_type"], "resume_sent": u["resume_sent"],
                 "createdate": ts(first_date) if first_date else None,
                 "description": f"[{u['confidence']}] {u['rationale']}"[:2000],
                 **provenance(file, u["source_rows"], u.get("evidence"))}
        deal_inputs.append({"properties": {k: v for k, v in props.items() if v},
                            "associations": hub.link("deals", "companies", [co_id]) + hub.link("deals", "contacts", [pid[x] for x in u["people"]])})
    deals = hub.create_each("deals", deal_inputs)
    did = {u["id"]: dl["id"] for u, dl in zip(d["pursuits"], deals)}

    notes, meetings = [], []
    for e in d["events"]:
        obj = "meetings" if e["kind"] == "meeting" else "notes"
        links = hub.link(obj, "companies", [co_id]) + hub.link(obj, "contacts", [pid[x] for x in e["people"]])
        if e["pursuit"]:
            links += hub.link(obj, "deals", [did[e["pursuit"]]])
        prov = provenance(file, [e["source_row"]] if e["source_row"] is not None else None, e.get("evidence"))
        if obj == "meetings":
            meetings.append({"properties": {"hs_timestamp": ts(e["date"]), "hs_meeting_title": e["title"] or f"Meeting — {co['name']}",
                                            "hs_meeting_body": e["body"], "hs_meeting_outcome": "COMPLETED", **prov}, "associations": links})
        else:
            notes.append({"properties": {"hs_timestamp": ts(e["date"]), "hs_note_body": (f"{e['title']}\n\n" if e["title"] else "") + e["body"], **prov}, "associations": links})
    # standing relationship context becomes a pinned note on the contact
    pinned = []
    for p in people:
        if p.get("relationship"):
            pinned.append((p["id"], {"properties": {"hs_timestamp": ts(d["events"][-1]["date"]) if d["events"] else ts("2026-09-14"),
                                                    "hs_note_body": f"Relationship: {p['relationship']}", **provenance(file, p["source_rows"], p.get("evidence"))},
                                     "associations": hub.link("notes", "contacts", [pid[p["id"]]]) + hub.link("notes", "companies", [co_id])}))
    hub.batch_create("notes", notes) if notes else []
    made_meetings = hub.batch_create("meetings", meetings) if meetings else []
    for (person_id, _), note in zip(pinned, hub.create_each("notes", [x for _, x in pinned])):
        hub.req("PATCH", f"/crm/v3/objects/contacts/{pid[person_id]}", json={"properties": {"hs_pinned_engagement_id": note["id"]}})
    return {"company": co["name"], "contacts": len(contacts), "deals": len(deals), "notes": len(notes), "meetings": len(made_meetings), "pinned": len(pinned)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["dry-run", "load", "wipe"])
    ap.add_argument("companies_dir", nargs="?")
    ap.add_argument("--skip-existing", action="store_true", help="skip files whose company name already exists in the portal")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--exclude", action="append", default=[], help="file name to skip (repeatable)")
    ap.add_argument("--facts", help="JSON map of company file name -> company properties (domain, city, industry, ...) to set at creation")
    a = ap.parse_args()
    if a.mode == "wipe":
        if not a.yes:
            sys.exit("wipe needs --yes")
        hub = Hub()
        for obj in ("notes", "meetings", "tasks", "deals", "contacts", "companies"):
            ids = [x["id"] for x in hub.list_ids(obj)]
            hub.batch_archive(obj, ids)
            print(f"archived {len(ids)} {obj}")
        return
    files = [f for f in sorted(Path(a.companies_dir).glob("*.json")) if f.name not in a.exclude]
    if a.mode == "dry-run":
        tot = {"companies": len(files), "people": 0, "pursuits": 0, "events": 0, "pinned": 0}
        for f in files:
            d = json.loads(f.read_text())
            tot["people"] += len(d["people"]); tot["pursuits"] += len(d["pursuits"]); tot["events"] += len(d["events"])
            tot["pinned"] += sum(1 for p in d["people"] if p.get("relationship"))
        print(tot)
        return
    hub = Hub()
    hub.probe_stamping()
    facts = json.loads(Path(a.facts).read_text()) if a.facts else {}
    existing = {x["properties"]["name"] for x in hub.list_ids("companies", ["name", "hs_object_source_detail_2"])
                if (x["properties"].get("hs_object_source_detail_2") or "").startswith(WRITER)} if a.skip_existing else set()
    done = 0
    for f in files:
        name = json.loads(f.read_text())["company"]["name"]
        if name in existing:
            continue
        r = load_file(hub, f, facts)
        done += 1
        print(f"{r['company']:40s} contacts={r['contacts']} deals={r['deals']} notes={r['notes']} meetings={r['meetings']} pinned={r['pinned']}")
    print(f"loaded {done} companies ({len(files) - done} skipped)")


if __name__ == "__main__":
    main()
