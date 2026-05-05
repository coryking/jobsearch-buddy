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


class ResearchWorkItem(TypedDict):
    """One company to research — returned by JobStore.get_companies_needing_bio()."""

    slug: str
    name: str


class DistillWorkItem(TypedDict):
    """One job to distill — returned by JobStore.get_jobs_needing_distill().

    Carries everything the distill prompt needs: the JD body plus the
    surrounding context (title, location, company name, structured salary
    flag) used to construct the XML-tagged user message.
    """

    id: int
    company_slug: str
    company_name: str
    job_id: str
    title: str
    location: str | None
    salary: str | None
    description: str
