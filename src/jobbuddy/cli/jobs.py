"""Job operations commands: save, lookup."""

import json
import re
from pathlib import Path

import typer

from jobbuddy.cli import app, console
from jobbuddy.core import (
    fetch_from_url,
    job_to_markdown,
    result_to_dict,
    save_job_listing,
)
from jobbuddy.fetchers import get_fetcher
from jobbuddy.models import Company, slugify
from jobbuddy.registry import list_companies, lookup_by_name


def _resolve_company(name: str) -> Company:
    """Resolve a company name/slug to a Company. Exits on failure."""
    company = lookup_by_name(name)
    if not company:
        companies = list_companies()
        console.print(f"[red]Unknown company: {name}[/red]")
        console.print(f"Available: {', '.join(companies.keys())}")
        raise SystemExit(1)
    return company


@app.command()
def save(
    company: str = typer.Argument(help="Company name or slug"),
    job_ids: list[str] = typer.Argument(help="One or more job IDs to save"),
    output_dir: Path | None = typer.Option(None, "--output-dir", "-o", help="Output directory"),
):
    """Fetch full job descriptions and save as markdown."""
    resolved = _resolve_company(company)

    try:
        fetcher = get_fetcher(resolved)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    for job_id in job_ids:
        with console.status(f"Fetching {job_id}..."):
            job = fetcher.fetch_job(job_id)

        if output_dir:
            from datetime import date

            out = output_dir.resolve()
            out.mkdir(parents=True, exist_ok=True)
            pub_date = job.published_at[:10] if job.published_at and re.match(r"\d{4}-\d{2}-\d{2}", job.published_at) else date.today().isoformat()
            filename = f"{pub_date}_{slugify(job.title)}_{job.id}.md"
            filepath = out / filename
            filepath.write_text(job_to_markdown(job))
        else:
            filepath = save_job_listing(resolved, job)

        console.print(f"[green]Saved:[/green] {filepath}")


@app.command()
def lookup(
    url: str = typer.Argument(help="Job listing URL"),
):
    """Parse a job URL, fetch details, and print as JSON."""
    try:
        result = fetch_from_url(url)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    output = result_to_dict(result)
    print(json.dumps(output, indent=2))
