"""Application activity logging command: log."""

import typer

from jobbuddy.cli import app, console
from jobbuddy.core import fetch_from_url, save_job_listing


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
    console.print(f"[green]Logged:[/green] {row['action']} -> {result.company.name} - {result.job.title} on {row['date']}")
