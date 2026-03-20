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
from rich.table import Table

from rich.console import Console

from jobbuddy.cli import app, console

stderr = Console(stderr=True)


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
        False, "--json", help="Output JSON (array of {query, source, score} records)"
    ),
):
    """Test embedding similarity between source texts and queries.

    Embeds source texts and queries, prints a cosine similarity matrix.
    No database involved — pure embedding comparison.

    Examples:
        echo "Walgreens cashier..." | jsb embed-test --stdin "retail jobs" "pharmacy jobs"
        jsb embed-test -f with-context.txt -f without-context.txt "retail jobs near me"
        jsb embed-test --json -f a.txt -f b.txt "query" | jq '.[] | select(.score > 0.4)'
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

    # Embed everything in one batch
    all_texts = [text for _, text in sources] + list(queries)
    stderr.print(f"[dim]Embedding {len(sources)} source(s) + {len(queries)} query/queries...[/dim]")
    vectors, tokens = embed_texts(all_texts)
    stderr.print(f"[dim]{tokens} tokens[/dim]")

    source_vecs = np.array(vectors[:len(sources)])
    query_vecs = np.array(vectors[len(sources):])

    # Cosine similarity matrix (vectors are L2-normalized by OpenAI)
    similarities = query_vecs @ source_vecs.T

    if json_output:
        records = []
        for i, query in enumerate(queries):
            for j, (label, _) in enumerate(sources):
                records.append({
                    "query": query,
                    "source": label,
                    "score": float(similarities[i, j]),
                })
        print(json.dumps(records, indent=2))
        return

    # Build table
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
