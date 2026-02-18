#!/usr/bin/env python3
"""LLM-as-judge — auto-scores strip eval outputs using gpt-5-mini.

For each sample, sends original + stripped to the judge model and
parses structured JSON scores.

Usage:
    uv run python eval/judge.py \
        --run eval/data/runs/v1-gpt4.1nano/ \
        --samples eval/data/samples/
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

from openai import AzureOpenAI

from jobbuddy.settings import get_settings

SCORE_FIELDS = ["boilerplate_removal", "content_preservation", "no_hallucination"]
CSV_HEADER = ["filename", "run_name", *SCORE_FIELDS, "reasoning"]


def load_judge_prompt(prompt_file: Path | None = None) -> str:
    if prompt_file is None:
        prompt_file = Path("eval/prompts/judge.txt")
    return prompt_file.read_text(encoding="utf-8").strip()


def parse_judge_response(text: str) -> dict | None:
    """Parse JSON scores from judge response. Returns None on failure."""
    # Strip markdown fences if present
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove first and last fence lines
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
        # Validate required fields
        for field in SCORE_FIELDS:
            val = data.get(field)
            if not isinstance(val, int) or val < 1 or val > 5:
                return None
        return data
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def run_judge(
    run_dir: Path,
    samples_dir: Path,
    scores_file: Path,
    judge_model: str,
    judge_prompt_file: Path | None,
) -> None:
    run_name = run_dir.name
    judge_prompt = load_judge_prompt(judge_prompt_file)

    # Find sample files that have run output
    run_files = sorted(f for f in run_dir.glob("*.txt"))
    if not run_files:
        print(f"No .txt files found in {run_dir}")
        return

    # Check for already-judged
    already_judged: set[str] = set()
    if scores_file.exists():
        with scores_file.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["run_name"] == run_name:
                    already_judged.add(row["filename"])

    remaining = [f for f in run_files if f.name not in already_judged]
    if len(remaining) < len(run_files):
        print(f"Skipping {len(run_files) - len(remaining)} already-judged files")

    if not remaining:
        print("All files already judged!")
        return

    print(f"Judging {len(remaining)} files for run '{run_name}' with model={judge_model}")

    settings = get_settings()
    client = AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        timeout=60.0,
    )

    # Prepare CSV
    write_header = not scores_file.exists()
    scores_file.parent.mkdir(parents=True, exist_ok=True)

    results = []
    errors = []

    for i, run_file in enumerate(remaining, 1):
        filename = run_file.name
        original_path = samples_dir / filename

        if not original_path.exists():
            print(f"  [{i}/{len(remaining)}] {filename}: SKIP (original not found)")
            continue

        original = original_path.read_text(encoding="utf-8")
        stripped = run_file.read_text(encoding="utf-8")

        print(f"  [{i}/{len(remaining)}] {filename}...", end=" ", flush=True)

        try:
            start = time.monotonic()
            user_content = f"ORIGINAL:\n{original}\n\n---\n\nSTRIPPED:\n{stripped}"
            response = client.chat.completions.create(
                model=judge_model,
                messages=[
                    {"role": "system", "content": judge_prompt},
                    {"role": "user", "content": user_content},
                ],
            )
            elapsed = time.monotonic() - start

            raw = response.choices[0].message.content.strip()
            scores = parse_judge_response(raw)

            if scores is None:
                print(f"PARSE ERROR ({elapsed:.1f}s)")
                print(f"    Raw: {raw[:200]}")
                errors.append({"filename": filename, "error": "parse_error", "raw": raw[:500]})
                continue

            row = {
                "filename": filename,
                "run_name": run_name,
                "boilerplate_removal": scores["boilerplate_removal"],
                "content_preservation": scores["content_preservation"],
                "no_hallucination": scores["no_hallucination"],
                "reasoning": scores.get("reasoning", ""),
            }
            results.append(row)

            avg = sum(scores[f] for f in SCORE_FIELDS) / 3
            print(f"avg={avg:.1f} (bp={scores['boilerplate_removal']} cp={scores['content_preservation']} nh={scores['no_hallucination']}) {elapsed:.1f}s")

        except Exception as e:
            print(f"ERROR: {e}")
            errors.append({"filename": filename, "error": str(e)})

    # Write results
    if results:
        with scores_file.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
            if write_header:
                writer.writeheader()
            for row in results:
                writer.writerow(row)

    # Summary
    print(f"\nJudged: {len(results)} succeeded, {len(errors)} failed")
    if results:
        for field in SCORE_FIELDS:
            vals = [r[field] for r in results]
            avg = sum(vals) / len(vals)
            print(f"  {field}: mean={avg:.2f}")
    print(f"Scores saved to: {scores_file}")


def main():
    parser = argparse.ArgumentParser(description="LLM-as-judge for strip eval runs")
    parser.add_argument("--run", type=Path, required=True, help="Path to run output directory")
    parser.add_argument("--samples", type=Path, default=Path("eval/data/samples"),
                        help="Path to original samples directory")
    parser.add_argument("--scores", type=Path, default=Path("eval/data/scores/judge_scores.csv"),
                        help="Path to scores CSV output")
    parser.add_argument("--model", type=str, default="gpt-5-mini",
                        help="Judge model deployment name (default: gpt-5-mini)")
    parser.add_argument("--judge-prompt", type=Path, default=None,
                        help="Path to judge prompt (default: eval/prompts/judge.txt)")
    args = parser.parse_args()

    if not args.run.exists():
        parser.error(f"Run directory not found: {args.run}")
    if not args.samples.exists():
        parser.error(f"Samples directory not found: {args.samples}")

    run_judge(args.run, args.samples, args.scores, args.model, args.judge_prompt)


if __name__ == "__main__":
    main()
