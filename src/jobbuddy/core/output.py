"""Markdown rendering and disk persistence for fetched jobs."""

from datetime import date
from pathlib import Path
from typing import Any

from jobbuddy.models import Company, FetchResult, Job, slugify


def job_to_markdown(job: Job) -> str:
    """Render a Job as a markdown string."""
    return f"""# {job.title}

## Essentials
- **Job ID:** {job.id}
- **URL:** {job.url}
- **Apply:** {job.apply_url}
- **Location:** {job.location or "Not specified"}
- **Salary:** {job.salary or "Not specified"}
- **Team:** {job.team or job.department or "Not specified"}
- **Posted:** {job.published_at or "Unknown"}

## Description
{job.description or "No description available."}
"""


def save_job_listing(company: Company, job: Job) -> Path:
    """Save a job listing as markdown. Returns the file path."""
    from jobbuddy.settings import get_settings
    output_dir = get_settings().listings_dir / company.slug
    output_dir.mkdir(parents=True, exist_ok=True)

    pub_date = (job.published_at or date.today()).isoformat()
    filename = f"{pub_date}_{slugify(job.title)}_{job.id}.md"
    filepath = output_dir / filename
    filepath.write_text(job_to_markdown(job))
    return filepath


def result_to_dict(result: FetchResult) -> dict[str, Any]:
    """Convert a FetchResult to a JSON-serializable dict with company metadata."""
    output = result.job.model_dump()
    output["_company_slug"] = result.company.slug
    output["_company_name"] = result.company.name
    output["_ats"] = result.company.ats
    return output
