"""Live board-health probe — does every registered board still answer?

The company registry is the phone book behind `list_company_jobs`: a row with
a stale `ats`/`board` is not a bookkeeping nit, it is a hard error handed to
the human the moment they ask what's open somewhere. Board tokens get renamed,
companies migrate ATSes, and nothing upstream announces it.

The probe calls `fetcher.list_jobs()` — the same call `core.live` makes — so a
board that fails here fails for the human too. Three outcomes, because two
aren't enough:

- ``ok``    — the board answered with jobs.
- ``empty`` — the board answered ``200`` with zero jobs. Silent drift: a
  disabled posting API and a genuinely empty board look identical from the
  status code, and a 404-only check calls both healthy.
- ``error`` — the fetch raised.

There is no run history here on purpose. `sync_status` is last-attempt-wins,
so "failing for 60 days" was never answerable from it; the probe sidesteps the
question by measuring the board right now.
"""

import time
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Literal

import httpx

from jobbuddy.fetchers import SUPPORTED_ATS_TYPES, get_fetcher
from jobbuddy.models import Company

BoardStatus = Literal["ok", "empty", "error"]


@dataclass(frozen=True)
class BoardCheck:
    """One board's live probe result."""

    slug: str
    name: str
    ats: str
    board: str | None
    status: BoardStatus
    total: int | None
    error: str | None
    error_class: str | None
    elapsed: float

    @property
    def failed(self) -> bool:
        """Anything the operator should look at — errors and silent empties."""
        return self.status != "ok"


def classify_error(exc: BaseException) -> str:
    """Bucket an exception by failure shape so drift clusters in the report.

    Buckets: HTTP status codes (``404``/``403``/``500``…), ``timeout``,
    ``parse`` (the ATS returned something that isn't the JSON we expected),
    ``validation`` (the response parsed but a `Job` wouldn't build — our bug,
    not the board's), and ``other``.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return str(exc.response.status_code)
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "timeout"

    message = str(exc)
    # Pydantic raises ValidationError (a ValueError) with this exact preamble.
    # Bucket it apart from `parse`: it means we fetched fine and our own model
    # rejected the payload.
    if "validation error" in message:
        return "validation"
    if isinstance(exc, ValueError) and "Expecting value" in message:
        return "parse"
    if "timed out" in message.lower():
        return "timeout"
    return "other"


def _probe(company: Company) -> BoardCheck:
    started = time.monotonic()
    try:
        with get_fetcher(company) as fetcher:
            jobs = fetcher.list_jobs()
    except Exception as exc:  # noqa: BLE001 — enumerating failures is the job
        return BoardCheck(
            slug=company.slug,
            name=company.name,
            ats=company.ats or "",
            board=company.board,
            status="error",
            total=None,
            error=str(exc).strip() or exc.__class__.__name__,
            error_class=classify_error(exc),
            elapsed=time.monotonic() - started,
        )

    total = len(jobs)
    return BoardCheck(
        slug=company.slug,
        name=company.name,
        ats=company.ats or "",
        board=company.board,
        status="ok" if total else "empty",
        total=total,
        error=None,
        error_class=None,
        elapsed=time.monotonic() - started,
    )


def checkable(companies: Iterable[Company]) -> list[Company]:
    """Companies that actually have a board to probe.

    Directory-only rows (``ats=None``, created by activity-log backfill) and
    rows naming an ATS with no fetcher have nothing to check — reporting them
    as failures would bury the real drift.
    """
    return [c for c in companies if c.ats and c.ats in SUPPORTED_ATS_TYPES]


def check_boards(
    companies: Iterable[Company],
    *,
    workers: int = 8,
) -> Iterator[BoardCheck]:
    """Probe each company's board concurrently, yielding results as they land.

    Yields in completion order, not input order, so a slow Workday board
    doesn't hold up the report. One `BoardCheck` per checkable company; a
    raising fetcher becomes an ``error`` result rather than aborting the sweep.
    """
    targets = checkable(companies)
    if not targets:
        return

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(targets)))) as pool:
        futures = [pool.submit(_probe, c) for c in targets]
        for future in as_completed(futures):
            yield future.result()
