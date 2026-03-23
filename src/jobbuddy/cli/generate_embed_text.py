"""embed-text: generate embedding text for a job via the LLM combiner prompt.

Pulls the JD from the database, loads a company profile and prompt from disk,
runs the prompt through the OpenAI-compatible API, and outputs the result.
"""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from jobbuddy.cli import app

stderr = Console(stderr=True)

PROFILES_DIR = Path(__file__).parents[3] / "docs" / "company-profiles"


@app.command(name="embed-text")
def embed_text(
    company: str = typer.Argument(help="Company slug"),
    job_id: str = typer.Argument(help="External job ID"),
    prompt_file: Path = typer.Option(
        ..., "--prompt-file", "-p", help="System prompt file for the embedding text generator"
    ),
    company_profile: Optional[Path] = typer.Option(
        None, "--company-profile", help="Company profile path (default: docs/company-profiles/{company}.md)"
    ),
    out: Optional[Path] = typer.Option(
        None, "-o", "--out", help="Write output to file instead of stdout"
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="Override strip_model from settings"
    ),
):
    """Generate embedding text for a job by combining its JD with a company profile.

    Reads the system prompt from --prompt-file, pulls the job description from
    the database, loads the company profile, and runs the LLM to produce
    normalized embedding text.

    Examples:
        jsb embed-text walgreens 92959547344 -p prompts/embed-v1.txt
        jsb embed-text polyai 4796935101 -p prompts/embed-v1.txt -o /tmp/out.txt
        jsb embed-text stripe 12345 -p prompts/embed-v1.txt --model gpt-5-mini
    """
    from jobbuddy.openai_client import create_openai_client
    from jobbuddy.settings import get_settings
    from jobbuddy.store import JobStore

    settings = get_settings()

    # Load prompt
    if not prompt_file.exists():
        stderr.print(f"[red]Prompt file not found: {prompt_file}[/red]")
        raise typer.Exit(1)
    system_prompt = prompt_file.read_text().strip()

    # Load job from DB
    store = JobStore()
    results = store.get_jobs_by_external_ids([(company, job_id)])
    if not results:
        stderr.print(f"[red]Job not found: {company}/{job_id}[/red]")
        raise typer.Exit(1)

    job = list(results.values())[0]
    description = job.get("description") or ""
    if not description:
        stderr.print(f"[red]Job {company}/{job_id} has no description[/red]")
        raise typer.Exit(1)

    title = job.get("title", "")
    location = job.get("location", "")
    store.close()

    # Load company profile
    profile_path = company_profile or (PROFILES_DIR / f"{company}.md")
    if not profile_path.exists():
        stderr.print(f"[yellow]No company profile at {profile_path} — running without company context[/yellow]")
        profile_text = ""
    else:
        profile_text = profile_path.read_text().strip()

    # Build user message
    parts = []
    if profile_text:
        parts.append(f"<company_profile>\n{profile_text}\n</company_profile>")
    parts.append(f"<job_description>\nTitle: {title}\nLocation: {location}\n\n{description}\n</job_description>")
    user_message = "\n\n".join(parts)

    # Call LLM
    use_model = model or settings.strip_model
    stderr.print(f"[dim]{company}/{job_id} ({title}) via {use_model}...[/dim]")

    client = create_openai_client(timeout=60.0)
    response = client.chat.completions.create(
        model=use_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_completion_tokens=2048,
    )

    content = (response.choices[0].message.content or "").strip()

    if response.usage:
        stderr.print(f"[dim]{response.usage.total_tokens} tokens[/dim]")

    if not content:
        stderr.print("[red]LLM returned empty content[/red]")
        raise typer.Exit(1)

    if out:
        out.write_text(content)
        stderr.print(f"[dim]Wrote to {out}[/dim]")
    else:
        print(content)
