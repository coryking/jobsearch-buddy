"""Normalized job model and text utilities."""

import csv
import io
import json
import re
from collections import Counter
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from typing import Any, TypeAlias
from uuid import UUID

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
    last_listing_update: PublishedAt = None
    department: str | None = None
    team: str | None = None
    salary: str | None = None
    description: str | None = None
    ats_metadata: dict | None = Field(default=None, exclude=True)

    @field_validator("location", mode="before")
    @classmethod
    def coerce_null_location(cls, v: str | None) -> str:
        # ATSes occasionally emit an explicit "location": null. Fetchers guard
        # the *missing* key with .get("location", "") but that returns None on
        # an explicit null, which would reject here and fail the whole
        # company's sync over one bad job. "" is the no-location representation
        # everywhere downstream, so coerce rather than raise.
        return v or ""


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


class Account(BaseModel):
    """Authenticated identity. One row per (provider, external_id) — keyed
    on the OAuth provider's stable user identifier (`oid` for Entra,
    numeric `sub` for GitHub). Created lazily on the first authenticated
    MCP request that carries a verified token; never via signup form.

    `id` is the FK target on `activity_log.account_id`. The other fields
    are best-effort snapshots refreshed on each call from the token's
    claims — useful for display, never for identity decisions.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    provider: str
    external_id: str
    email: str | None = None
    display_name: str | None = None
    handle: str | None = None


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
    # ATS-side freshness signal where the platform exposes one (greenhouse,
    # amazon, eightfold_v2, jibe, avature). NULL for ATSes whose public API
    # only surfaces a single date — the LLM should treat absence as
    # "freshness unknown," not "stale." Critical for disambiguating
    # year+-old `published_at` values that have actually been touched in
    # the ATS within the last week (see issue #53).
    last_listing_update: PublishedAt = None
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
            last_listing_update=row.get("last_listing_update"),
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
