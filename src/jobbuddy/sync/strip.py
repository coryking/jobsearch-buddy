"""StripPhase -- LLM-based boilerplate removal from job descriptions.

Uses Azure OpenAI gpt-5-nano to strip boilerplate before embedding.
Prompt is v9-surgical-benefits (eval-validated, do not modify).
"""

from __future__ import annotations

import logging
from pathlib import Path

from openai import AzureOpenAI

from jobbuddy.settings import get_settings
from jobbuddy.sync.base import WorkerPhase
from jobbuddy.sync.display import PhaseState

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


class StripPhase(WorkerPhase):
    """Strip boilerplate from job descriptions using Azure OpenAI."""

    def __init__(self, db_path: str | Path, *, display: PhaseState,
                 max_workers: int = 60):
        super().__init__(db_path, max_workers=max_workers, display=display)
        self._client: AzureOpenAI | None = None

    def on_phase_start(self) -> None:
        settings = get_settings()
        self._client = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            timeout=60.0,
        )

    def count_remaining(self) -> int:
        return self._reader.get_jobs_needing_stripping(count_only=True)

    def poll_work(self, batch_size: int) -> list[dict]:
        return self._reader.get_jobs_needing_stripping(limit=batch_size)

    def process_item(self, item: dict) -> None:
        description = item["description"]
        settings = get_settings()

        # max_tokens: approximate upper bound (description chars / 4)
        max_tokens = max(len(description) // 4, 256)

        response = self._client.chat.completions.create(
            model=settings.azure_openai_model,
            messages=[
                {"role": "system", "content": STRIP_SYSTEM_PROMPT},
                {"role": "user", "content": f"<job_description>\n{description}\n</job_description>"},
            ],
            max_tokens=max_tokens,
        )
        stripped = response.choices[0].message.content.strip()

        store = self._get_thread_store()
        store.update_stripped_description(item["id"], stripped)
        self.display.advance(detail=item["company_slug"])
