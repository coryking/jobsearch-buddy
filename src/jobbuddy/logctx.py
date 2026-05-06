"""Logging context — Python's analog to log4j's MDC.

Per-task context (company, ats, job_id, url) flows automatically into
every LogRecord while a `with bind(...)` block is active, so log lines
emitted from anywhere in the call tree get tagged without each log call
having to remember to pass the values.

Example:
    with bind(company="disney", ats="talentbrew"):
        with bind(job_id="123", url="https://..."):
            log.warning("parser whiffed")
    # → "[disney/talentbrew] jobbuddy.fetchers.base: parser whiffed (job=123 url=...)"

Implementation: context-local vars + a LogRecord factory that copies
them onto every record. ContextVar is task-local under asyncio and
thread-local under sync code, so concurrent enrich workers each see
their own bindings without interference.
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Iterator
from contextlib import contextmanager

_company = contextvars.ContextVar[str]("jobbuddy_company", default="")
_ats = contextvars.ContextVar[str]("jobbuddy_ats", default="")
_job_id = contextvars.ContextVar[str]("jobbuddy_job_id", default="")
_url = contextvars.ContextVar[str]("jobbuddy_url", default="")

_VARS = {
    "company": _company,
    "ats": _ats,
    "job_id": _job_id,
    "url": _url,
}


@contextmanager
def bind(**kwargs: str | None) -> Iterator[None]:
    """Set context vars for the duration of the block.

    Pass any subset of {company, ats, job_id, url}. None or empty string
    clears that var. Bindings stack — nested `bind(job_id=...)` inside an
    outer `bind(company=...)` keeps the company tag.
    """
    keys = list(kwargs.keys())
    tokens = []
    for key in keys:
        var = _VARS.get(key)
        if var is None:
            raise ValueError(f"bind: unknown context key {key!r}")
        tokens.append(var.set(kwargs[key] or ""))
    try:
        yield
    finally:
        for key, tok in zip(reversed(keys), reversed(tokens)):
            _VARS[key].reset(tok)


def install() -> None:
    """Install the LogRecord factory that injects context vars onto every
    record. Call once at process startup, before any logging happens.

    Sets four record attributes:
      - record.company / record.ats / record.job_id / record.url — raw values
      - record.ctx — bracket prefix like "[disney/talentbrew] " or ""
        (with trailing space when present, so format strings can include it
        unconditionally without producing a leading space when unset).
      - record.suffix — trailing " (job=... url=...)" or "" when both empty.
    """
    base_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = base_factory(*args, **kwargs)
        company = _company.get()
        ats = _ats.get()
        job_id = _job_id.get()
        url = _url.get()
        record.company = company
        record.ats = ats
        record.job_id = job_id
        record.url = url

        head = "/".join(p for p in (company, ats) if p)
        record.ctx = f"[{head}] " if head else ""

        tail_parts = []
        if job_id:
            tail_parts.append(f"job={job_id}")
        if url:
            tail_parts.append(f"url={url}")
        record.suffix = f" ({' '.join(tail_parts)})" if tail_parts else ""

        return record

    logging.setLogRecordFactory(factory)
