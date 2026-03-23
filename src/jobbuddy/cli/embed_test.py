"""embed-test command: pure embedding similarity test harness.

No database, no pgvector. Embeds source texts and queries via the OpenAI API,
prints a cosine similarity matrix.
"""

import json
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import typer
from rich.console import Console
from rich.table import Table

from jobbuddy.cli import app, console

stderr = Console(stderr=True)


def _build_records(
    queries: list[str],
    sources: list[tuple[str, str]],
    similarities: np.ndarray,
) -> list[dict]:
    """Build flat list of {query, source, score} dicts from similarity matrix."""
    records = []
    for i, query in enumerate(queries):
        for j, (label, _) in enumerate(sources):
            records.append({
                "query": query,
                "source": label,
                "score": float(similarities[i, j]),
            })
    return records


def _build_dataframe(records: list[dict]):
    """Build a pandas DataFrame with a pivot-friendly layout.

    Returns a DataFrame with columns: query, source, score, plus a pivot
    table (queries as rows, sources as columns) for spreadsheet output.
    """
    import pandas as pd

    df = pd.DataFrame(records)
    pivot = df.pivot(index="query", columns="source", values="score")
    return df, pivot


@app.command(name="embed-test")
def embed_test(
    queries: list[str] = typer.Argument(help="Query strings to test against source texts"),
    file: Optional[list[Path]] = typer.Option(
        None, "-f", "--file", help="Source text files (reads content as embedding input)"
    ),
    stdin: bool = typer.Option(
        False, "--stdin", help="Read one source text from stdin"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output JSON records (flat array of {query, source, score})"
    ),
    csv_output: bool = typer.Option(
        False, "--csv", help="Output CSV pivot table (queries as rows, sources as columns)"
    ),
    xlsx_output: Optional[Path] = typer.Option(
        None, "--xlsx", help="Write Excel workbook to this path (pivot + raw data sheets)"
    ),
    out: Optional[Path] = typer.Option(
        None, "-o", "--out", help="Write output to file instead of stdout (for --json and --csv)"
    ),
):
    """Test embedding similarity between source texts and queries.

    Embeds source texts and queries, prints a cosine similarity matrix.
    No database involved — pure embedding comparison.

    Output formats:
        (default)  Rich table to terminal
        --json     Flat JSON array of {query, source, score} records
        --csv      Pivot CSV (queries as rows, sources as columns)
        --xlsx F   Excel workbook with pivot + raw sheets

    Use -o/--out with --json or --csv to write to a file instead of stdout.

    Examples:
        jsb embed-test -f bare.txt -f enriched.txt "retail jobs" "AI startup"
        jsb embed-test --json -f a.txt -f b.txt "query" | jq 'sort_by(-.score)[:5]'
        jsb embed-test --csv -o results.csv -f a.txt -f b.txt "query1" "query2"
        jsb embed-test --xlsx results.xlsx -f a.txt -f b.txt "query1" "query2"
    """
    from jobbuddy.embeddings import embed_texts

    # Collect source texts
    sources: list[tuple[str, str]] = []  # (label, text)

    if stdin:
        text = sys.stdin.read().strip()
        if not text:
            console.print("[red]No input on stdin[/red]")
            raise typer.Exit(1)
        sources.append(("stdin", text))

    if file:
        for path in file:
            if not path.exists():
                console.print(f"[red]File not found: {path}[/red]")
                raise typer.Exit(1)
            sources.append((path.stem, path.read_text().strip()))

    if not sources:
        console.print("[red]Provide at least one source via --file or --stdin[/red]")
        raise typer.Exit(1)

    if not queries:
        console.print("[red]Provide at least one query argument[/red]")
        raise typer.Exit(1)

    if xlsx_output and xlsx_output.suffix != ".xlsx":
        console.print("[red]--xlsx path must end in .xlsx[/red]")
        raise typer.Exit(1)

    # Embed everything in one batch
    all_texts = [text for _, text in sources] + list(queries)
    stderr.print(f"[dim]Embedding {len(sources)} source(s) + {len(queries)} query/queries...[/dim]")
    vectors, tokens = embed_texts(all_texts)
    stderr.print(f"[dim]{tokens} tokens[/dim]")

    source_vecs = np.array(vectors[:len(sources)])
    query_vecs = np.array(vectors[len(sources):])

    # Cosine similarity matrix (vectors are L2-normalized by OpenAI)
    similarities = query_vecs @ source_vecs.T

    records = _build_records(queries, sources, similarities)

    # --- JSON output ---
    if json_output:
        text = json.dumps(records, indent=2)
        if out:
            out.write_text(text)
            stderr.print(f"[dim]Wrote {len(records)} records to {out}[/dim]")
        else:
            print(text)
        return

    # --- CSV output ---
    if csv_output:
        _, pivot = _build_dataframe(records)
        if out:
            pivot.to_csv(out)
            stderr.print(f"[dim]Wrote pivot CSV to {out}[/dim]")
        else:
            print(pivot.to_csv())
        return

    # --- Excel output ---
    if xlsx_output:
        df, pivot = _build_dataframe(records)
        with __import__("pandas").ExcelWriter(xlsx_output, engine="openpyxl") as writer:
            pivot.to_excel(writer, sheet_name="pivot")
            df.to_excel(writer, sheet_name="raw", index=False)
        stderr.print(f"[dim]Wrote {xlsx_output} (pivot + raw sheets)[/dim]")
        return

    # --- Default: Rich table ---
    table = Table(show_header=True, header_style="bold")
    table.add_column("Query", style="cyan", no_wrap=False, max_width=50)
    for label, _ in sources:
        table.add_column(label, justify="right", style="green")

    for i, query in enumerate(queries):
        row = [query]
        for j in range(len(sources)):
            score = similarities[i, j]
            row.append(f"{score:.4f}")
        table.add_row(*row)

    console.print(table)
