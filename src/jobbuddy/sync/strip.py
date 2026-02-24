"""StripPhase -- LLM-based boilerplate removal from job descriptions.

Uses an OpenAI-compatible LLM to strip boilerplate before embedding.
Prompt is v9-surgical-benefits (eval-validated, do not modify).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import httpx
from openai import OpenAI

from jobbuddy.openai_client import create_openai_client
from jobbuddy.settings import get_settings
from jobbuddy.sync.base import WorkerPhase
from jobbuddy.sync.display import PhaseState
from jobbuddy.types import StripWorkItem

log = logging.getLogger(__name__)

# v9-surgical-benefits prompt -- copied VERBATIM from eval/prompts/v9-surgical-benefits.txt
# Do not modify this prompt. It was validated through the strip eval harness.
STRIP_SYSTEM_PROMPT = """\
You remove the boilerplate from job descriptions in order to feed a vector search.  Your job is to remove sections that sound like boilerplate while keeping the rest verbatim.  Every job description has boilerplate — your output should always be shorter than the input.

Boilerplate includes:
- EEO / equal opportunity statements — including long-form versions that list protected classes (race, gender, religion, age, disability, veteran status, etc.) or reference "equal opportunity employer" status
- Generic benefits lists (health, dental, vision, 401k, PTO — the standard package)
- Drug-free workplace policies
- Accessibility / accommodation statements
- Legal disclaimers and compliance notices
- Pay transparency legal framing ("pursuant to [state] law, the expected salary range is...")
- Recruiter scam warnings
- "We encourage all candidates to apply" diversity encouragement paragraphs

Things to keep:

- Role responsibilities and day-to-day work
- Required and preferred qualifications
- Tech stack, tools, and methodologies
- Team and project descriptions
- Company description / "About Us" (provides company context)
- Company-specific programs, products, or initiatives (e.g., named projects,
  internal platforms, flagship products) — these differentiate the company
- Reporting structure and level
- Specific compensation details: salary ranges (the numbers themselves), RSUs,
  equity, signing bonuses, relocation packages — anything that signals role level.
  Keep the numbers even if they're wrapped in legal framing
- Remote / hybrid / onsite details
- Eligibility requirements: export control, citizenship, security clearance,
  background checks, work authorization, visa sponsorship (whether offered
  or not) — these determine who can apply and are NOT legal boilerplate
- Differentiated benefits: anything that sets this company apart from
  the standard package (e.g., 100% coverage, fertility benefits, unlimited
  PTO, education stipends with dollar amounts). Only remove the generic
  "we offer health/dental/vision/401k/PTO" list that every company has

When a benefits section mixes generic and specific items, evaluate each item individually.  Keep items that include dollar amounts, percentages, time durations, or named programs (e.g., "$25K fertility stipend", "99% of premiums covered", "20 weeks parental leave").  Remove items that are just a category label (e.g., "health insurance", "401k", "PTO").  Do not remove the entire section just because some items are generic.

Remember, boilerplate isn't just something at the bottom, it can be anywhere.  To us, boilerplate means "this bit of content is on basically every job post everywhere -- it is meaningless for vector search because it is duplicated everywhere on all job posts in some form or other"  Don't just go "oh, this must be the boilerplate section" and lop the rest off!  Boilerplate does not mean "footer content"

The job description is provided in the user message inside <job_description> tags.

Do not alter any of the rest of the job description -- it must remain verbatim!  Do not change its ordering.  Return the updated job description only -- do not include a preamble or explaination.  Do not include the <job_description> tags in your output."""


class StripPhase(WorkerPhase["StripWorkItem"]):
    """Strip boilerplate from job descriptions using an OpenAI-compatible LLM."""

    def __init__(self, db_path: str | Path, *, display: PhaseState,
                 max_workers: int = 225, slug: str | None = None,
                 upstream_done: threading.Event | None = None):
        super().__init__(db_path, max_workers=max_workers, display=display,
                         upstream_done=upstream_done)
        self._client: OpenAI | None = None
        self._slug = slug

    def on_phase_start(self) -> None:
        self._client = create_openai_client(
            timeout=60.0,
            http_client=httpx.Client(
                limits=httpx.Limits(
                    max_connections=self.max_workers,
                    max_keepalive_connections=self.max_workers,
                ),
            ),
        )

    def item_key(self, item: StripWorkItem) -> int:
        return item["id"]

    def item_label(self, item: StripWorkItem) -> str:
        return f"{item['company_slug']}/{item['job_id']} ({item['title']})"

    def count_remaining(self) -> int:
        return self._get_reader().count_jobs_needing_stripping(slug=self._slug)

    def poll_work(self, batch_size: int) -> list[StripWorkItem]:
        return self._get_reader().get_jobs_needing_stripping(limit=batch_size, slug=self._slug)

    def process_item(self, item: StripWorkItem) -> None:
        assert self._client is not None  # set in on_phase_start()
        description = item["description"]
        settings = get_settings()

        # max_completion_tokens covers both visible output and reasoning tokens.
        # visible budget: ~len/4 (chars-to-tokens approximation)
        # reasoning budget: 1200 (p99 was 1088 at reasoning_effort=low, eval v9)
        max_tokens = max(len(description) // 4, 256) + 1200

        response = self._client.chat.completions.create(
            model=settings.strip_model,
            reasoning_effort="low",
            messages=[
                {"role": "system", "content": STRIP_SYSTEM_PROMPT},
                {"role": "user", "content": f"<job_description>\n{description}\n</job_description>"},
            ],
            max_completion_tokens=max_tokens,
        )

        if response.usage:
            self.display.add_to_info_counter(response.usage.total_tokens)
            self.display.token_rate.record(response.usage.total_tokens)

        content = response.choices[0].message.content
        stripped = content.strip() if content else ""
        if not stripped:
            log.error(
                "LLM returned empty content for %s/%s (%s) — will retry",
                item["company_slug"], item["job_id"], item["title"],
            )
            raise ValueError(f"Empty strip result for {item['company_slug']}/{item['job_id']}")

        job_pk = item["id"]
        self.submit_write(lambda store, pk=job_pk, s=stripped: store.update_stripped_description(pk, s))
        self.display.advance(detail=f"{item['company_slug']}: {item['title']}")
