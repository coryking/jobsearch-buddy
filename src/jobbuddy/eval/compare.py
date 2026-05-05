"""Side-by-side compare of distill outputs across models for one prompt.

Prints markdown to stdout: per-job sections with short_jd, description_normalized,
and salary stacked across models. Designed for quick eyeball diff.

    jsb-eval compare --prompt distill-v3.1
    jsb-eval compare --prompt distill-v3.1 -m gpt-5.4-nano-medium -m DeepSeek-V3.2
    jsb-eval compare --prompt distill-v3.1 --files openai gsk
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer

from jobbuddy.eval.utils import RUNS_DIR

SECTION_HEADERS = (
    "=== TITLE ===",
    "=== COMPANY ===",
    "=== LOCATION ===",
    "=== ATS-PROVIDED SALARY ===",
    "=== COMPANY BIO",
    "=== JOB DESCRIPTION",
    "=== DISTILL: SHORT_JD ===",
    "=== DISTILL: DESCRIPTION_NORMALIZED ===",
    "=== DISTILL: SALARY ===",
)


def _parse_sections(text: str) -> dict[str, str]:
    """Split a run output file into a dict keyed by section header line."""
    out: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if any(stripped.startswith(h) for h in SECTION_HEADERS) and stripped.startswith("==="):
            if current is not None:
                out[current] = "\n".join(buf).strip()
            current = stripped
            buf = []
        else:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf).strip()
    return out


def _key_starts_with(d: dict[str, str], prefix: str) -> str:
    for k, v in d.items():
        if k.startswith(prefix):
            return v
    return ""


def compare(
    prompt: Annotated[str, typer.Option("--prompt", help="Prompt stem, e.g. distill-v3.1")],
    model: Annotated[Optional[list[str]], typer.Option(
        "--model", "-m",
        help="Restrict to specific model(s). Repeatable. Omit for all available.",
    )] = None,
    files: Annotated[Optional[list[str]], typer.Option(
        "--files", "-f",
        help="Substring filter on filenames. Repeatable; matches if ANY substring is in the name.",
    )] = None,
    runs_dir: Annotated[Path, typer.Option(help="Base runs directory")] = RUNS_DIR,
) -> None:
    """Print same-job-across-models comparison as markdown.

    Output structure: one ## per job, then for each section (short_jd /
    description_normalized / salary) a blockquote per model. Optimized for
    eyeball diff, not for parsing.
    """
    # Discover run dirs for this prompt
    prefix = f"{prompt}-"
    run_dirs = sorted(
        d for d in runs_dir.iterdir()
        if d.is_dir() and d.name.startswith(prefix) and len(d.name) > len(prefix)
    )
    if not run_dirs:
        print(f"No runs found for prompt '{prompt}' under {runs_dir}")
        raise typer.Exit(1)

    runs: list[tuple[str, Path]] = [(d.name[len(prefix):], d) for d in run_dirs]
    if model:
        wanted = set(model)
        runs = [(m, d) for m, d in runs if m in wanted]
        missing = wanted - {m for m, _ in runs}
        if missing:
            print(f"# Warning: requested models without runs: {sorted(missing)}")
    if not runs:
        print("No runs match the model filter.")
        raise typer.Exit(1)

    # Filenames present in the FIRST run dir (used as anchor)
    anchor_dir = runs[0][1]
    filenames = sorted(p.name for p in anchor_dir.glob("*.txt"))
    if files:
        filenames = [n for n in filenames if any(s.lower() in n.lower() for s in files)]
    if not filenames:
        print("No files match the filter.")
        raise typer.Exit(1)

    print(f"# Distill compare — prompt `{prompt}` — models: {', '.join(m for m, _ in runs)}\n")

    for fname in filenames:
        # Header from first available run
        header_text = ""
        for _, run_dir in runs:
            p = run_dir / fname
            if p.exists():
                header_text = p.read_text(encoding="utf-8")
                break
        if not header_text:
            continue
        sections = _parse_sections(header_text)
        title = sections.get("=== TITLE ===", "").strip()
        company = sections.get("=== COMPANY ===", "").strip()
        location = sections.get("=== LOCATION ===", "").strip()
        ats_salary = sections.get("=== ATS-PROVIDED SALARY ===", "").strip()

        print(f"## `{fname}`")
        print(f"**{title}** — {company} — {location}")
        if ats_salary and ats_salary != "(none)":
            print(f"_ATS-provided salary:_ `{ats_salary}`")
        print()

        # Reference content (same across all model runs — pulled from the
        # anchor file). Surface this BEFORE the per-model outputs so a
        # reviewer can verify claims against the source.
        bio = _key_starts_with(sections, "=== COMPANY BIO")
        jd = _key_starts_with(sections, "=== JOB DESCRIPTION")
        if bio:
            print("<details><summary>Company bio (reference)</summary>\n")
            print(bio)
            print("\n</details>\n")
        if jd:
            print("<details><summary>Original JD (reference)</summary>\n")
            print(jd)
            print("\n</details>\n")

        # Three rows: SHORT_JD, DESCRIPTION_NORMALIZED, SALARY
        for label, key in (
            ("SHORT_JD", "=== DISTILL: SHORT_JD ==="),
            ("DESCRIPTION_NORMALIZED", "=== DISTILL: DESCRIPTION_NORMALIZED ==="),
            ("SALARY", "=== DISTILL: SALARY ==="),
        ):
            print(f"### {label}")
            for model_name, run_dir in runs:
                p = run_dir / fname
                if not p.exists():
                    print(f"- **{model_name}**: _(missing)_\n")
                    continue
                content = _parse_sections(p.read_text(encoding="utf-8")).get(key, "").strip()
                if not content:
                    content = "_(empty)_"
                # Render as a labeled blockquote-ish block
                print(f"**{model_name}**")
                for line in content.splitlines():
                    print(f"> {line}")
                print()
        print("---\n")
