"""CLI for fetching job listings from ATS job boards."""

import csv
import io
import json
import re
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
    import threading

    from rich.panel import Panel
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
    from rich.table import Table
    from rich.text import Text

    from jobbuddy.sync import SyncCallbacks, SyncResult, sync_jobs

    registry = list_companies()
    scrapeable = sum(1 for c in registry.values() if c.ats is not None)
    target_count = 1 if company else scrapeable

    # Track active fetches for the progress display
    active_slugs: dict[str, int] = {}  # slug -> progress task_id
    lock = threading.Lock()

    progress = Progress(
        SpinnerColumn("dots"),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        MofNCompleteColumn(),
        TextColumn("[dim]|[/dim]"),
        TimeElapsedColumn(),
        console=console,
        expand=False,
    )
    overall_task = progress.add_task(
        "Syncing" if not company else f"Syncing {company}",
        total=target_count,
    )

    def on_start(slug: str) -> None:
        with lock:
            task_id = progress.add_task(f"  [cyan]{slug}[/cyan]", total=None)
            active_slugs[slug] = task_id

    def on_result(sr: SyncResult) -> None:
        with lock:
            # Remove the active spinner task
            if sr.slug in active_slugs:
                progress.remove_task(active_slugs.pop(sr.slug))
            # Advance overall progress
            progress.advance(overall_task)

        # Print completed line above the progress bar
        if sr.ok:
            progress.console.print(
                f"  [green]✓[/green] {sr.slug:<24} [bold]{sr.job_count:>4}[/bold] jobs  [dim]{sr.elapsed:>5.1f}s[/dim]"
            )
        else:
            short_err = (sr.error or "unknown")[:55]
            progress.console.print(
                f"  [red]✗[/red] {sr.slug:<24} [red]{short_err}[/red]  [dim]{sr.elapsed:>5.1f}s[/dim]"
            )

    def on_skip(slug: str, reason: str) -> None:
        with lock:
            progress.advance(overall_task)
        progress.console.print(f"  [dim]↷ {slug:<24} skipped ({reason})[/dim]")

    def on_fetch_progress(slug: str, fetched: int, total: int) -> None:
        with lock:
            task_id = active_slugs.get(slug)
            if task_id is not None:
                progress.update(task_id, total=total, completed=fetched)

    # Enrichment progress — for stub fetchers that need individual description fetches
    enrich_progress: Progress | None = None
    enrich_task_id: int | None = None
    enrich_last_completed: int = 0

    def on_enrich_start(total_jobs: int) -> None:
        nonlocal enrich_progress, enrich_task_id
        progress.stop()
        if total_jobs == 0:
            return
        console.print(f"\n[bold blue]Enriching descriptions[/bold blue] for {total_jobs} jobs")
        enrich_progress = Progress(
            SpinnerColumn("dots"),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            MofNCompleteColumn(),
            TextColumn("[dim]|[/dim]"),
            TimeElapsedColumn(),
            console=console,
            expand=False,
        )
        enrich_task_id = enrich_progress.add_task("Enriching", total=total_jobs)
        enrich_progress.start()

    def on_enrich_progress(done: int, total: int) -> None:
        nonlocal enrich_last_completed
        if enrich_progress is not None and enrich_task_id is not None:
            advance_by = done - enrich_last_completed
            if advance_by > 0:
                enrich_progress.advance(enrich_task_id, advance_by)
                enrich_last_completed = done

    def on_enrich_done() -> None:
        if enrich_progress is not None:
            enrich_progress.stop()
        console.print("[green]✓[/green] Description enrichment complete.")

    # Embedding progress — created lazily in on_embed_start
    embed_progress: Progress | None = None
    embed_task_id: int | None = None
    embed_last_completed: int = 0

    def on_embed_start(total_jobs: int, model_name: str, dimensions: int) -> None:
        nonlocal embed_progress, embed_task_id, embed_last_completed
        # Reset per-model progress tracking
        embed_last_completed = 0
        if total_jobs == 0:
            return
        # Stop any previous embed progress bar (multi-model case)
        if embed_progress is not None:
            embed_progress.stop()
        console.print(f"\n[bold blue]Generating embeddings[/bold blue] ({model_name}, {dimensions}d) for {total_jobs} jobs")
        embed_progress = Progress(
            SpinnerColumn("dots"),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            MofNCompleteColumn(),
            TextColumn("[dim]|[/dim]"),
            TimeElapsedColumn(),
            console=console,
            expand=False,
        )
        embed_task_id = embed_progress.add_task("Embedding", total=total_jobs)
        embed_progress.start()

    def on_embed_progress(done: int, total: int) -> None:
        nonlocal embed_last_completed
        if embed_progress is not None and embed_task_id is not None:
            advance_by = done - embed_last_completed
            if advance_by > 0:
                embed_progress.advance(embed_task_id, advance_by)
                embed_last_completed = done

    def on_embed_done() -> None:
        if embed_progress is not None:
            embed_progress.stop()
        console.print("[green]✓[/green] Embeddings complete.")

    def on_model_load(model_key: str, model_name: str) -> None:
        console.print(f"  [dim]Loading model {model_key} ({model_name})...[/dim]")

    def on_model_unload(model_key: str, model_name: str) -> None:
        console.print(f"  [dim]Unloading {model_key} and freeing GPU memory...[/dim]")

    callbacks = SyncCallbacks(
        on_start=on_start,
        on_result=on_result,
        on_skip=on_skip,
        on_fetch_progress=on_fetch_progress,
        on_enrich_start=on_enrich_start,
        on_enrich_progress=on_enrich_progress,
        on_enrich_done=on_enrich_done,
        on_embed_start=on_embed_start,
        on_embed_progress=on_embed_progress,
        on_embed_done=on_embed_done,
        on_model_load=on_model_load,
        on_model_unload=on_model_unload,
    )

    progress.start()
    try:
        results = sync_jobs(
            company_slug=company,
            stale_hours=stale,
            callbacks=callbacks,
        )
    except ValueError as e:
        progress.stop()
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)
    # Progress may already be stopped by on_embed_start; safe to call again
    progress.stop()

    # Summary
    if not results:
        console.print("[dim]Nothing to sync.[/dim]")
        return

    ok_count = sum(1 for r in results if r.ok)
    err_count = sum(1 for r in results if not r.ok)
    total_jobs = sum(r.job_count for r in results if r.ok)
    total_elapsed = sum(r.elapsed for r in results)

    # Build summary panel
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("Companies", f"[green]{ok_count} synced[/green]" + (f", [red]{err_count} failed[/red]" if err_count else ""))
    summary.add_row("Jobs cached", f"[bold]{total_jobs:,}[/bold]")

    if err_count:
        errors = [r for r in results if not r.ok]
        err_lines = "\n".join(f"  [red]•[/red] {r.slug}: [dim]{r.error}[/dim]" for r in errors)
        summary.add_row("Errors", "")
        console.print(Panel(summary, title="[bold]Sync Complete[/bold]", border_style="green" if not err_count else "yellow"))
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
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    from jobbuddy.store import JobStore
    from jobbuddy.sync import SyncCallbacks
    from jobbuddy.sync.embed import EmbedPhase

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

    # Embedding progress callbacks
    embed_progress: Progress | None = None
    embed_task_id: int | None = None
    embed_last_completed: int = 0

    def on_embed_start(total_jobs: int, model_name: str, dimensions: int) -> None:
        nonlocal embed_progress, embed_task_id, embed_last_completed
        embed_last_completed = 0
        if total_jobs == 0:
            console.print(f"[dim]{model_name} ({dimensions}d): all embeddings up to date[/dim]")
            return
        if embed_progress is not None:
            embed_progress.stop()
        console.print(f"\n[bold blue]Generating embeddings[/bold blue] ({model_name}, {dimensions}d) for {total_jobs} jobs")
        embed_progress = Progress(
            SpinnerColumn("dots"),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            MofNCompleteColumn(),
            TextColumn("[dim]|[/dim]"),
            TimeElapsedColumn(),
            console=console,
            expand=False,
        )
        embed_task_id = embed_progress.add_task("Embedding", total=total_jobs)
        embed_progress.start()

    def on_embed_progress(done: int, total: int) -> None:
        nonlocal embed_last_completed
        if embed_progress is not None and embed_task_id is not None:
            advance_by = done - embed_last_completed
            if advance_by > 0:
                embed_progress.advance(embed_task_id, advance_by)
                embed_last_completed = done

    def on_embed_done() -> None:
        if embed_progress is not None:
            embed_progress.stop()
        console.print("[green]✓[/green] Embeddings complete.")

    def on_model_load(model_key: str, model_name: str) -> None:
        console.print(f"  [dim]Loading model {model_key} ({model_name})...[/dim]")

    def on_model_unload(model_key: str, model_name: str) -> None:
        console.print(f"  [dim]Unloading {model_key} and freeing GPU memory...[/dim]")

    callbacks = SyncCallbacks(
        on_embed_start=on_embed_start,
        on_embed_progress=on_embed_progress,
        on_embed_done=on_embed_done,
        on_model_load=on_model_load,
        on_model_unload=on_model_unload,
    )

    try:
        EmbedPhase(store, slugs, callbacks).run()
    finally:
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
