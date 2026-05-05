"""DistillPhase -- structured-output LLM call producing short_jd,
description_normalized, and salary in one round trip per job.

Replaces the old strip+embed pair. Polls jobs where description IS NOT NULL
and short_jd IS NULL (matches idx_jobs_needs_distill). The "done" predicate
is column-presence based — no hash, avoiding the embedding-starvation churn
class documented in MEMORY.md.

Failure mode: on parse/API error, the row is left null and retried on the
next sync invocation. Same posture as the deleted sync/strip.py.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
from openai import OpenAI

from jobbuddy.openai_client import create_openai_client
from jobbuddy.settings import get_settings
from jobbuddy.store import JobStore
from jobbuddy.sync.base import WorkerPhase
from jobbuddy.sync.display import PhaseState
from jobbuddy.types import DistillWorkItem

log = logging.getLogger(__name__)


# JSON schema sent to the model. Strict mode requires every property to be
# in `required` and additionalProperties:false. Salary is union-typed so the
# model can emit null when the JD has no comp info AND the upstream
# structured field was empty. If a deployment rejects the union form in
# strict mode, fall back to {"type": "string"} with empty-string convention.
DISTILL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["short_jd", "description_normalized", "salary"],
    "properties": {
        "short_jd": {"type": "string"},
        "description_normalized": {"type": "string"},
        "salary": {"type": ["string", "null"]},
    },
}


def _repo_root() -> Path:
    """Return the repo root path (where prompts/ lives).

    From src/jobbuddy/sync/distill.py: parents[3] is the repo root.
    """
    return Path(__file__).resolve().parents[3]


def load_distill_prompt(version: str) -> str:
    """Load the distill prompt file for the given version (e.g. 'distill-v1')."""
    path = _repo_root() / "prompts" / f"{version}.txt"
    return path.read_text(encoding="utf-8")


def build_user_message(item: DistillWorkItem, *, company_bio: str) -> str:
    """Construct the XML-tagged user message for the distill prompt.

    Field order is deliberate for prefix-cache stacking, most-stable first:
    company and bio are identical across all jobs at one company; location
    is identical across many jobs at one site (SF office, factory, store).
    The [system_prompt + company + bio + location] prefix becomes cacheable
    when sync processes jobs grouped by company+location."""
    ats_provided = "true" if (item["salary"] or "").strip() else "false"
    return (
        f"<company>{item['company_name']}</company>\n"
        f"<company_bio>{company_bio}</company_bio>\n"
        f"<location>{item['location'] or ''}</location>\n"
        f"<title>{item['title']}</title>\n"
        f"<ats_provided_salary>{ats_provided}</ats_provided_salary>\n"
        f"<job_description>\n{item['description']}\n</job_description>"
    )


class DistillPhase(WorkerPhase["DistillWorkItem"]):
    """Run the distill prompt over jobs missing short_jd."""

    def __init__(
        self, conninfo: str, *,
        display: PhaseState,
        max_workers: int = 30,
        slugs: list[str] | None = None,
    ):
        super().__init__(conninfo, max_workers=max_workers, display=display)
        self._slugs = slugs
        self._client: OpenAI | None = None
        self._system_prompt: str = ""
        self._company_bios: dict[str, str] = {}

    def on_phase_start(self) -> None:
        settings = get_settings()
        self._system_prompt = load_distill_prompt(settings.distill_prompt_version)
        self._client = create_openai_client(
            timeout=60.0,
            http_client=httpx.Client(
                limits=httpx.Limits(
                    max_connections=self.max_workers,
                    max_keepalive_connections=20,
                ),
            ),
        )
        # Cache {slug → long_bio} once. Bios change slowly; per-sync staleness
        # is fine and avoids re-fetching per job.
        reader = self._get_reader()
        rows = reader.conn.execute(
            "SELECT slug, COALESCE(long_bio, '') AS long_bio FROM companies"
        ).fetchall()
        self._company_bios = {r["slug"]: r["long_bio"] for r in rows}

    def item_key(self, item: DistillWorkItem) -> int:
        return item["id"]

    def item_label(self, item: DistillWorkItem) -> str:
        return f"{item['company_slug']}/{item['job_id']} ({item['title']})"

    def count_remaining(self) -> int:
        return self._get_reader().count_jobs_needing_distill(slugs=self._slugs)

    def poll_work(self, batch_size: int) -> list[DistillWorkItem]:
        return self._get_reader().get_jobs_needing_distill(
            limit=batch_size, slugs=self._slugs,
        )

    def process_item(self, item: DistillWorkItem) -> None:
        assert self._client is not None  # set in on_phase_start()
        settings = get_settings()
        company_bio = self._company_bios.get(item["company_slug"], "")
        user_msg = build_user_message(item, company_bio=company_bio)

        response = self._client.chat.completions.create(
            model=settings.distill_model,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_msg},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "distill",
                    "schema": DISTILL_SCHEMA,
                    "strict": True,
                },
            },
        )

        if response.usage:
            cached = 0
            details = getattr(response.usage, "prompt_tokens_details", None)
            if details is not None:
                cached = getattr(details, "cached_tokens", 0) or 0
            self.display.add_to_info_counter(response.usage.total_tokens, cached=cached)
            self.display.token_rate.record(response.usage.total_tokens)

        content = response.choices[0].message.content or ""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Non-JSON distill output for {item['company_slug']}/{item['job_id']}: {e}"
            ) from e

        short_jd = (parsed.get("short_jd") or "").strip()
        description_normalized = (parsed.get("description_normalized") or "").strip()
        salary_raw = parsed.get("salary")
        salary = salary_raw.strip() if isinstance(salary_raw, str) and salary_raw.strip() else None

        if not short_jd or not description_normalized:
            raise ValueError(
                f"Empty distill fields for {item['company_slug']}/{item['job_id']}"
            )

        job_pk = item["id"]
        self.submit_write(
            lambda store, pk=job_pk, sj=short_jd, dn=description_normalized, sa=salary:
                store.update_job_distill(
                    pk, short_jd=sj, description_normalized=dn, salary=sa,
                )
        )
        self.display.advance(detail=f"{item['company_slug']}: {item['title']}")
