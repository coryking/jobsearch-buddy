"""Search and listing commands: search, list-jobs, serve, companies."""

import csv
import io
from typing import Optional

import typer

from jobbuddy.cli import app, console
from jobbuddy.registry import list_companies, lookup_by_name
from jobbuddy.settings import get_settings


@app.command()
def companies():
    """List supported companies and their ATS configurations."""
    registry = list_companies()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Company", "ATS", "Board ID", "Default Output Dir"])
    for slug, company in sorted(registry.items()):
        output_dir = f"job-listings/{slug}/"
        writer.writerow([slug, company.ats, company.board, output_dir])
    print(buf.getvalue(), end="")


@app.command(name="list-jobs")
def list_jobs(
    company: Optional[str] = typer.Argument(None, help="Company name or slug (omit for all cached jobs)"),
    filter: Optional[str] = typer.Option(None, "--filter", "-f", help="Case-insensitive substring filter on job title"),
):
    """List open jobs from cache. Omit company for all cached jobs."""
    from jobbuddy.store import JobStore

    if not get_settings().db_path.exists():
        console.print("[yellow]No cached data. Run 'jsb sync' to populate.[/yellow]")
        raise SystemExit(0)

    store = JobStore()

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
            company=company_slug,
            title=filter,
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
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Title substring filter (comma-separated for OR)"),
    location: Optional[str] = typer.Option(None, "--location", "-l", help="Location substring filter (comma-separated for OR)"),
    company: Optional[str] = typer.Option(None, "--company", "-c", help="Company name or slug"),
):
    """Search cached jobs across all companies."""
    from jobbuddy.store import JobStore

    if not get_settings().db_path.exists():
        console.print("[yellow]No cached data. Run 'jsb sync' to populate.[/yellow]")
        raise SystemExit(0)

    company_slug = None
    if company:
        resolved = lookup_by_name(company)
        if resolved:
            company_slug = resolved.slug
        else:
            company_slug = company  # try raw slug

    store = JobStore()
    try:
        rows = store.query_jobs(
            company=company_slug,
            title=title,
            location=location,
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


@app.command()
def serve(
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
):
    """Start the semantic search web UI."""
    from jobbuddy.web import create_app
    console.print(f"[green]Starting web UI at[/green] http://{host}:{port}")
    create_app().run(host=host, port=port, debug=True)
