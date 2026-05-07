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
    company: Optional[str] = typer.Argument(None, help="Company name or slug (omit to list across all companies)"),
    filter: Optional[str] = typer.Option(None, "--filter", "-f", help="Keyword search across title and description (full-text search with stemming)"),
    since: Optional[str] = typer.Option(None, "--since", "-s", help="Only show jobs posted within this period (e.g. 24h, 3d, 1w, 2w)"),
):
    """List open jobs. Omit company to list across all companies."""
    from jobbuddy.store import JobStore

    posted_after = parse_since(since) if since else None

    try:
        store = JobStore()
    except Exception:
        console.print("[yellow]Cannot connect to database. Check pg_service.conf.[/yellow]")
        raise SystemExit(1)

    if not store.has_any_jobs():
        console.print("[yellow]No jobs in the database. Run 'jsb sync' to populate.[/yellow]")
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

        statuses = store.get_sync_status(company_slug)
        if statuses:
            console.print(f"[dim]Last synced: {statuses[0]['last_sync']}[/dim]")

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
        console.print(f"[dim]{len(rows)} jobs[/dim]")

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


@app.command(name="find-companies")
def find_companies_cmd(
    query: str = typer.Argument(..., help="Vibe / theme query (e.g. 'companies that ship AI as product')"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max rows (default 20, cap 100)"),
):
    """Vector-search companies by long_bio. Mirrors the `find_companies` MCP tool."""
    from jobbuddy.core import find_companies

    try:
        result = find_companies(query, limit=min(limit, 100))
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    if result["coverage_hint"]:
        console.print(f"[yellow]{result['coverage_hint']}[/yellow]")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Active", "Slug", "Name", "Short Bio"])
    for r in result["results"]:
        writer.writerow([
            r["active_jobs"],
            r["slug"],
            r["name"],
            (r["short_bio"] or "").replace("\n", " "),
        ])
    print(buf.getvalue(), end="")


@app.command()
def search(
    query: Optional[str] = typer.Option(None, "--query", "-q", help="Postgres FTS query over title, short_jd, description_normalized, location, department"),
    location: Optional[str] = typer.Option(None, "--location", "-l", help="Location substring filter (comma-separated for OR)"),
    company: Optional[str] = typer.Option(None, "--company", "-c", help="Company name or slug"),
    exclude: Optional[str] = typer.Option(None, "--exclude", "-x", help="Comma-separated company names/slugs to exclude"),
    posted_since: Optional[str] = typer.Option(None, "--posted-since", "-s", help="Only show jobs posted within this period (e.g. 24h, 3d, 1w, 2w)"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max rows to return"),
):
    """Search jobs across all companies (CLI parity with the search_jobs MCP tool)."""
    from jobbuddy.core import search_jobs

    exclude_list = None
    if exclude:
        exclude_list = [s.strip() for s in exclude.split(",") if s.strip()]

    try:
        rows = search_jobs(
            query=query or "",
            companies=[company] if company else None,
            exclude_companies=exclude_list,
            location=location or "",
            posted_since=posted_since or "",
            limit=limit,
        )
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)
    except Exception:
        console.print("[yellow]Cannot connect to database. Check pg_service.conf.[/yellow]")
        raise SystemExit(1)

    if not rows:
        console.print("[yellow]No jobs found matching filters.[/yellow]")
        raise SystemExit(0)

    console.print(f"[dim]{len(rows)} results[/dim]")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Company", "Title", "Location", "Posted", "Job ID", "Salary", "Team", "Short JD", "URL"])
    for r in rows:
        writer.writerow([
            r["company_slug"],
            r["title"],
            r["location"] or "",
            r["published_at"] or "",
            r["job_id"],
            r["salary"] or "",
            r["team"] or r["department"] or "",
            (r.get("short_jd") or "").replace("\n", " "),
            r["url"] or "",
        ])
    print(buf.getvalue(), end="")


