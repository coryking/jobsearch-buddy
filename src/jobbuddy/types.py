"""Work-item DTOs for the sync pipeline.

TypedDicts defining the shape of data flowing between JobStore queries and
phase process_item() methods. These exist so the type checker catches key
mismatches at lint time, not runtime.

Lives at the package top level (not inside sync/) to avoid circular imports
— store.py and sync phases both import from here.
"""

from __future__ import annotations

from typing import Any, TypedDict

# Per-job ATS metadata dict (parsed from JSON ats_metadata column).
JobMeta = dict[str, Any]


class EnrichWorkItem(TypedDict):
    """One company's enrichment work — built by EnrichPhase.count_remaining()."""

    slug: str
    job_ids: list[str]
    jobs_meta: dict[str, JobMeta]
