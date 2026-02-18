"""StripPhase — LLM-based boilerplate removal from job descriptions."""

from __future__ import annotations

import logging

from openai import AzureOpenAI

from jobbuddy.settings import get_settings
from jobbuddy.store import JobStore
from jobbuddy.sync.types import (
    EventQueue,
    Phase,
    PhaseDone,
    PhaseError,
    PhaseProgress,
    PhaseStarted,
)

log = logging.getLogger(__name__)

STRIP_SYSTEM_PROMPT = """\
You clean job descriptions for use in a search index. Remove sections that are
identical across all jobs at a company and don't help distinguish one role from
another. Return ONLY the cleaned text — no preamble, no explanation.

Remove:
- EEO / equal opportunity statements
- Generic benefits lists (health, dental, vision, 401k, PTO — the standard package)
- Drug-free workplace policies
- Accessibility / accommodation statements
- Legal disclaimers and compliance notices
- Pay transparency legal framing ("pursuant to [state] law, the expected salary range is...")

Keep:
- Role responsibilities and day-to-day work
- Required and preferred qualifications
- Tech stack, tools, and methodologies
- Team and project descriptions
- Company description / "About Us" (provides company context)
- Reporting structure and level
- Specific compensation details: salary ranges (the numbers themselves), RSUs,
  equity, signing bonuses, relocation packages — anything that signals role level
- Remote / hybrid / onsite details"""


class StripPhase:
    def __init__(self, store: JobStore, events: EventQueue):
        self.store = store
        self.events = events

    def run(self) -> None:
        """Strip boilerplate from job descriptions using Azure OpenAI.

        Processes all unstripped jobs in batches. No-op if Azure OpenAI
        credentials are not configured.
        """
        settings = get_settings()
        if not settings.azure_openai_api_key or not settings.azure_openai_endpoint:
            log.debug("Azure OpenAI not configured, skipping strip phase")
            return

        total = self.store.get_jobs_needing_stripping(count_only=True)
        if total == 0:
            return

        self.events.put(PhaseStarted(Phase.STRIP, total))

        client = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            timeout=30.0,
        )

        done = 0
        errors = 0
        batch_size = settings.strip_batch_size

        while done < total:
            jobs = self.store.get_jobs_needing_stripping(limit=batch_size)
            if not jobs:
                break

            for job in jobs:
                log.info("Stripping job %s (%s), %d chars", job["job_id"], job["title"], len(job["description"]))
                try:
                    stripped = self._strip_one(client, settings.azure_openai_model, job["description"])
                    log.info("Stripped job %s: %d → %d chars", job["job_id"], len(job["description"]), len(stripped))
                    self.store.update_stripped_description(job["id"], stripped)
                except Exception as e:
                    errors += 1
                    log.warning("Strip failed for job %s (%s): %s", job["job_id"], job["title"], e)
                    self.events.put(PhaseError(Phase.STRIP, job["job_id"], job["title"], str(e)))

                done += 1
                self.events.put(PhaseProgress(Phase.STRIP, done, total))

        self.events.put(PhaseDone(Phase.STRIP))

    def _strip_one(self, client, model: str, description: str) -> str:
        """Call Azure OpenAI to strip boilerplate from a single description."""
        log.debug("=== PROMPT (system) ===\n%s", STRIP_SYSTEM_PROMPT)
        log.debug("=== PROMPT (user, %d chars) ===\n%s", len(description), description[:2000])
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": STRIP_SYSTEM_PROMPT},
                {"role": "user", "content": description},
            ],
        )
        result = response.choices[0].message.content.strip()
        usage = response.usage
        log.debug("=== RESPONSE (%d chars, %d prompt_tokens, %d completion_tokens) ===\n%s",
                   len(result), usage.prompt_tokens, usage.completion_tokens, result[:2000])
        return result
