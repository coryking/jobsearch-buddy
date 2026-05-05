# Job Search Redesign — Requirements

**Date:** 2026-05-05
**Status:** Brainstorm complete. Phase 1 ready for planning. Phase 2 deferred.

## Summary

The current MCP search returns thin job rows (company + title + location). The
calling LLM has been doing fuzzy keyword search on titles alone, ignoring the
embedding pipeline because the embedding pipeline can't help when queries are
keyword-shaped. The pipeline is also currently broken (embeddings dropped during
the Azure migration). Embeddings as currently used are not earning their cost.

The redesign reframes the MCP server as **a structured, fact-dense data
provider for an LLM that is itself the ranker**. The calling LLM (Claude
Desktop, ChatGPT) has the user's context; we have comparable, normalized job
and company data that's hard to assemble from one-shot web search. We provide
evidence; the LLM judges fit.

Two phases:

- **Phase 1 — Job-side rebuild plus optional per-company short bios.**
  Replace the embedding-dependent strip pipeline with an extract pipeline
  producing `short_jd`, `description_normalized`, and `salary` from one
  structured-output call. Add a separate, opt-in company-research pipeline
  that produces `short_bio` per company via web-search-grounded LLM. The
  job extract pipeline gracefully handles missing bios (treats absence as
  "no extra context"), so company research can be backfilled gradually,
  selectively, or skipped entirely. Drop job embeddings. Ship.
- **Phase 2 — `find_companies` semantic-search tool (deferred).** Adding
  vibe-based company discovery would need an `embed_text` artifact, pgvector
  for companies, and a new MCP tool. Defer until we see how the calling LLM
  uses Phase 1 search.

## Background and motivation

### What's broken

1. **Embedding pipeline is currently empty in production.** Azure migration
   dropped most embeddings. Search via MCP returns near-empty results for most
   companies.
2. **Even when populated, embeddings under-perform.** The calling LLM doesn't
   issue queries embeddings can match well. It issues structured keyword queries
   like `staff software engineer AND ai`, plus location and company filters,
   then makes ranking decisions from the returned titles alone.
3. **Returned rows lack signal.** A row with `company + title + location` does
   not contain enough information for the LLM to decide whether to surface a
   job. It re-fetches the full JD or guesses from the title.
4. **The strip-for-embedding pipeline serves no consumer.** Stripped text is
   never shown to anyone; it only feeds an embedding that isn't being matched
   well anyway. The LLM call producing it is a fixed per-job cost with no
   downstream payoff in the current architecture.

### Reframe

The MCP server is a data provider for an LLM that ranks. Two consequences:

- **Nothing we return is shown directly to the user.** Outputs go to a calling
  LLM that filters and rephrases for the user. Optimize for information density,
  not human readability.
- **The calling LLM is part of the ranking algorithm.** Our job is to give it
  evidence dense and comparable enough that it can confidently include or
  exclude jobs from what it surfaces. Our competition is `web_search` +
  `web_fetch`. Our edge is structured, normalized, comparable data across many
  companies that one-shot web search cannot easily assemble.

### NPOV — neutral point of view

Nothing we generate should encode judgment. The calling LLM has more context
about the user than we do. It should make the compatibility judgment, not us.

The same reality one seeker calls "chaotic" another calls "scrappy". Both
seekers should find this job through their query. Surface specific facts that
imply the vibe; don't name the vibe. This applies to both the JD outputs and
the company outputs.

This is the stance already established in `prompts/embed-text-v1.txt`. We
extend it to the new outputs.

---

## Phase 1 — Job-side rebuild plus per-company short bios

### Goals

- Restore working search end-to-end
- Give the calling LLM enough per-job signal to filter without semantic search
- Make per-company `short_bio` *available* — generated on demand, opt-in,
  cost-bounded — so the calling LLM has consistent vibe context for the
  companies the user actually searches over
- Simplify the codebase by removing job embeddings
- One LLM call per job (3 structured outputs); one LLM call per company
  when researched (1 structured output, with `web_search` tool)

### Schema changes

Three text fields per job:

| Field | Source | Purpose |
|---|---|---|
| `description` | ATS, raw, untouched | Audit / re-extraction source |
| `description_normalized` | New extract phase | Returned by `get_job_post_details` — boilerplate-stripped, substance-preserved |
| `short_jd` | New extract phase | Returned inline by `search_jobs` — 2-3 sentence factual capsule |

Two new fields on `companies`:

| Field | Source | Purpose |
|---|---|---|
| `short_bio` | New company-research pipeline | Returned by `get_company`, optionally inlined in `search_jobs` rows, fed as context into the job extract prompt |
| `research_updated_at` | New company-research pipeline | Tracks when bio was last refreshed; enables refresh policy without touching the prompt |

Migrations:

- Add `short_jd TEXT` to `jobs`
- Rename `description_stripped` → `description_normalized` (or new column,
  re-populate via extract phase; existing data is regenerable)
- Add `short_bio TEXT` and `research_updated_at TIMESTAMPTZ` to `companies`
- Drop `job_embeddings` table, its HNSW index, embedding-extraction migration
  scaffolding (007a–007f), and `query_embeddings_cache` (010)
- Update `jobs_fts_vector` (009) to index over
  `title + short_jd + description_normalized` instead of `description_stripped`

`pgvector` extension stays installed (cheap, harmless) in case Phase 2 ever
ships. No Phase 1 tables depend on it.

We do NOT add `embed_text` to companies in Phase 1. YAGNI — we don't yet
know what form it should take, we don't yet have `find_companies`, and the
short_bio alone covers Phase 1's needs. Phase 2 can add `embed_text` and
backfill via the same researcher pipeline if/when it ships.

### Pipeline changes

Two pipelines:

#### Job extract pipeline

Replaces `sync/strip.py` with `sync/extract.py`. Same `WorkerPhase` shape —
DB poll, threadpool workers, write queue. Difference is the prompt and the
structured output.

Per-job: one LLM call, structured output (`response_format=json_schema`):

```json
{
  "short_jd": "string (~2-3 sentences)",
  "description_normalized": "string (the JD with boilerplate removed)",
  "salary": "string or null"
}
```

Inputs to the prompt (passed as user-message XML tags):

- `<title>`, `<company>`, `<location>` — basic metadata
- `<ats_provided_salary>true|false</ats_provided_salary>` — drives whether
  salary extraction runs (true = skip, ATS field is authoritative)
- `<company_bio>` — the company's `short_bio` from the registry, may be
  empty if the company hasn't been researched yet (the prompt is told to
  treat empty as "you have no extra context"). The prompt forbids
  restating any of this in the output.
- `<job_description>` — the raw posting

Salary fill rate in production is 3.5%, so `<ats_provided_salary>` is false
for the vast majority of jobs and the LLM extraction path is the dominant
one. The skip branch is correct but rarely fires.

Failure mode: if extraction fails for any reason, all three fields stay null
and the job is reattempted on next sync. Same retry pattern as the existing
strip phase.

The full prompt lives at `prompts/extract-v1.txt`. See "Prompts" section
below.

#### Company research pipeline

New: `sync/research.py` (or a separate `jsb research-companies` CLI
command — naming is a planning concern). Single-output structured response
via Azure Responses API with `web_search` tool:

```json
{ "short_bio": "string (60-100 words, NPOV, fact-dense)" }
```

Inputs:

- `<name>` — the company's display name
- `<careers_url>` — optional, when known from the registry

**The pipeline is opt-in.** Job extract works without it — the extract
prompt is told that empty `<company_bio>` means "you have no extra
context." Bios are an enhancement, not a prerequisite. This hedges the
cost: the user can run research selectively (only the companies they
search most), in batches, or never.

Trigger semantics (all manual, all opt-in):

- **Backfill** — `jsb research-companies` (CLI command) runs research
  for all unresearched companies, or a filtered subset (`--company X`,
  `--limit N`). Estimated cost per company: $0.10–$0.25 per the
  prototype, depending on how aggressively the model searches. Full
  backfill of ~700 companies = ~$70–$175.
- **On-add** — when `jsb companies-add` registers a new company, the
  user can pass `--research` to trigger research immediately, or omit
  it and let the company sit bio-less until a future backfill.
- **Refresh** — periodic or per-company re-runs based on
  `research_updated_at`. Cadence is a planning concern; default is
  "no automatic refresh — user runs `jsb research-companies --stale 90d`
  when they want one."

Cost framing: bios are the difference between "calling LLM scans 30
search results that include vibe context" vs. "calling LLM has to fetch
each company via web_search itself, paying per-query in its own
context." If the user searches the same companies repeatedly, the bio
cost amortizes. If they search broadly across all 700 companies once,
it might not be worth it. Opting in selectively keeps the call open.

Empirically validated by the parallel session in `scratchpad/runs/` —
`gpt-5-mini` with `web_search` tool produces grounded, NPOV-compliant
bios at ~$0.10/company including Bing transactions. Quality is good but
the prompt has a known "meta-line bug" where the model occasionally ends
the bio with self-reflection about what queries it'll match. The prompt
in `prompts/company-research-v1.txt` includes an explicit instruction
against this.

The full prompt lives at `prompts/company-research-v1.txt`. See "Prompts"
section below.

### MCP tool surface changes

`search_jobs`:
- Existing filters preserved: `title`, `location`, `company`
- Add: `posted_since` (string like `"7d"`, `"2w"`, or ISO date)
- Add: `query` (free-text FTS over `title + short_jd + description_normalized`)
- Add: `include_company_bio` (bool, default false) — when true, each row
  includes the company's `short_bio` inline. Default off to keep tokens
  manageable; the calling LLM flips it on when vibe context matters.
- Returned rows include `short_jd` inline alongside existing fields
- Drop: any embedding-distance ranking, `similarity` column

`get_job_post_details`:
- Returns `description_normalized` as the description, not raw
- Other fields unchanged (`salary`, `apply_url`, `published_at`, etc.)

`get_company(slug)`:
- New tool. Returns the company's `short_bio` plus basic metadata
  (slug, name, ATS, board if relevant). Lets the calling LLM fetch
  vibe context for a single company without paying the inline-bio cost
  on every search row.

`find_companies` (semantic search by vibe) is Phase 2 only.
`lookup_by_name` and `companies` are unchanged.

### Tool description guidance (planning concern, but called out)

The MCP tool descriptions need updating to reflect the new behavior. The
`search_jobs` description should signal to the calling LLM that:

- Returned rows include enough context (`short_jd`, salary, location, posted
  date) to filter without re-fetching
- `query` does fuzzy text matching across title + short_jd + normalized JD —
  not embedding search, not exact match
- `posted_since` exists and is the right way to ask for fresh roles

This is real prompt-engineering work for the tool-descriptions and is deferred
to planning, not specified here.

### Removed work

- Strip phase (`sync/strip.py`) and its prompt (`eval/prompts/v9-surgical-benefits.txt`)
- Embed phase (`sync/embed.py`)
- Embedding generation CLI (`cli/generate_embed_text.py`)
- `embeddings.py` module — `text-embedding-3-small` integration
- `search.py` module — `VectorSearch` class
- Eval harness for strip prompts (`eval/`) — still useful as a pattern, but
  the strip-prompt-specific content is dead. Repurposable for the new extract
  prompt eval if we want.
- Migrations 007a–007f, 010 (write a single rollback migration; do not edit
  existing migrations)

Do not yet remove the `pgvector` extension or migration 004 (`companies`
table) — Phase 2 may use both.

### Eval

The existing eval harness (`eval/`) was structured around scoring a single
strip output. It generalizes to the new three-field extract output by scoring
each output independently:

- `short_jd`: graded on information density, NPOV adherence, factual fidelity
  to the source JD, length compliance
- `description_normalized`: graded similar to current strip eval — recall
  (differentiating content kept), precision (boilerplate removed), integrity
  (no fabrication), fidelity (surface form preserved)
- `salary`: graded for accuracy when present in the JD, correct null when not

Reuse the harness shape; rewrite the judge prompt for the new rubric. Detail
deferred to planning.


## Prompts

Both Phase 1 prompts live in the repo as standalone artifacts. They are
part of the product spec — the stance and epistemological framing belong
to this document, but the prompt text itself ships in version-controlled
files so the pipelines can read them at runtime and so iteration produces
clean diffs.

### `prompts/extract-v1.txt`

Drives the job extract phase. Inputs (XML tags in user message): `title`,
`company`, `location`, `ats_provided_salary` (bool flag), `company_bio`
(may be empty), `job_description`. Output: structured JSON
`{short_jd, description_normalized, salary}`.

Responsible for:

- NPOV stance: facts not verdicts; the calling LLM does fit-judging.
- The "specifics not archetypes" principle for `short_jd` — the title
  already says "Software Engineer," so the short_jd must not say "writes
  software." Cross-vertical examples (Databricks Sr. Solutions Engineer,
  Wegmans Dishwasher) anchor what good output looks like across collar
  lines.
- Hard-gate enumeration for the keep/remove split in
  `description_normalized`, including drug test / piss test as a hard
  gate (not legal boilerplate).
- Salary handling controlled by the `<ats_provided_salary>` flag — true
  means skip extraction, false means scan the JD body.
- Explicit instruction to use `<company_bio>` as situational context but
  never restate it in any output field.

Mechanical specs for the planner:

- JSON schema with three required fields, `salary` nullable
- `short_jd`: target ~40-80 words, hard cap ~120
- `description_normalized`: no fixed cap; must always be shorter than input
- Recommended model: `gpt-5-nano` (per existing strip-eval winner). Re-eval
  with the new prompt during planning.

### `prompts/company-research-v1.txt`

Drives the company research pipeline via Azure Responses API with
`web_search` tool enabled. Inputs (XML tags): `name`, `careers_url`
(optional). Output: structured JSON `{short_bio}`.

Responsible for:

- NPOV stance; banned-phrasings list (no "fast-paced", "scrappy",
  "world-class", etc.); allowed-facts list (founding year, employee count
  with date, funding history with dates, regulatory regime, recent events).
- Explicit instruction against the "meta-line bug" observed in the
  prototype (no trailing self-commentary like "These facts make this a
  good fit for seekers who...").
- Research guidance: 2-3 searches typical, don't pad. Worked-example pair
  showing bad (verdict-laden) vs. good (fact-dense) short_bio for the
  same company.
- Contradiction handling: prefer recent + authoritative sources, date-stamp
  figures, fall back to vaguer phrasing if irreconcilable, never invent.

Mechanical specs for the planner:

- JSON schema with one required string field
- `short_bio`: target 60-100 words, hard cap ~130
- Recommended model: `gpt-5-mini` with `web_search` tool — empirically
  validated at ~$0.10/company on the prototype runs in `scratchpad/runs/`
  ($14/1K Bing transactions + token costs)
- `gpt-5-nano` likely under-powered for this synthesis; evaluate during
  planning if cost becomes an issue
- Failure mode: if web_search returns thin information, the prompt is told
  to produce a shorter bio with verified facts rather than padding with
  speculation. Test in eval.

---

## Phase 2 — `find_companies` semantic search (deferred)

Generating an `embed_text` artifact per company and exposing a
`find_companies(query)` tool would unlock vibe-based decomposition — the
calling LLM could ask "AI-as-product startups in the Bay Area" and get a
list of company slugs to scope `search_jobs` to.

Why deferred:

- We don't yet know what form `embed_text` should take (length, structure,
  whether to include vibe vocabulary or stay strictly factual). YAGNI
  applies until we have a concrete reason to commit.
- We don't yet know whether the calling LLM will reach for two-step
  decomposition even if we build it.
- Phase 1 alone may close most of the gap. The `short_bio` plus the
  `include_company_bio` toggle on `search_jobs` lets the calling LLM read
  vibe context inline; whether it needs *search* over that vibe is the
  open question.

If/when Phase 2 proceeds:

- Extend the company-research prompt to also emit `embed_text`. The
  artifact is already on disk with `short_bio`; embed_text generation is
  additive — same pipeline, additional output field.
- Add `embed_text` and `embed_text_updated_at` columns to `companies`
- Add a `company_embeddings` table with HNSW index in pgvector
- Build `find_companies` MCP tool with semantic search (and possibly
  structured filters layered on top, though we explicitly chose not to
  build those in Phase 1)
- Wire query embedding via the same pattern previously used for jobs

Cost is already validated: ~$60-85 one-time for full backfill across 600
companies. The marginal cost to add `embed_text` once Phase 2 commits is
small since the model has already done the research; embedding cost is
negligible.

---

## Open questions deferred to planning

- **Tool description prompts.** Updating `mcp_server.py` tool descriptions
  to push the calling LLM toward two-step decomposition (when Phase 2
  exists) and toward using `query` + `posted_since` (always). Real
  prompt-engineering work; cannot be specified abstractly.
- **Migration strategy for existing data.** Whether to drop
  `description_stripped` outright or keep the column and let extract phase
  re-populate (renamed). Affects rollout — clean cut means search is
  empty until extract phase has run; in-place rename means partial.
- **Eval harness reshape.** Current eval scores a single strip output.
  Three-output scoring needs new judge prompt and probably a new score
  CSV shape.
- **`get_job_post_details` for un-extracted jobs.** What does the tool
  return for a job that hasn't been through the new extract phase yet?
  Probably the raw `description` as a fallback, with a marker; sequencing
  question.
- **Pagination / token budget for `search_jobs`.** A query like "SWE in
  SF" might match 800 jobs. Default limit, cursor shape, ranking when
  no `query` is given.
- **SERP behavior when `query` is empty.** Default ordering: by
  `published_at` desc? By company alphabetical? Decide.
- **Refresh policy for `short_bio`.** `research_updated_at` is in the
  schema but the cadence (quarterly? per-company-significance weighted?
  triggered by detected changes?) is a planning concern.
- **Sibling-JD context for the extract phase.** Including 2-3 other JDs
  from the same company in the prompt, with the framing "here are siblings —
  use them to identify what's distinctive about this posting and what's
  shared boilerplate." Sharper differentiation in `short_jd`, better
  boilerplate detection in `description_normalized`. Real complexity
  (DB fetch, more tokens per call, slower throughput). Phase 1.5 if Phase 1
  short_jds are too generic. Don't build it yet.
- **Salary fill rate is 3.5%.** Most jobs need LLM extraction. The
  `ats_provided_salary` skip-branch is correct but rarely fires.
- **Company-bucket / saved-list feature.** User-mentioned during
  brainstorm. Out of scope for both phases. Track separately.
- **Adding non-tech companies to the registry.** Out of scope. Track
  separately. Phase 1 design is domain-neutral so this lift becomes
  smaller when the time comes.

## What we explicitly chose not to do

- **Per-job seniority enum.** Title carries it. Adding a field is
  speculative complexity.
- **Per-job remote_policy enum.** Worksite info goes in `short_jd` as
  prose. The enum was an attribute framed as a positive, doesn't generalize
  past white-collar tech, and JDs lie about it.
- **Per-company structured tags (industry, size_bucket, maturity).** The
  calling LLM can infer these from `short_bio` prose. Pre-structuring is
  speculative complexity.
- **Hybrid keyword/structured filter on jobs (e.g., min_salary).** Salary
  stays free-text. Hard-search by salary is rarely the right move; a
  visible salary string lets the LLM and the user judge.
- **Per-job `tech_tags` array.** Tech surface is implied by `short_jd`.
- **A separate full-JD-normalizer prompt.** Folded into the single extract
  prompt as one of three structured outputs.
- **Saving any of the embedding-era infrastructure for reuse.** Drop it
  cleanly; pgvector extension stays installed but its tables don't.
- **Generating `embed_text` for companies in Phase 1.** YAGNI — we don't
  yet know what form embed_text should take, and we don't have the
  consumer (`find_companies`) to validate against. Phase 2 adds it as an
  additional output of the same researcher pipeline if/when it ships.
