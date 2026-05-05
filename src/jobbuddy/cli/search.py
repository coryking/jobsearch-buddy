"""Search and listing commands: search, list-jobs, companies."""

import csv
import io
from typing import Optional

import typer

from jobbuddy.cli import app, console
from jobbuddy.registry import list_companies, lookup_by_name


def parse_since(value: str) -> str:
    """Parse a human-friendly duration into an ISO date string.

    Accepts: 24h, 3d, 1w, 2w, etc. Returns YYYY-MM-DD.
    Raises typer.BadParameter on invalid input.
    """
    from jobbuddy.core import parse_duration_to_date

    try:
        return parse_duration_to_date(value)
    except ValueError as e:
        raise typer.BadParameter(str(e))


@app.command()
def companies():
    """List supported companies and their ATS + bio status."""
    registry = list_companies()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Company", "ATS", "Board ID", "Short Bio"])
    for slug, company in sorted(registry.items()):
        writer.writerow([slug, company.ats, company.board, company.short_bio or ""])
    print(buf.getvalue(), end="")


@app.command(name="companies-add")
def companies_add(
    name: str = typer.Argument(help="Company display name"),
    ats: Optional[str] = typer.Option(None, "--ats", help="ATS platform (greenhouse, ashby, lever, workday, etc.)"),
    board: Optional[str] = typer.Option(None, "--board", help="Board/account slug for the ATS"),
    config: Optional[str] = typer.Option(None, "--config", help="Extra ATS config as raw JSON (e.g. '{\"wd_company\": \"adobe\", \"wd_instance\": 5}')"),
):
    """Register a new company in the database."""
    import json as json_mod

    from jobbuddy.registry import register_company

    extra = {}
    if config:
        try:
            extra = json_mod.loads(config)
        except json_mod.JSONDecodeError as e:
            console.print(f"[red]Invalid JSON in --config: {e}[/red]")
            raise SystemExit(1)

    company = register_company(name, ats=ats, board=board, **extra)
    console.print(f"[green]Registered:[/green] {company.slug} ({company.name}, ats={company.ats}, board={company.board})")


@app.command(name="list-jobs")
def list_jobs(
    company: Optional[str] = typer.Argument(None, help="Company name or slug (omit for all cached jobs)"),
    filter: Optional[str] = typer.Option(None, "--filter", "-f", help="Keyword search across title and description (full-text search with stemming)"),
    since: Optional[str] = typer.Option(None, "--since", "-s", help="Only show jobs posted within this period (e.g. 24h, 3d, 1w, 2w)"),
):
    """List open jobs from cache. Omit company for all cached jobs."""
    from jobbuddy.store import JobStore

    posted_after = parse_since(since) if since else None

    try:
        store = JobStore()
    except Exception:
        console.print("[yellow]Cannot connect to database. Check pg_service.conf.[/yellow]")
        raise SystemExit(1)

    if not store.cache_exists():
        console.print("[yellow]No cached data. Run 'jsb sync' to populate.[/yellow]")
        store.close()
        raise SystemExit(0)

    # Resolve company slug if provided
    company_slug = None
    if company:
        resolved = lookup_by_name(company)
        if not resolved:
            store.close()
            console.print(f"[red]Unknown company: {company}[/red]")
            raise SystemExit(1)
        company_slug = resolved.slug

        # Show cache freshness
        statuses = store.get_sync_status(company_slug)
        if statuses:
            console.print(f"[dim]Cache from: {statuses[0]['last_sync']}[/dim]")

    try:
        rows = store.query_jobs(
            companies=[company_slug] if company_slug else None,
            title=filter,
            posted_after=posted_after,
            limit=10000,
        )
    finally:
        store.close()

    if not rows:
        console.print("[yellow]No jobs found.[/yellow]")
        raise SystemExit(0)

    if company_slug:
        console.print(f"[dim]{company_slug} -- {len(rows)} jobs[/dim]")
    else:
        console.print(f"[dim]{len(rows)} cached jobs[/dim]")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Company", "Title", "Location", "Posted", "Job ID", "Salary", "Team"])
    for r in rows:
        writer.writerow([
            r["company_slug"],
            r["title"],
            r["location"] or "",
            r["published_at"] or "",
            r["job_id"],
            r["salary"] or "",
            r["team"] or r["department"] or "",
        ])
    print(buf.getvalue(), end="")


@app.command()
def search(
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Keyword search across title and description (full-text search with stemming)"),
    location: Optional[str] = typer.Option(None, "--location", "-l", help="Location substring filter (comma-separated for OR)"),
    company: Optional[str] = typer.Option(None, "--company", "-c", help="Company name or slug"),
    exclude: Optional[str] = typer.Option(None, "--exclude", "-x", help="Comma-separated company names/slugs to exclude"),
    since: Optional[str] = typer.Option(None, "--since", "-s", help="Only show jobs posted within this period (e.g. 24h, 3d, 1w, 2w)"),
):
    """Search cached jobs across all companies."""
    from jobbuddy.store import JobStore

    posted_after = parse_since(since) if since else None

    company_slug = None
    if company:
        resolved = lookup_by_name(company)
        if resolved:
            company_slug = resolved.slug
        else:
            company_slug = company  # try raw slug

    exclude_slugs = None
    if exclude:
        from jobbuddy.core import resolve_exclude_companies
        exclude_slugs = resolve_exclude_companies(exclude)

    try:
        store = JobStore()
    except Exception:
        console.print("[yellow]Cannot connect to database. Check pg_service.conf.[/yellow]")
        raise SystemExit(1)
    try:
        rows = store.query_jobs(
            companies=[company_slug] if company_slug else None,
            exclude_companies=exclude_slugs,
            title=title,
            location=location,
            posted_after=posted_after,
            limit=500,
        )
    finally:
        store.close()

    if not rows:
        console.print("[yellow]No jobs found matching filters.[/yellow]")
        raise SystemExit(0)

    console.print(f"[dim]{len(rows)} results[/dim]")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Company", "Title", "Location", "Posted", "Job ID", "Salary", "Team", "URL"])
    for r in rows:
        writer.writerow([
            r["company_slug"],
            r["title"],
            r["location"] or "",
            r["published_at"] or "",
            r["job_id"],
            r["salary"] or "",
            r["team"] or r["department"] or "",
            r["url"] or "",
        ])
    print(buf.getvalue(), end="")


