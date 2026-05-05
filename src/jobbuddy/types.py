"""Work-item DTOs for the sync pipeline.

TypedDicts defining the shape of data flowing between JobStore queries and
phase process_item() methods. These exist so the type checker catches key
mismatches (like a missing 'title') at lint time, not runtime.

Lives at the package top level (not inside sync/) to avoid circular imports
— store.py and sync phases both import from here.
"""

from __future__ import annotations

from typing import Any, TypedDict

# Per-job ATS metadata dict (parsed from JSON ats_metadata column).
JobMeta = dict[str, Any]


class StripWorkItem(TypedDict):
    """One job to strip — returned by JobStore.get_jobs_needing_stripping()."""

    id: int
    company_slug: str
    job_id: str
    title: str
    description: str


class EmbedWorkItem(TypedDict):
    """One job ready to embed — returned by JobStore.list_jobs_needing_embeddings()."""

    id: int
    job_hash: str
    company_hash: str
    company_slug: str
    job_id: str
    title: str
    department: str | None
    location: str | None
    description_stripped: str


EmbedBatch = list[EmbedWorkItem]
"""A batch of jobs to embed in a single API call."""


class EnrichWorkItem(TypedDict):
    """One company's enrichment work — built by EnrichPhase.count_remaining()."""

    slug: str
    job_ids: list[str]
    jobs_meta: dict[str, JobMeta]


class ResearchWorkItem(TypedDict):
    """One company to research — returned by JobStore.get_companies_needing_bio()."""

    slug: str
    name: str
