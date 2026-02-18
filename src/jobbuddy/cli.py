"""CLI for fetching job listings from ATS job boards."""

import csv
import io
import json
import queue
import re
import threading
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from jobbuddy.core import (
    fetch_from_url,
    job_to_markdown,
    result_to_dict,
    save_job_listing,
)
from jobbuddy.fetchers import get_fetcher
from jobbuddy.models import Company, slugify
from jobbuddy.registry import list_companies, lookup_by_name
from jobbuddy.settings import get_settings

app = typer.Typer(help="Fetch job listings from ATS job boards.")
console = Console(stderr=True)


def _resolve_company(name: str) -> Company:
    """Resolve a company name/slug to a Company. Exits on failure."""
    company = lookup_by_name(name)
    if not company:
        companies = list_companies()
        console.print(f"[red]Unknown company: {name}[/red]")
        console.print(f"Available: {', '.join(companies.keys())}")
        raise SystemExit(1)
    return company


# ---------------------------------------------------------------------------
# Event consumer — replaces 16 callback closures
# ---------------------------------------------------------------------------


def _make_phase_progress(con: Console):
    """Create a fresh Rich Progress bar for a phase."""
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
    return Progress(
        SpinnerColumn("dots"),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        MofNCompleteColumn(),
        TextColumn("[dim]|[/dim]"),
        TimeElapsedColumn(),
        console=con,
        expand=False,
    )


def _consume_events(
    events: queue.SimpleQueue,
    con: Console,
    overall_progress,
    overall_task,
):
    """Drain the event queue on a daemon thread. Handles all sync events."""
    from jobbuddy.sync.types import (
        CompanySkipped,
        Done,
        FetchProgress,
        FetchResult,
        FetchStarted,
        ModelLoaded,
        ModelUnloaded,
        Phase,
        PhaseDone,
        PhaseError,
        PhaseProgress,
        PhaseStarted,
        RetryEvent,
    )

    active_slugs: dict[str, int] = {}  # slug -> progress task_id
    lock = threading.Lock()

    # Per-phase progress tracking
    phase_progress = None
    phase_task_id = None
    phase_last_completed: dict[Phase, int] = {}

    while True:
        event = events.get()

        match event:
            case Done():
                break

            case FetchStarted(slug=slug):
                with lock:
                    task_id = overall_progress.add_task(f"  [cyan]{slug}[/cyan]", total=None)
                    active_slugs[slug] = task_id

            case FetchProgress(slug=slug, fetched=fetched, total=total):
                with lock:
                    task_id = active_slugs.get(slug)
                    if task_id is not None:
                        overall_progress.update(task_id, total=total, completed=fetched)

            case FetchResult(result=sr):
                with lock:
                    if sr.slug in active_slugs:
                        overall_progress.remove_task(active_slugs.pop(sr.slug))
                    overall_progress.advance(overall_task)

                if sr.ok:
                    overall_progress.console.print(
                        f"  [green]✓[/green] {sr.slug:<24} [bold]{sr.job_count:>4}[/bold] jobs  [dim]{sr.elapsed:>5.1f}s[/dim]"
                    )
                else:
                    short_err = (sr.error or "unknown")[:55]
                    overall_progress.console.print(
                        f"  [red]✗[/red] {sr.slug:<24} [red]{short_err}[/red]  [dim]{sr.elapsed:>5.1f}s[/dim]"
                    )

            case CompanySkipped(slug=slug, reason=reason):
                with lock:
                    overall_progress.advance(overall_task)
                overall_progress.console.print(f"  [dim]↷ {slug:<24} skipped ({reason})[/dim]")

            case PhaseStarted(phase=phase, total=total, detail=detail):
                # Stop fetch progress bar when entering a new phase
                if phase != Phase.FETCH:
                    overall_progress.stop()

                if phase_progress is not None:
                    phase_progress.stop()

                phase_last_completed[phase] = 0

                if total == 0:
                    phase_progress = None
                    phase_task_id = None
                    if phase == Phase.EMBED and detail:
                        con.print(f"[dim]{detail}: all embeddings up to date[/dim]")
                    continue

                label = {
                    Phase.ENRICH: "Enriching descriptions",
                    Phase.STRIP: "Stripping boilerplate",
                    Phase.EMBED: "Generating embeddings",
                }.get(phase, str(phase))

                detail_str = f" ({detail})" if detail else ""
                con.print(f"\n[bold blue]{label}[/bold blue]{detail_str} for {total} jobs")

                phase_progress = _make_phase_progress(con)
                short_label = {
                    Phase.ENRICH: "Enriching",
                    Phase.STRIP: "Stripping",
                    Phase.EMBED: "Embedding",
                }.get(phase, str(phase))
                phase_task_id = phase_progress.add_task(short_label, total=total)
                phase_progress.start()

            case PhaseProgress(phase=phase, done=done, total=total):
                if phase_progress is not None and phase_task_id is not None:
                    last = phase_last_completed.get(phase, 0)
                    advance_by = done - last
                    if advance_by > 0:
                        phase_progress.advance(phase_task_id, advance_by)
                        phase_last_completed[phase] = done

            case PhaseDone(phase=phase):
                if phase_progress is not None:
                    phase_progress.stop()
                    phase_progress = None
                    phase_task_id = None
                label = {
                    Phase.ENRICH: "Description enrichment",
                    Phase.STRIP: "Boilerplate stripping",
                    Phase.EMBED: "Embeddings",
                }.get(phase, str(phase))
                con.print(f"[green]✓[/green] {label} complete.")

            case PhaseError(phase=_phase, job_id=job_id, title=title, error=error):
                target = phase_progress.console if phase_progress is not None else con
                target.print(f"  [red]✗[/red] {job_id} ({title}): [dim]{error[:80]}[/dim]")

            case ModelLoaded(model_key=key, device=device):
                con.print(f"  [dim]Loaded {key} on [bold]{device}[/bold][/dim]")

            case ModelUnloaded(model_key=key):
                con.print(f"  [dim]Unloading {key}, freeing memory...[/dim]")

            case RetryEvent(slug=slug, job_id=job_id, attempt=attempt, max_attempts=max_attempts, wait_seconds=wait, reason=reason):
                target = slug if slug else job_id
                con.print(f"  [yellow]⟳[/yellow] {target}: {reason}, retry {attempt}/{max_attempts} in {wait:.0f}s")


def _run_with_events(fn, con, overall_progress, overall_task):
    """Create queue, start consumer thread, call fn(events=queue), join."""
    eq = queue.SimpleQueue()
    consumer = threading.Thread(
        target=_consume_events,
        args=(eq, con, overall_progress, overall_task),
        daemon=True,
    )
    consumer.start()
    try:
        result = fn(events=eq)
    except BaseException:
        # Push Done so consumer exits even on error
        from jobbuddy.sync.types import Done
        eq.put(Done())
        consumer.join(timeout=2)
        raise
    consumer.join(timeout=5)
    return result


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


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
        console.print("[yellow]No cached data. Run 'ats sync' to populate.[/yellow]")
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
        console.print(f"[dim]{company_slug} — {len(rows)} jobs[/dim]")
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
        console.print("[yellow]No cached data. Run 'ats sync' to populate.[/yellow]")
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
def sync(
    company: Optional[str] = typer.Option(None, "--company", "-c", help="Sync only this company"),
    stale: Optional[float] = typer.Option(None, "--stale", "-s", help="Skip companies synced within N hours"),
):
    """Sync job listings from ATS boards into the local cache."""
    from rich.panel import Panel
    from rich.table import Table

    from jobbuddy.sync import sync_jobs

    registry = list_companies()
    scrapeable = sum(1 for c in registry.values() if c.ats is not None)
    target_count = 1 if company else scrapeable

    progress = _make_phase_progress(console)
    overall_task = progress.add_task(
        "Syncing" if not company else f"Syncing {company}",
        total=target_count,
    )

    progress.start()
    try:
        results = _run_with_events(
            lambda events: sync_jobs(
                company_slug=company,
                stale_hours=stale,
                events=events,
            ),
            console,
            progress,
            overall_task,
        )
    except ValueError as e:
        progress.stop()
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)
    progress.stop()

    # Summary
    if not results:
        console.print("[dim]Nothing to sync.[/dim]")
        return

    ok_count = sum(1 for r in results if r.ok)
    err_count = sum(1 for r in results if not r.ok)
    total_jobs = sum(r.job_count for r in results if r.ok)

    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("Companies", f"[green]{ok_count} synced[/green]" + (f", [red]{err_count} failed[/red]" if err_count else ""))
    summary.add_row("Jobs cached", f"[bold]{total_jobs:,}[/bold]")

    if err_count:
        errors = [r for r in results if not r.ok]
        err_lines = "\n".join(f"  [red]•[/red] {r.slug}: [dim]{r.error}[/dim]" for r in errors)
        summary.add_row("Errors", "")
        console.print(Panel(summary, title="[bold]Sync Complete[/bold]", border_style="yellow"))
        console.print(err_lines)
    else:
        console.print(Panel(summary, title="[bold]Sync Complete[/bold]", border_style="green"))


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


@app.command()
def log(
    url: str = typer.Argument(help="Job listing URL"),
    action: str = typer.Option("Application", "--action", "-a", help="Action type (Application, Contact, Screen, etc.)"),
    person: str = typer.Option("", "--person", "-p", help="Contact person name"),
    notes: str = typer.Option("", "--notes", "-n", help="Free-text notes"),
    log_date: str = typer.Option(None, "--date", "-d", help="Date (YYYY-MM-DD), defaults to today"),
):
    """Fetch job from URL, append to job search log CSV, and save listing."""
    from jobbuddy.job_log import append_row, find_duplicates

    try:
        result = fetch_from_url(url)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    # Check for duplicates
    dupes = find_duplicates(url=url)
    if dupes:
        console.print(f"[yellow]Duplicate found:[/yellow] {result.company.name} - {result.job.title}")
        for d in dupes:
            console.print(f"  {d.get('date', '?')} | {d.get('action', '?')} | {d.get('status', '')}")
        console.print("[dim]Logging anyway (may be a follow-up).[/dim]")

    # Save the job listing file
    filepath = save_job_listing(result.company, result.job)
    console.print(f"[green]Saved listing:[/green] {filepath}")

    # Append to CSV
    row = append_row(
        company=result.company.name,
        role=result.job.title,
        action=action,
        job_id=result.job.id,
        location=result.job.location or "",
        url=url,
        person=person,
        notes=notes,
        row_date=log_date,
    )
    console.print(f"[green]Logged:[/green] {row['action']} → {result.company.name} - {result.job.title} on {row['date']}")


@app.command()
def embed(
    company: Optional[str] = typer.Option(None, "--company", "-c", help="Only embed jobs for this company"),
):
    """Generate embeddings for cached jobs (without fetching or enriching)."""
    from jobbuddy.store import JobStore
    from jobbuddy.sync.embed import EmbedPhase
    from jobbuddy.sync.types import Done

    if not get_settings().db_path.exists():
        console.print("[yellow]No cached data. Run 'ats sync' to populate.[/yellow]")
        raise SystemExit(0)

    store = JobStore()

    # Determine which slugs to embed
    if company:
        resolved = lookup_by_name(company)
        if not resolved:
            store.close()
            console.print(f"[red]Unknown company: {company}[/red]")
            raise SystemExit(1)
        slugs = [resolved.slug]
    else:
        statuses = store.get_sync_status()
        slugs = [s["company_slug"] for s in statuses]

    if not slugs:
        store.close()
        console.print("[yellow]No synced companies found.[/yellow]")
        raise SystemExit(0)

    # Use a no-op progress bar for the overall (embed has no fetch phase)
    dummy_progress = _make_phase_progress(console)
    dummy_task = dummy_progress.add_task("Embedding", total=0)

    eq = queue.SimpleQueue()
    consumer = threading.Thread(
        target=_consume_events,
        args=(eq, console, dummy_progress, dummy_task),
        daemon=True,
    )
    consumer.start()

    try:
        EmbedPhase(store, slugs, eq).run()
    finally:
        eq.put(Done())
        consumer.join(timeout=5)
        store.close()


@app.command()
def strip(
    force: bool = typer.Option(False, "--force", "-f", help="Re-strip jobs that already have stripped descriptions"),
):
    """Strip boilerplate from cached job descriptions using Azure OpenAI."""
    from jobbuddy.store import JobStore
    from jobbuddy.sync.strip import StripPhase
    from jobbuddy.sync.types import Done

    settings = get_settings()
    if not settings.azure_openai_api_key or not settings.azure_openai_endpoint:
        console.print("[red]Azure OpenAI not configured.[/red]")
        console.print("[dim]Set JOBBUDDY_AZURE_OPENAI_API_KEY and JOBBUDDY_AZURE_OPENAI_ENDPOINT[/dim]")
        raise SystemExit(1)

    if not settings.db_path.exists():
        console.print("[yellow]No cached data. Run 'ats sync' to populate.[/yellow]")
        raise SystemExit(0)

    store = JobStore()

    if force:
        cleared = store.clear_stripped_descriptions()
        if cleared:
            console.print(f"[dim]Cleared {cleared} existing stripped descriptions[/dim]")

    # Use a no-op progress bar for the overall (strip has no fetch phase)
    dummy_progress = _make_phase_progress(console)
    dummy_task = dummy_progress.add_task("Stripping", total=0)

    eq = queue.SimpleQueue()
    consumer = threading.Thread(
        target=_consume_events,
        args=(eq, console, dummy_progress, dummy_task),
        daemon=True,
    )
    consumer.start()

    try:
        StripPhase(store, eq).run()
    finally:
        eq.put(Done())
        consumer.join(timeout=5)
        store.close()


@app.command()
def serve(
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
):
    """Start the semantic search web UI."""
    from jobbuddy.web import create_app
    console.print(f"[green]Starting web UI at[/green] http://{host}:{port}")
    create_app().run(host=host, port=port, debug=True)


if __name__ == "__main__":
    app()
