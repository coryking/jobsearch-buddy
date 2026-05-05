---
title: Phase 2 — Company Bio Pipeline
type: plan
status: draft
date: 2026-05-05
branch: worktree-phase2-company-bio
parents:
  - docs/brainstorms/2026-05-05-phase2-company-bio-experiments.md
  - docs/brainstorms/2026-05-05-job-search-redesign-requirements.md
---

# Phase 2 — Company Bio Pipeline

## Scope (radically narrowed)

Get researched company bios into the database so the Phase 1 extract phase
can use them as context when producing `short_jd`. Nothing else.

**In:**
- New `jsb research-companies` CLI (backfill all + one-at-a-time modes)
- Researcher emits `short_bio` and `long_bio` as structured JSON
- `companies.short_bio` and `companies.long_bio` columns
- Auto-trigger on new company added to the registry
- Productionize the existing scratchpad invocation mechanism

**Out (deliberately):**
- `find_companies` MCP tool — bio is not exposed to MCP clients today
- Per-row bio attachment on `search_jobs` results
- Embeddings on bios (not generated, not stored, not indexed)
- Company buckets — requires multi-user, which doesn't exist yet
- Two-mode pipeline (no profile shortcut path; always run the researcher)
- Re-running extract on existing jobs as part of this work — that's
  Phase 1's call once bios are populated

## Why now

The Phase 1 extract phase is being redesigned to produce `short_jd` that
elevates *specifics over archetypes*. Without company context, the LLM only
has the JD body to work with — and JD bodies often don't say what the company
actually does. With `long_bio` in the prompt, "Senior Tech at Caliber" can
become "you'd repair collision damage at an I-CAR Gold Class shop on
flat-rate pay" instead of "you'd be a senior automotive technician."

The bio is also a forward investment: when the future MCP-exposure question
is answered (workflow A's `find_companies`, workflow B's per-row bio,
buckets), the data is already in PG.

## Reused assets

- **`.claude/agents/company-research-agent.md`** — opus subagent prompt that
  produces 500-800 word profiles with NPOV, describe-don't-judge,
  evidence-driven length. Already industry-neutral by design (the 8 profiles
  in `docs/company-profiles/` span AI labs, defense, retail pharmacy, banks,
  CDN infrastructure, voice AI, payments). This is the v2 prompt the Phase
  2 brainstorm doc said we'd need to write — turns out it's already written.
- **`docs/company-profiles/*.md`** — 8 hand-curated long profiles. Not used
  as a pipeline shortcut (decision: always run researcher), but useful as
  reference shape during prompt iteration.
- **`scratchpad/foundry_company_research.py`** — the Azure Responses API +
  `web_search` invocation mechanism. Validated in the brainstorm phase:
  $14/1K Bing search transactions, gpt-5.4 + `no-filter` RAI policy,
  managed-identity auth via `DefaultAzureCredential`. **This becomes the
  basis of the new CLI command.**
- **`prompts/company-research-v1.txt`** — old short_bio-only prompt.
  Superseded by the new prompt (see "Prompt Design" below). Will be removed
  or replaced.

## Output schema

The researcher emits one structured JSON object per company:

```json
{
  "short_bio": "string, ~80 words, single paragraph",
  "long_bio": "string, ~500-800 words, prose"
}
```

`short_bio` is generated even though no consumer uses it today — it's cheap
to ask for and it lands in PG ready for future MCP exposure. `long_bio` is
the load-bearing field; it's what extract reads, and it doubles as embed
text if/when embeddings come back.

## Schema migration

`011_company_bios.sql` (or next available number — currently `011_phase1_redesign.sql`
exists on the parent branch; pick `012_company_bios.sql`):

```sql
ALTER TABLE companies
    ADD COLUMN short_bio TEXT,
    ADD COLUMN long_bio TEXT,
    ADD COLUMN bio_researched_at TIMESTAMPTZ,
    ADD COLUMN bio_model TEXT;            -- e.g. 'gpt-5.4' for provenance
```

No index needed — neither field is queried.

## Prompt design

Adapt `.claude/agents/company-research-agent.md` into a system prompt
(`prompts/company-research-v2.txt`). Keep:

- "Describe, don't judge" / NPOV stance
- Behavioral signals as evidence, not verdicts
- Industry-neutral framing
- Evidence-driven length

Adjust:

1. **Drop source prescriptions.** The current agent prompt names Glassdoor,
   Crunchbase, PitchBook, Wikipedia by category. Replace with: "use web
   search extensively; let the question route the search; cite inline when
   credibility matters." Don't hand the model a checklist of source types.
2. **Require structured output.** Two fields, `short_bio` and `long_bio`,
   per the schema above. The Azure Responses API supports `response_format`
   JSON schema natively.
3. **Hard exclusions.** Explicit list: do not include current open
   positions, salary ranges from current postings, office locations the JD
   itself would disclose. The bio describes the company, not the hiring
   surface.
4. **Multi-industry anchoring.** No in-prompt examples (the agent prompt
   doesn't carry them today; the model handles industry spread well
   without). If quality wobbles on regulated retail / trades / blue collar
   during empirical testing, add 2-3 distilled examples from
   `docs/company-profiles/`.

The five empirical reference companies stay the same as the brainstorm
doc: Anthropic, Anduril, 9-mothers, Walgreens, Caliber Collision. Spread
across AI lab / defense unicorn / defense seed-stage / regulated retail /
trades chain. Iterate prompt against this set.

## Pipeline mechanics

### CLI surface

```
jsb research-companies                   # Backfill: research all where long_bio IS NULL
jsb research-companies <slug>            # Research one company (idempotent: re-runs if --force)
jsb research-companies --force           # Re-research everything
jsb research-companies --stale 90d       # Re-research where bio_researched_at older than threshold
```

Lives in `cli/companies.py` (or `cli/research.py` — the executor decides
where it fits with the existing CLI split). Core logic in
`core.py`/`research.py` so it's importable from the auto-trigger code path.

### Invocation mechanism

Take `scratchpad/foundry_company_research.py` and move its core into
`src/jobbuddy/research.py`. Keep:

- Azure Responses API: `OpenAI(base_url="$ENDPOINT/openai/v1/", api_key=token_provider)`
- `tools=[{"type": "web_search"}]`
- Bearer token via `get_bearer_token_provider(DefaultAzureCredential(),
  "https://cognitiveservices.azure.com/.default")`
- Walk `response.output` to surface `web_search_call` items for logging
- Human-readable markdown side-output to `scratchpad/runs/` retained as a
  debug feature, gated by `--debug-output` flag (off by default in
  production CLI runs)

Add:

- Structured JSON output via `response_format={"type": "json_schema", ...}`
- `content_filter` retry path: on `incomplete_details.reason ==
  "content_filter"`, retry once with a slightly altered system message;
  on second failure, log and skip (don't crash the batch)
- Settings: `JOBBUDDY_RESEARCH_MODEL` (default `gpt-5.4`),
  `JOBBUDDY_RESEARCH_DEPLOYMENT` if needed

### Auto-trigger on new company

Falls out of the WorkerPhase design naturally. `ResearchPhase` polls for
`long_bio IS NULL`, so any company that lands in `companies` (via
`save_company()` from manual registration or fetcher-side discovery) gets
researched on the next phase run. New-company trigger and backfill are
the same code path.

`save_company()` itself stays pure DB — no extra I/O, no fire-and-forget
threading.

### Backfill economics

~600 companies × $0.05 per company on gpt-5.4 + canonical prompt =
~$30 one-time. ~$140/yr if quarterly refresh ever becomes a thing.
Negligible.

### Concurrency — reuse `WorkerPhase`

This is a `sync/` phase, not a bespoke CLI loop. Build it on
`WorkerPhase` (`sync/base.py`) — same machinery `EnrichPhase` and
`StripPhase` use. We get for free:

- DB-as-queue polling — phase finds companies with `long_bio IS NULL`
  and processes them
- `ThreadPoolExecutor` parallelism with per-thread DB connections via
  the existing single-threaded `WriteQueue`
- Graceful shutdown via `threading.Event` (Ctrl-C mid-batch is safe)
- `PhaseState` updates feeding the existing Rich Live TUI — same
  spinner/totals/rate display the rest of sync uses
- Per-company error isolation (one failed research doesn't crash the
  batch)

New file: `src/jobbuddy/sync/research.py` defining `ResearchPhase`
following the `EnrichPhase` shape.

`jsb research-companies` becomes a thin CLI wrapper that runs
`ResearchPhase` standalone (same way `jsb sync strip` runs only
`StripPhase` today). Auto-trigger on new company is the same phase
running at the end of `jsb sync` — added to the default phase list,
but tolerant of missing Azure config (skip with warning, don't fail
the whole sync) so devs without Azure access can still run other
phases.

Worker count: start at 8. Raise if Bing/web_search rate-limit headers
indicate headroom. Per-deployment RPM (gpt-5.4) is generous; the
practical ceiling will be Bing search transaction rate, not LLM RPM.

## Work units

Order roughly by dependency:

1. **Migration `012_company_bios.sql`** — adds the four columns to
   `companies`. Smallest unit; ships first.
2. **Researcher core** — port scratchpad to a pure function:
   `research_company(slug, name) -> CompanyBio` (lives in
   `src/jobbuddy/sync/research.py` alongside the phase, or in a new
   `src/jobbuddy/research.py` if the executor prefers — either works).
   JSON schema, content_filter retry, structured output. Unit-testable
   with a mocked OpenAI client.
3. **`prompts/company-research-v2.txt`** — adapt
   `.claude/agents/company-research-agent.md` per "Prompt Design" above.
   Iterate against the five reference companies. This is where time goes.
4. **JobStore integration** — `save_company_bio(slug, short_bio, long_bio,
   model)` and `find_companies_missing_bio() -> list[Company]`. Tested.
5. **`ResearchPhase`** — extends `WorkerPhase` (`sync/base.py`) following
   `EnrichPhase` shape. Polls for `long_bio IS NULL`, processes via
   thread pool, writes via `WriteQueue`. `PhaseState` updates feed the
   existing Rich Live TUI. Standalone-runnable like other phases.
6. **CLI + sync wiring** — `jsb research-companies [slug] [--force]
   [--stale]` runs `ResearchPhase` standalone with the same display
   the rest of sync uses. Add `research` to the default phase list in
   `jsb sync` so new companies get processed automatically. Phase
   skips with a warning if Azure research deployment isn't configured
   (devs without Azure can still run fetch/enrich).
7. **Backfill run** — execute `jsb research-companies` against prod PG to
   populate all ~600 companies. ~$30, ~30-60 min wallclock at 8 workers.
8. **Cleanup** — remove `prompts/company-research-v1.txt`,
   `scratchpad/foundry_company_research.py`. Update AGENTS.md to mention
   the new phase and CLI command.

Phase 1 owns the extract-prompt update that consumes `companies.long_bio`
— not in this plan's scope.

## Validation

Before claiming this is done, the executor should:

- Run the prompt against the five reference companies and eyeball
  `long_bio` for: factual accuracy (no hallucinated funding rounds /
  partnerships), NPOV stance (no "appeals to engineers who care about
  X"), industry-neutrality (Walgreens and Caliber don't read like tech
  companies), and the hard-exclusion list (no open positions, no
  salaries from current postings)
- Verify `short_bio` cap is honored without truncation that loses the
  load-bearing fact
- Confirm content_filter retry actually triggers on a known-tripping
  query (defense-adjacent terminology) and recovers
- Confirm auto-trigger fires on `save_company()` of a brand-new slug

## Rollback

Migration is additive (only `ADD COLUMN`); rollback is `DROP COLUMN`
across the four new columns. No data dependencies elsewhere yet —
extract integration is downstream of this work and fails gracefully when
`long_bio IS NULL` (Phase 1's responsibility).

## Open question, not blocking

Whether the research call should *also* run when an existing company's
ATS config changes (i.e., `content_hash` shifts). Probably no — the bio
describes the company, not the careers-page integration. Defer until
there's a reason.
