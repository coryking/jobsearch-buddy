"""Normalized job model and text utilities."""

import csv
import io
import json
import re
from collections import Counter
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ats_metadata keys worth surfacing to LLM consumers (MCP output).
# Other keys (e.g. Workday's ext_path) are internal plumbing.
_MCP_METADATA_KEYS = {"displayJobId", "workLocationOption", "efcustomTextWorkSite", "efcustomTextRoletype", "efcustomTextEmploymentType"}


PublishedAt: TypeAlias = date | None
"""When a job was first posted. Date-only by design — see Deferred Smell #5
(fetchers truncate timestamps to date)."""


def parse_published_at(value: Any) -> PublishedAt:
    """Coerce ATS-supplied date-ish values into PublishedAt.

    Accepts: None, date, datetime, ISO-8601 string ("2026-03-04",
    "2026-03-04T18:30:00Z"), or epoch seconds (int/float). Anything
    unparseable returns None — fetchers should never blow up on a
    malformed PostedDate.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).date()
        except (ValueError, OSError, OverflowError):
            return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _filter_metadata(raw: dict | str | None) -> dict[str, Any] | None:
    """Parse and whitelist ats_metadata for MCP output."""
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    if not isinstance(raw, dict):
        return None
    filtered = {k: v for k, v in raw.items() if k in _MCP_METADATA_KEYS}
    return filtered or None


class Job(BaseModel):
    id: str
    title: str
    location: str
    url: str
    apply_url: str
    published_at: PublishedAt = None
    department: str | None = None
    team: str | None = None
    salary: str | None = None
    description: str | None = None
    ats_metadata: dict | None = Field(default=None, exclude=True)


class Company(BaseModel):
    """A company in the registry. Slug is normalized at construction time."""

    model_config = ConfigDict(extra="allow")

    slug: str
    name: str
    ats: str | None = None
    board: str | None = None
    short_bio: str | None = None
    long_bio: str | None = None

    @field_validator("slug", mode="before")
    @classmethod
    def normalize_slug(cls, v: str) -> str:
        return slugify(v)


class FetchResult(BaseModel):
    company: Company
    job: Job


# ---------------------------------------------------------------------------
# MCP response models — compact header+rows format to minimize token usage
# ---------------------------------------------------------------------------

JobRow = list[Any]


class JobSearchResults(BaseModel):
    """Job listings. `jobs` is a header+rows table (first element is column names)."""

    count: int
    company: str | None = None

    jobs: list[JobRow]

    @classmethod
    def from_query(
        cls,
        rows: list[dict],
        log_entries: list[dict],
        *,
        company_slug: str | None = None,
    ) -> "JobSearchResults":
        log_by_job_id: dict[str, list[dict]] = {}
        log_by_company: dict[str, list[dict]] = {}
        for entry in log_entries:
            if entry.get("job_id"):
                log_by_job_id.setdefault(entry["job_id"], []).append(entry)
            co = entry.get("company", "").lower()
            if co:
                log_by_company.setdefault(co, []).append(entry)

        has_metadata = any(_filter_metadata(row.get("ats_metadata")) for row in rows)
        headers = ["company", "title", "location", "posted", "job_id", "url",
                   "salary", "team", "short_jd", "applied"]
        if has_metadata:
            headers.append("metadata")
        job_list: list[JobRow] = [headers]

        for row in rows:
            company_name = row.get("company_name") or row["company_slug"]

            matched = log_by_job_id.get(row["job_id"], [])
            if not matched:
                co_entries = log_by_company.get(company_name.lower(), [])
                matched = [e for e in co_entries if e.get("role", "").lower() == row["title"].lower()]

            applied = (
                ", ".join(f"{m.get('date', '')} {m.get('action', '')}" for m in matched)
                if matched
                else ""
            )

            entry = [
                company_name,
                row["title"],
                row["location"] or "",
                row["published_at"] or "",
                row["job_id"],
                row["url"] or "",
                row["salary"] or "",
                row["team"] or row["department"] or "",
                row.get("short_jd") or "",
                applied,
            ]
            if has_metadata:
                meta = _filter_metadata(row.get("ats_metadata"))
                entry.append(json.dumps(meta) if meta else "")
            job_list.append(entry)

        return cls(count=len(rows), company=company_slug, jobs=job_list)

    def to_mcp_result(self) -> str:
        prefix = f"{self.count} jobs"
        if self.company:
            prefix += f" at {self.company}"
        return prefix + "\n" + _to_csv(self.jobs[0], self.jobs[1:])


def _to_csv(headers: list[str], rows: list[list[Any]]) -> str:
    """Render header+rows as properly escaped CSV."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue()


class ActivitySummary(BaseModel):
    """All-company activity summary. `rows` is header+rows table."""

    count: int
    rows: list[list[Any]]

    @classmethod
    def from_log(cls, by_company: dict[str, list[dict]]) -> "ActivitySummary":
        headers = ["company", "total", "last", "first", "actions", "people", "status"]
        summaries = []
        for name, co_rows in by_company.items():
            actions = Counter(r.get("action", "") for r in co_rows if r.get("action"))
            dates = [r.get("date", "") for r in co_rows if r.get("date")]
            people = sorted({r.get("person", "") for r in co_rows if r.get("person", "").strip()})
            statuses = [r.get("status", "") for r in co_rows if r.get("status", "").strip()]
            summaries.append((
                max(dates) if dates else "",
                [
                    name,
                    len(co_rows),
                    max(dates) if dates else "",
                    min(dates) if dates else "",
                    " ".join(f"{a}:{c}" for a, c in actions.items()),
                    ", ".join(people),
                    statuses[-1] if statuses else "",
                ],
            ))
        summaries.sort(key=lambda s: s[0], reverse=True)
        data_rows = [s[1] for s in summaries]
        return cls(count=len(by_company), rows=[headers] + data_rows)

    def to_mcp_result(self) -> str:
        return f"{self.count} companies\n" + _to_csv(self.rows[0], self.rows[1:])


class ActivityDetail(BaseModel):
    """Single-company activity detail. `rows` is header+rows table."""

    company: str
    total: int
    actions: str
    rows: list[list[Any]]

    @classmethod
    def from_company(cls, name: str, co_rows: list[dict]) -> "ActivityDetail":
        actions = Counter(r.get("action", "") for r in co_rows if r.get("action"))
        headers = ["date", "role", "action", "job_id", "person", "location", "status", "url", "notes"]
        data_rows = [[r.get(h, "") for h in headers] for r in co_rows]
        return cls(
            company=name,
            total=len(co_rows),
            actions=" ".join(f"{a}:{c}" for a, c in actions.items()),
            rows=[headers] + data_rows,
        )

    def to_mcp_result(self) -> str:
        return f"{self.company} | {self.total} activities | {self.actions}\n" + _to_csv(self.rows[0], self.rows[1:])


class CompactJob(BaseModel):
    """Job posting details for LLM consumption. Null fields omitted on serialization."""

    title: str
    company: str
    location: str
    url: str
    apply_url: str | None = None
    id: str
    published_at: PublishedAt = None
    department: str | None = None
    team: str | None = None
    salary: str | None = None
    description: str | None = None
    metadata: dict | None = None
    # True when `description` is the distill-phase output. False when it's
    # the raw fetcher payload. Lets the calling LLM decide whether to trust
    # the description as fact-dense or treat it as raw boilerplate.
    distilled: bool = False

    def model_dump(self, **kwargs) -> dict:
        kwargs.setdefault("exclude_none", True)
        return super().model_dump(**kwargs)

    @classmethod
    def from_result(cls, result: FetchResult) -> "CompactJob":
        meta = _filter_metadata(result.job.ats_metadata)
        return cls(
            company=result.company.name, metadata=meta, distilled=False,
            **result.job.model_dump(),
        )

    @classmethod
    def from_db_row(cls, row: dict, company_name: str) -> "CompactJob":
        """Build from a JobStore row dict.

        Prefers the distill-phase output (description_normalized); falls back
        to the raw description for jobs that haven't been distilled yet.
        """
        meta = _filter_metadata(row.get("ats_metadata"))
        normalized = row.get("description_normalized")
        desc = normalized or row.get("description")
        return cls(
            title=row["title"],
            company=company_name,
            location=row["location"],
            url=row["url"],
            apply_url=row.get("apply_url"),
            id=row["job_id"],
            published_at=row.get("published_at"),
            department=row.get("department"),
            team=row.get("team"),
            salary=row.get("salary"),
            description=desc,
            metadata=meta,
            distilled=normalized is not None,
        )


class _HTMLStripper(HTMLParser):
    _BLOCK_TAGS = frozenset(
        "p div br hr li tr dt dd h1 h2 h3 h4 h5 h6 blockquote pre ol ul table".split()
    )

    def __init__(self):
        super().__init__()
        self._pieces: list[str] = []

    def handle_starttag(self, tag: str, attrs: list):
        if tag in self._BLOCK_TAGS:
            self._pieces.append("\n")

    def handle_endtag(self, tag: str):
        if tag in self._BLOCK_TAGS:
            self._pieces.append("\n")

    def handle_data(self, data: str):
        self._pieces.append(data)

    def get_text(self) -> str:
        import re as _re
        text = "".join(self._pieces)
        text = _re.sub(r"\n{3,}", "\n\n", text)
        return text


def strip_html(html: str) -> str:
    import html as html_mod

    html = html_mod.unescape(html)
    s = _HTMLStripper()
    s.feed(html)
    return s.get_text().strip()


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")
