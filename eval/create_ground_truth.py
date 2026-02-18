#!/usr/bin/env python3
"""Interactive ground truth creator.

Walks through each sample, lets you decide whether to include it,
and opens your $EDITOR so you can hand-strip the boilerplate.

Usage:
    uv run python eval/create_ground_truth.py
    uv run python eval/create_ground_truth.py --samples eval/data/samples/ --output eval/data/ground-truth/
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def preview(filepath: Path, lines: int = 15) -> str:
    """Return the first N lines of a file."""
    text = filepath.read_text(encoding="utf-8")
    return "\n".join(text.splitlines()[:lines])


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive ground truth creator")
    parser.add_argument("--samples", type=Path, default=Path("eval/data/samples"))
    parser.add_argument("--output", type=Path, default=Path("eval/data/ground-truth"))
    args = parser.parse_args()

    samples_dir: Path = args.samples
    output_dir: Path = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    editor = os.environ.get("EDITOR", "nano")

    sample_files = sorted(f for f in samples_dir.glob("*.txt"))
    if not sample_files:
        print(f"No .txt files found in {samples_dir}")
        sys.exit(1)

    already_done = {f.name for f in output_dir.glob("*.txt")}
    remaining = [f for f in sample_files if f.name not in already_done]

    print(f"\n{len(sample_files)} samples total, {len(already_done)} already in ground-truth, {len(remaining)} remaining\n")

    included = len(already_done)
    for i, sample in enumerate(remaining, 1):
        print(f"{'=' * 60}")
        print(f"[{i}/{len(remaining)}]  {sample.name}")
        print(f"{'=' * 60}")
        print(preview(sample))
        print(f"\n{'...' if len(sample.read_text().splitlines()) > 15 else ''}")
        print(f"\nGround truth so far: {included}")
        print()

        while True:
            choice = input("Include as ground truth? [y/n/q] ").strip().lower()
            if choice in ("y", "n", "q"):
                break
            print("  y = yes (copy + edit), n = skip, q = quit")

        if choice == "q":
            print("\nQuitting.")
            break

        if choice == "n":
            continue

        dest = output_dir / sample.name
        shutil.copy2(sample, dest)
        subprocess.run([editor, str(dest)])
        included += 1
        print(f"  Saved: {dest}\n")

    print(f"\nDone. {included} ground-truth files in {output_dir}/")


if __name__ == "__main__":
    main()
