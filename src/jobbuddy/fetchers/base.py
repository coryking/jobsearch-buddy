"""Abstract base class for ATS fetchers."""

import logging
from abc import ABC, abstractmethod

from jobbuddy.models import Job

log = logging.getLogger(__name__)


class ATSFetcher(ABC):
    ats_type: str  # set by each subclass

    # Whether list_jobs() returns descriptions. Override to False in fetchers
    # that only return stubs (Workday, Eightfold, Oracle HCM) so the sync
    # pipeline knows to run a post-sync enrichment phase.
    descriptions_in_listing: bool = True

    def __init__(self, board: str, name: str | None = None):
        self.board = board
        self.name = name or board.replace("-", " ").replace("_", " ").title()

    @abstractmethod
    def list_jobs(self) -> list[Job]: ...

    @abstractmethod
    def fetch_job(self, job_id: str) -> Job: ...

    def resolve_name(self) -> str | None:
        """Try to resolve company display name from the ATS. Returns None by default."""
        return None

    def fetch_descriptions(self, job_ids: list[str]) -> dict[str, str | None]:
        """Fetch descriptions for a batch of job IDs.

        Default implementation calls fetch_job() per ID. Returns {job_id: description_or_None}.
        Individual failures return None for that job.
        """
        results: dict[str, str | None] = {}
        for job_id in job_ids:
            try:
                job = self.fetch_job(job_id)
                results[job_id] = job.description
            except Exception as e:
                log.warning("Failed to fetch description for %s: %s", job_id, e)
                results[job_id] = None
        return results
