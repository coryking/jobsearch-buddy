"""Interactive ground truth creator.

Walks through each sample, lets you decide whether to include it,
and opens your $EDITOR so you can hand-strip the boilerplate.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer


def _preview(filepath: Path, lines: int = 15) -> str:
    """Return the first N lines of a file."""
    text = filepath.read_text(encoding="utf-8")
    return "\n".join(text.splitlines()[:lines])


def ground_truth(
    samples: Annotated[Path, typer.Option(help="Path to samples directory")] = Path("eval/data/samples"),
    output: Annotated[Path, typer.Option(help="Output directory for ground truth files")] = Path("eval/data/ground-truth"),
) -> None:
    """Interactive ground truth creator -- copy samples and hand-edit."""
    output.mkdir(parents=True, exist_ok=True)
    editor = os.environ.get("EDITOR", "nano")

    sample_files = sorted(f for f in samples.glob("*.txt"))
    if not sample_files:
        print(f"No .txt files found in {samples}")
        raise typer.Exit(1)

    already_done = {f.name for f in output.glob("*.txt")}
    remaining = [f for f in sample_files if f.name not in already_done]

    print(f"\n{len(sample_files)} samples total, {len(already_done)} already in ground-truth, {len(remaining)} remaining\n")

    included = len(already_done)
    for i, sample in enumerate(remaining, 1):
        print(f"{'=' * 60}")
        print(f"[{i}/{len(remaining)}]  {sample.name}")
        print(f"{'=' * 60}")
        print(_preview(sample))
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

        dest = output / sample.name
        shutil.copy2(sample, dest)
        subprocess.run([editor, str(dest)])
        included += 1
        print(f"  Saved: {dest}\n")

    print(f"\nDone. {included} ground-truth files in {output}/")
