"""Board health commands — is every registered board still answering?"""

import json
from collections import Counter

import typer
from rich.table import Table

from jobbuddy.cli import app, console
from jobbuddy.core.board_health import BoardCheck, check_boards, checkable
from jobbuddy.fetchers import SUPPORTED_ATS_TYPES
from jobbuddy.registry import list_companies

_STATUS_STYLE = {"ok": "green", "empty": "yellow", "error": "red"}


def _row_style(check: BoardCheck) -> str:
    return _STATUS_STYLE.get(check.status, "white")


def _detail(check: BoardCheck) -> str:
    """First line of the error — httpx appends a docs URL nobody needs in a table."""
    if not check.error:
        return ""
    return check.error.splitlines()[0].strip()


def _render_table(checks: list[BoardCheck]) -> Table:
    table = Table(title="Board health", header_style="bold")
    table.add_column("company")
    table.add_column("ats")
    table.add_column("board")
    table.add_column("status")
    table.add_column("jobs", justify="right")
    table.add_column("detail", overflow="fold", max_width=60)

    for check in checks:
        table.add_row(
            check.slug,
            check.ats,
            check.board or "",
            f"[{_row_style(check)}]{check.status}[/{_row_style(check)}]",
            "" if check.total is None else str(check.total),
            _detail(check),
        )
    return table


@app.command("check-boards")
def check_boards_command(
    ats: str | None = typer.Option(
        None, "--ats", "-a", help=f"Only probe this ATS: {', '.join(sorted(SUPPORTED_ATS_TYPES))}"
    ),
    company: list[str] = typer.Option(
        None, "--company", "-c", help="Only probe these companies (repeatable)"
    ),
    workers: int = typer.Option(8, "--workers", "-w", help="Concurrent probes"),
    all_rows: bool = typer.Option(
        False, "--all", help="Show healthy boards too (default: failures only)"
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a table"),
):
    """Live-probe every registered job board and report the ones that are broken.

    Hits each board's ATS right now with the same call the live MCP surface
    makes, so a board that fails here fails for the user. Reports three
    outcomes: `ok`, `empty` (answered 200 with zero jobs — silent drift, e.g.
    a disabled posting API), and `error`.

    Exits non-zero if any board is not `ok`.
    """
    registry = list_companies()
    selected = list(registry.values())

    if company:
        wanted = {c.lower() for c in company}
        selected = [c for c in selected if c.slug.lower() in wanted or c.name.lower() in wanted]
        missing = wanted - {c.slug.lower() for c in selected} - {c.name.lower() for c in selected}
        if missing:
            console.print(f"[red]Unknown company: {', '.join(sorted(missing))}[/red]")
            raise SystemExit(1)
    if ats:
        if ats not in SUPPORTED_ATS_TYPES:
            console.print(f"[red]Unknown ATS '{ats}'. Valid: {', '.join(sorted(SUPPORTED_ATS_TYPES))}[/red]")
            raise SystemExit(1)
        selected = [c for c in selected if c.ats == ats]

    targets = checkable(selected)
    if not targets:
        console.print("[yellow]No boards to check.[/yellow]")
        raise SystemExit(0)

    skipped = len(selected) - len(targets)
    console.print(
        f"Probing {len(targets)} board(s) with {workers} workers"
        + (f" ({skipped} directory-only entries skipped)" if skipped else "")
        + "..."
    )

    checks = sorted(
        check_boards(targets, workers=workers),
        key=lambda c: (c.status == "ok", c.ats, c.slug),
    )
    failures = [c for c in checks if c.failed]

    if as_json:
        payload = {
            "probed": len(checks),
            "ok": len(checks) - len(failures),
            "failures": [
                {
                    "slug": c.slug, "ats": c.ats, "board": c.board, "status": c.status,
                    "total": c.total, "error": c.error, "error_class": c.error_class,
                }
                for c in failures
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        shown = checks if all_rows else failures
        if shown:
            console.print(_render_table(shown))
        console.print(
            f"\n[bold]{len(checks) - len(failures)} ok[/bold], "
            f"[yellow]{sum(1 for c in failures if c.status == 'empty')} empty[/yellow], "
            f"[red]{sum(1 for c in failures if c.status == 'error')} error[/red]"
        )
        shapes = Counter(c.error_class for c in failures if c.error_class)
        if shapes:
            console.print(
                "By failure shape: "
                + ", ".join(f"{shape}={count}" for shape, count in shapes.most_common())
            )

    raise SystemExit(1 if failures else 0)
