"""Eyeball-test tool for the FTS ranker.

`jsb search-debug jobs "<query>"` — runs the production search path, then
shows what the LLM would actually receive plus the evidence-quality stats
that motivate ranker changes (company concentration, rank saturation,
distill coverage).

Companies-side debug command will land alongside the company-search work.
"""

import typer
from rich.console import Console
from rich.table import Table

from jobbuddy.cli import app

search_debug_app = typer.Typer(help="Inspect what the search ranker is handing the calling LLM.")
app.add_typer(search_debug_app, name="search-debug")

console = Console()


@search_debug_app.command("jobs")
def jobs(
    query: str = typer.Argument(..., help="FTS query (websearch syntax: terms, \"phrases\", OR, -negation)"),
    location: str = typer.Option("", "--location", "-l", help="Location substring (comma-separated for OR)"),
    posted_since: str = typer.Option("", "--posted-since", "-s", help="e.g. 7d, 2w, 30d"),
    limit: int = typer.Option(20, "--limit", "-n", help="Top-K to display (matches the MCP default)"),
    pool: int = typer.Option(200, "--pool", help="Candidate pool size for stats (independent of --limit)"),
):
    """Show top-K plus ranker-quality stats for a query."""
    from jobbuddy.core import parse_duration_to_date
    from jobbuddy.store import JobStore

    posted_after = parse_duration_to_date(posted_since) if posted_since else None

    store = JobStore()
    try:
        try:
            rows = store.search_jobs_fts(
                query=query or None,
                location=location or None,
                posted_after=posted_after,
                limit=limit,
            )
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise SystemExit(1)
        stats = _candidate_stats(store, query, location, posted_after, pool)
    finally:
        store.close()

    _render_stats(query, stats, limit, pool)
    _render_top_k(rows, limit)
    _render_company_concentration(rows)


def _candidate_stats(store, query: str, location: str, posted_after: str | None, pool: int) -> dict:
    """Inspect the candidate pool that ranking operates over.

    Mirrors JobStore._build_filter_conditions; intentionally re-built here
    so we can also pull ts_rank values without going through the public
    search path.
    """
    conditions = ["j.listing_status = 'active'"]
    params: list = []

    if query:
        conditions.append("j.fts_vector @@ websearch_to_tsquery('english', %s)")
        params.append(query)

    if posted_after:
        conditions.append("j.published_at >= %s")
        params.append(posted_after)

    if location:
        terms = [t.strip() for t in location.split(",") if t.strip()]
        if terms:
            or_clauses = ["j.location ILIKE %s"] * len(terms)
            conditions.append(f"({' OR '.join(or_clauses)})")
            params.extend(f"%{t}%" for t in terms)

    where = " AND ".join(conditions)

    rank_expr = (
        f"ts_rank(j.fts_vector, websearch_to_tsquery('english', %s))"
        if query else "0::real"
    )
    rank_params = [query] + params if query else params

    total = store.conn.execute(
        f"SELECT count(*) AS n FROM jobs j WHERE {where}", params
    ).fetchone()["n"]

    company_total = store.conn.execute(
        f"SELECT count(DISTINCT company_slug) AS n FROM jobs j WHERE {where}",
        params,
    ).fetchone()["n"]

    null_pubat = store.conn.execute(
        f"SELECT count(*) AS n FROM jobs j WHERE {where} AND j.published_at IS NULL",
        params,
    ).fetchone()["n"]

    not_distilled = store.conn.execute(
        f"SELECT count(*) AS n FROM jobs j WHERE {where} AND j.short_jd IS NULL",
        params,
    ).fetchone()["n"]

    pool_rows = store.conn.execute(
        f"""SELECT j.company_slug, {rank_expr} AS rank
              FROM jobs j WHERE {where}
              ORDER BY rank DESC, j.published_at DESC NULLS LAST
              LIMIT %s""",
        rank_params + [pool],
    ).fetchall()

    pool_companies: dict[str, int] = {}
    rank_buckets = {"1.0": 0, "[0.5, 1.0)": 0, "[0.1, 0.5)": 0, "(0, 0.1)": 0, "0": 0}
    for r in pool_rows:
        pool_companies[r["company_slug"]] = pool_companies.get(r["company_slug"], 0) + 1
        rank = float(r["rank"] or 0)
        if rank >= 1.0:
            rank_buckets["1.0"] += 1
        elif rank >= 0.5:
            rank_buckets["[0.5, 1.0)"] += 1
        elif rank >= 0.1:
            rank_buckets["[0.1, 0.5)"] += 1
        elif rank > 0:
            rank_buckets["(0, 0.1)"] += 1
        else:
            rank_buckets["0"] += 1

    return {
        "total": total,
        "companies": company_total,
        "null_pubat": null_pubat,
        "not_distilled": not_distilled,
        "pool_size": len(pool_rows),
        "pool_companies": pool_companies,
        "rank_buckets": rank_buckets,
    }


def _render_stats(query: str, stats: dict, limit: int, pool: int) -> None:
    total = stats["total"]
    if total == 0:
        console.print(f"[yellow]No active jobs match query[/yellow] [dim]({query!r})[/dim]")
        return

    pct_null = (stats["null_pubat"] / total * 100) if total else 0
    pct_distilled = ((total - stats["not_distilled"]) / total * 100) if total else 0
    top_pool_company, top_pool_count = max(
        stats["pool_companies"].items(), key=lambda kv: kv[1]
    ) if stats["pool_companies"] else ("-", 0)
    pool_concentration = (top_pool_count / stats["pool_size"] * 100) if stats["pool_size"] else 0

    console.print(f"\n[bold]Query:[/bold] {query!r}  [dim](top-{limit} of pool-{pool})[/dim]")
    console.print(
        f"[bold]Candidate pool:[/bold] {total:,} active matches across "
        f"{stats['companies']:,} companies"
    )
    console.print(
        f"  null published_at: {stats['null_pubat']:,} ({pct_null:.1f}%)   "
        f"distilled (has short_jd): {pct_distilled:.1f}%"
    )
    console.print(
        f"  top-{pool} pool concentration: {top_pool_company} = "
        f"{top_pool_count}/{stats['pool_size']} ({pool_concentration:.0f}%)"
    )

    bucket_table = Table(title="ts_rank distribution (top pool)", show_header=True, header_style="bold")
    bucket_table.add_column("Bucket")
    bucket_table.add_column("Count", justify="right")
    for bucket, count in stats["rank_buckets"].items():
        bucket_table.add_row(bucket, str(count))
    console.print(bucket_table)


def _render_top_k(rows: list[dict], limit: int) -> None:
    if not rows:
        return
    table = Table(title=f"Top {min(limit, len(rows))} (what the LLM sees)", show_header=True, header_style="bold")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Company")
    table.add_column("Title")
    table.add_column("Location", overflow="fold", max_width=30)
    table.add_column("Posted", justify="right")
    table.add_column("D", justify="center")  # distilled?
    for i, r in enumerate(rows, 1):
        distilled = "✓" if r.get("short_jd") else "·"
        table.add_row(
            str(i),
            r["company_slug"],
            r["title"][:60],
            (r.get("location") or "")[:30],
            str(r.get("published_at") or "·"),
            distilled,
        )
    console.print(table)


@search_debug_app.command("companies")
def companies(
    query: str = typer.Argument(..., help="Vibe / theme query (e.g. 'AI-as-product startups')"),
    limit: int = typer.Option(20, "--limit", "-n", help="Top-K to display (matches MCP default)"),
):
    """Show top-K plus per-arm contribution stats for the hybrid company search.

    Goes directly to the store to surface vec_score, fts_score, and
    rrf_score — `core.find_companies` strips them from its public response,
    so this command is the score-aware view used for tuning the
    coverage_hint floor and spotting cases where one arm is doing all the
    work.
    """
    import psycopg

    from jobbuddy.embeddings import embed_text
    from jobbuddy.store import JobStore

    embedding, _tokens = embed_text(query)
    store = JobStore()
    try:
        rows = store.find_companies(embedding, query, limit=limit)
    except psycopg.Error as e:
        console.print(f"[red]Invalid query: {e}[/red]")
        raise SystemExit(1)
    finally:
        store.close()

    if not rows:
        console.print("[yellow]No companies returned[/yellow]")
        raise SystemExit(0)

    vec_only = sum(1 for r in rows if r["vec_score"] is not None and r["fts_score"] is None)
    fts_only = sum(1 for r in rows if r["fts_score"] is not None and r["vec_score"] is None)
    both = sum(1 for r in rows if r["vec_score"] is not None and r["fts_score"] is not None)
    top_vec = max((r["vec_score"] for r in rows if r["vec_score"] is not None), default=None)
    top_fts = max((r["fts_score"] for r in rows if r["fts_score"] is not None), default=None)

    console.print(f"\n[bold]Query:[/bold] {query!r}  [dim](top-{limit})[/dim]")
    console.print(
        f"[bold]Arm contribution:[/bold] vec-only={vec_only}  fts-only={fts_only}  both={both}"
    )
    console.print(
        f"  top vec_score: {top_vec:.4f}" if top_vec is not None else "  top vec_score: —"
    )
    console.print(
        f"  top fts_score: {top_fts:.4f}" if top_fts is not None else "  top fts_score: —"
    )
    table = Table(title=f"Top {len(rows)} (what the LLM sees)",
                  show_header=True, header_style="bold")
    table.add_column("#", justify="right", style="dim")
    table.add_column("RRF", justify="right")
    table.add_column("Vec", justify="right")
    table.add_column("FTS", justify="right")
    table.add_column("Slug")
    table.add_column("Name")

    def _fmt(v):
        return f"{v:.3f}" if v is not None else "—"

    for i, r in enumerate(rows, 1):
        table.add_row(
            str(i),
            _fmt(r["rrf_score"]),
            _fmt(r["vec_score"]),
            _fmt(r["fts_score"]),
            r["slug"],
            r["name"][:40],
        )
    console.print(table)


def _render_company_concentration(rows: list[dict]) -> None:
    if not rows:
        return
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["company_slug"]] = counts.get(r["company_slug"], 0) + 1
    if len(counts) == len(rows):
        console.print(f"[green]Top-K diversity: {len(counts)} unique companies (no flooding)[/green]")
        return

    floods = sorted(((c, n) for c, n in counts.items() if n > 1), key=lambda kv: -kv[1])
    table = Table(title="Top-K company concentration", show_header=True, header_style="bold")
    table.add_column("Company")
    table.add_column("Count in top-K", justify="right")
    table.add_column("Share", justify="right")
    for company, n in floods:
        share = n / len(rows) * 100
        style = "red" if share >= 50 else ("yellow" if share >= 25 else None)
        table.add_row(company, str(n), f"{share:.0f}%", style=style)
    console.print(table)
    console.print(
        f"[dim]{len(counts)} unique companies in top-{len(rows)}; "
        f"{len(rows) - len(counts)} slots lost to repeats[/dim]"
    )
