"""ATS fetcher registry and factory."""

from jobbuddy.fetchers.amazon import AmazonFetcher
from jobbuddy.fetchers.apple import AppleFetcher
from jobbuddy.fetchers.ashby import AshbyFetcher
from jobbuddy.fetchers.avature import AvatureFetcher
from jobbuddy.fetchers.base import ATSFetcher
from jobbuddy.fetchers.eightfold import EightfoldFetcher
from jobbuddy.fetchers.eightfold_v2 import EightfoldV2Fetcher
from jobbuddy.fetchers.greenhouse import GreenhouseFetcher
from jobbuddy.fetchers.jibe import JibeFetcher
from jobbuddy.fetchers.jobsync import JobSyncFetcher
from jobbuddy.fetchers.lever import LeverFetcher
from jobbuddy.fetchers.oracle_hcm import OracleHCMFetcher
from jobbuddy.fetchers.paylocity import PaylocityFetcher
from jobbuddy.fetchers.phenom import PhenomFetcher
from jobbuddy.fetchers.rippling import RipplingFetcher
from jobbuddy.fetchers.smartrecruiters import SmartRecruitersFetcher
from jobbuddy.fetchers.successfactors import SuccessFactorsFetcher
from jobbuddy.fetchers.talentbrew import TalentBrewFetcher
from jobbuddy.fetchers.workable import WorkableFetcher
from jobbuddy.fetchers.workday import WorkdayFetcher
from jobbuddy.models import Company

_REGISTRY: dict[str, type[ATSFetcher]] = {
    "amazon": AmazonFetcher,
    "apple": AppleFetcher,
    "ashby": AshbyFetcher,
    "avature": AvatureFetcher,
    "eightfold": EightfoldFetcher,
    "eightfold_v2": EightfoldV2Fetcher,
    "greenhouse": GreenhouseFetcher,
    "jibe": JibeFetcher,
    "jobsync": JobSyncFetcher,
    "lever": LeverFetcher,
    "oracle_hcm": OracleHCMFetcher,
    "paylocity": PaylocityFetcher,
    "phenom": PhenomFetcher,
    "rippling": RipplingFetcher,
    "smartrecruiters": SmartRecruitersFetcher,
    "successfactors": SuccessFactorsFetcher,
    "talentbrew": TalentBrewFetcher,
    "workable": WorkableFetcher,
    "workday": WorkdayFetcher,
}

SUPPORTED_ATS_TYPES = set(_REGISTRY.keys())


def has_descriptions_in_listing(ats_type: str) -> bool:
    """Check if an ATS type includes descriptions in list_jobs() without instantiating a fetcher."""
    cls = _REGISTRY.get(ats_type)
    if not cls:
        raise ValueError(f"No fetcher for ATS type: {ats_type}")
    return cls.descriptions_in_listing


def create_fetcher(ats_type: str, *, board: str, name: str | None = None, **kw) -> ATSFetcher:
    """Create a fetcher from explicit params. Raises ValueError if ats_type unknown."""
    cls = _REGISTRY.get(ats_type)
    if not cls:
        raise ValueError(f"No fetcher for ATS type: {ats_type}")
    return cls(board, name, **kw)


def get_fetcher(company: Company) -> ATSFetcher:
    """Create a configured fetcher from a Company object.

    Raises ValueError if the company has no ATS config or the ATS type is unsupported.
    """
    if not company.ats:
        raise ValueError(
            f"No job board configured for '{company.slug}'. "
            "This company is in the registry but doesn't have ATS integration."
        )
    extra = {
        k: v
        for k, v in company.model_dump().items()
        if k not in ("slug", "name", "ats", "board", "metadata")
    }
    return create_fetcher(company.ats, board=company.board or "", name=company.name, **extra)
