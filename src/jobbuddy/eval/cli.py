"""Unified CLI for the strip eval harness.

Entry point: jsb-eval (registered in pyproject.toml).
"""

from __future__ import annotations

import typer

app = typer.Typer(help="Strip eval harness.", invoke_without_command=True)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


def register_commands() -> None:
    from jobbuddy.eval.extract import extract
    from jobbuddy.eval.ground_truth import ground_truth
    from jobbuddy.eval.judge import judge
    from jobbuddy.eval.results import results_app
    from jobbuddy.eval.run import run
    from jobbuddy.eval.score import score

    app.command()(extract)
    app.command()(run)
    app.command()(score)
    app.command()(judge)
    app.add_typer(results_app, name="results")
    app.command(name="ground-truth")(ground_truth)


register_commands()
