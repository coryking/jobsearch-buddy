#!/usr/bin/env python3
"""Extract stratified job description samples from the production DB.

Pulls active jobs with descriptions, round-robin across companies
to maximize employer diversity.

Usage:
    uv run python eval/extract_samples.py --count 25
    uv run python eval/extract_samples.py --count 100 --output eval/data/samples/
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from itertools import cycle
from pathlib import Path

from jobbuddy.store import JobStore


def sanitize_filename(title: str) -> str:
    """Turn a job title into a filesystem-safe slug."""
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:60]


def extract_samples(count: int, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    store = JobStore()
    try:
        # Get all active jobs with descriptions, grouped by company
        rows = store.conn.execute(
            """SELECT id, company_slug, job_id, title, description
               FROM jobs
               WHERE description IS NOT NULL
                 AND disappeared_at IS NULL
               ORDER BY company_slug, title"""
        ).fetchall()
    finally:
        store.close()

    if not rows:
        print("No jobs with descriptions found in DB.")
        return

    # Group by company
    by_company: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_company[r["company_slug"]].append(dict(r))

    print(f"Found {len(rows)} jobs across {len(by_company)} companies")

    # Round-robin across companies
    selected: list[dict] = []
    company_iters = {slug: iter(jobs) for slug, jobs in by_company.items()}
    company_cycle = cycle(sorted(by_company.keys()))
    exhausted: set[str] = set()

    while len(selected) < count and len(exhausted) < len(by_company):
        slug = next(company_cycle)
        if slug in exhausted:
            continue
        try:
            job = next(company_iters[slug])
            selected.append(job)
        except StopIteration:
            exhausted.add(slug)

    print(f"Selected {len(selected)} samples from {len(selected) - len([s for s in selected if selected.count(s) > 1])} companies")

    # Write files + manifest
    manifest = {}
    for i, job in enumerate(selected, 1):
        slug = job["company_slug"]
        safe_title = sanitize_filename(job["title"])
        filename = f"{i:03d}-{slug}-{safe_title}.txt"

        filepath = output_dir / filename
        filepath.write_text(job["description"], encoding="utf-8")

        manifest[filename] = {
            "company_slug": slug,
            "job_id": job["job_id"],
            "title": job["title"],
            "db_pk": job["id"],
        }

    manifest_path = output_dir / "sample_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Summary
    company_counts = defaultdict(int)
    for job in selected:
        company_counts[job["company_slug"]] += 1

    print(f"\nWrote {len(selected)} samples to {output_dir}/")
    print(f"Companies represented: {len(company_counts)}")
    print(f"Manifest: {manifest_path}")


def main():
    parser = argparse.ArgumentParser(description="Extract stratified job description samples")
    parser.add_argument("--count", type=int, default=25, help="Number of samples to extract (default: 25)")
    parser.add_argument("--output", type=Path, default=Path("eval/data/samples"), help="Output directory")
    args = parser.parse_args()

    extract_samples(args.count, args.output)


if __name__ == "__main__":
    main()
