---
title: Phase 2 Company-Bio Pipeline — Empirical Experiments
type: brainstorm
status: paused — out of scope for Phase 1 redesign
date: 2026-05-05
origin: docs/brainstorms/2026-05-05-job-search-redesign-requirements.md
---

# Phase 2 Company-Bio Pipeline — Empirical Experiments

## Why this exists

This doc captures what we learned while exploring the Phase 2 company-bio
pipeline (Azure Responses API + `web_search` + structured output). The
exploration was scoped to "build a stupid script that can produce one bio,"
ran longer than expected, and uncovered prompt-design problems that imply
a real redesign of `prompts/company-research-v1.txt`.

That redesign is **out of scope for the Phase 1 redesign branch** (job-side
rebuild). This doc is the durable handoff so it can be picked up later
without re-deriving the findings.

## What we built (working, on disk)

A scratchpad-only test harness:

- `scratchpad/foundry_company_research.py` (gitignored) — runs one company
  through Azure Responses API + `web_search` + structured-output JSON schema,
  saves human-readable markdown + raw JSON to `scratchpad/runs/{ts}_{model}_{slug}.{md,json}`
- `prompts/company-research-v1.txt` — canonical prompt, used as system message
- Output schema: `{short_bio: string}` (60-100 words target)

The script is gitignored because it's exploratory. If picked up later, either
move it into `src/jobbuddy/` as a real phase or keep it scratchpad-side.

## What we learned

### Architecture: confirmed

The brainstorm doc's call holds. Single `responses.create` call with
`tools=[{"type": "web_search"}]` produces a grounded bio in one shot. The
model decides how many searches to run (usually 1-4), plans them, and
synthesizes. **No agent loop is needed.** No `azure-ai-projects` SDK, no
Foundry project, no Bing resource provisioning — vanilla Azure OpenAI
deployment with the OpenAI-compatible v1 surface (`/openai/v1/responses`)
and AAD bearer-token auth.

### Cost: lower than the brainstorm estimated

Real numbers, ground-truth verified:

- **$14 per 1,000 web_search transactions** (not $35, not $15 — official
  Microsoft Bing Grounding pricing). Same rate across `web_search` (Responses
  API) and `WebSearchTool` / `BingGroundingTool` (Foundry Agents) — they
  route to the same Bing backend.
- Default `web_search` returns Bing **SERP snippets only**, not full page
  fetch. Only `o3-deep-research` does `open_page` / `find_in_page`. Snippets
  proved sufficient for company-research questions in our small sample.
- gpt-5.4 + canonical prompt: ~2-4 searches per company on average
- Token cost: ~$0.02-0.04 per company on gpt-5-mini, ~$0.05-0.10 on gpt-5/gpt-5.4

| Model | Per-company total | 694 companies one-time | Quarterly refresh/yr |
|---|---|---|---|
| gpt-5-mini | ~$0.10 | ~$70 | ~$280 |
| gpt-5 | ~$0.20 | ~$140 | ~$560 |
| gpt-5.4 + canonical prompt | ~$0.05 | **~$35** | **~$140** |

Cheaper-than-budget enough that cost is not a constraint.

### Content filter: real concern, solvable

Default Azure RAI filter (`Microsoft.DefaultV2`) blocks ~5-10% of company
research at low/medium severity — defense-adjacent terminology and AI-safety
discussions both trip it. Symptoms: response returns with
`status: "incomplete"` and `incomplete_details: {reason: "content_filter"}`.

**Resolution applied to the gpt-5.4 deployment:** swapped to the existing
`no-filter` custom RAI policy on the resource (severityThreshold=High, action=NONE
for all categories). Done via:

```bash
az rest --method patch \
  --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$ACCT/deployments/gpt-5.4?api-version=2024-10-01" \
  --body '{"properties":{"raiPolicyName":"no-filter"}}'
```

The `--rai-policy-name` flag is missing from `az cognitiveservices account
deployment create`; PATCH via REST is the workaround.

A retry path is still worth wiring into the production phase as cheap
insurance — content_filter rejections at "High" severity are still possible.

### Model selection: gpt-5.4 + no-filter is the current pick

Tested gpt-5-mini, gpt-5, and gpt-5.4 against five reference companies.
gpt-5.4 produces tighter bios with fewer searches (better stopping behavior),
and the latest non-codex/chat variant. gpt-5-mini also works at lower cost
but missed more of the load-bearing facts.

`gpt-5.4-mini` (March 2026) is also available and untested — likely the
best price/quality point. Worth a 5-company sweep when picking this back up.

## What broke: prompt design problems

### Empirical failures vs. ground truth

Tested five companies spanning the spectrum (AI lab, defense unicorn, defense
seed-stage YC startup, regulated retail Fortune 50, services trades chain).
Comparing canonical-prompt output to web-researched ground truth:

| Company | Current bio failure |
|---|---|
| Anthropic | Missed Series G ($30B Feb 2026), $5B follow (Apr 2026), $380B post-money, role mix (35% research) |
| Anduril | Missed ITAR / U.S. Person requirement as company-level fact, $20B Army Lattice contract Mar 2026 |
| 9-mothers | Bio included hiring-area discipline list and salary range — duplicates what `search_jobs` rows already show |
| **Walgreens** | Said "Walgreens Boots Alliance" (wrong post-Aug 2025; now five-company split). Missed: DEA $300M opioid settlement w/ 7-yr compliance MOA, 1,200-store closure plan, CEO change Aug 2025, partial UFCW unionization |
| **Caliber** | Said "automotive repair" (wrong; collision-only). Missed: flat-rate pay structure (the defining work fact for techs), I-CAR Gold Class certification, ownership stack (H&F majority + Leonard Green + OMERS), founding 1997, OSHA isocyanate exposure |

The non-tech ones suffered worst. The current prompt has no muscle for
*what defines the work* in a regulated retailer or a trades chain.

### Structural problems with `prompts/company-research-v1.txt`

1. **Allowed-facts list is tech-shaped.** It calls out "engineering blog
   cadence", "OSS repos with star counts", "Discord". Doesn't surface I-CAR
   / ASE certification, flat-rate vs hourly comp structure, state pharmacy
   licensing, OSHA chemical exposure, union status patterns. These are
   equally factual — the prompt just doesn't see them.

2. **Treats hiring posture as in-scope.** The MCP returns the bio alongside
   live job listings. Hiring-area lists, current salary ranges, office
   locations the JDs themselves disclose are duplicative noise. The bio
   should never restate what the search-result row already shows.

3. **Doesn't elevate workplace-defining facts.** A bio is more useful when
   it surfaces the few facts that *define what working there is like* —
   Caliber's flat-rate pay, Walgreens' DEA MOA, Anduril's ITAR — over
   generic facts like HQ city or founding year. Current prompt treats them
   as equal.

4. **Examples anchor only on Anthropic.** Easiest possible case (well-known
   tech, public funding, named founders). Doesn't transfer to trades or
   regulated retail.

5. **Doesn't handle declining/restructuring companies.** Growing AI lab vs.
   contracting Fortune-50 retailer need different fact emphases. The prompt
   is silent on this.

## Existing resource we should leverage

`docs/company-profiles/` already contains 8 hand-researched profiles
(Anduril, Bank of America, Cloudflare, Duolingo, PolyAI, Stripe, Target,
Walgreens). They're the right shape for what the bio should distill —
dense, factual, NPOV, span founding/ownership/regulatory/workplace
conditions/trajectory.

Two implications:

- **Use 3-4 of these as in-prompt examples** (distilled to 80-word bios)
  to anchor the model on cross-industry density, not Anthropic alone.
- **Consider a two-mode pipeline**: when a hand-researched profile exists
  for a company, the LLM call is a cheap "summarize this profile to 80
  words" pass (no `web_search`). When it doesn't, fall back to `web_search`
  grounding. The brainstorm noted this changes Phase 2 economics
  significantly — synthesis from a profile is much cheaper than web research.

## Redesign principles (for v2)

1. **Lead with consumption context.** First paragraph of system prompt:
   "This bio is returned alongside live job listings (which carry company,
   title, short_jd, salary, location, posted-since). Do not restate what
   the row already shows. Do not list what they're hiring for."

2. **Replace tech-leaning allowed-facts list with industry-neutral
   categories**, each with cross-industry examples:
   - **Identity**: what they make, primary customer/market
   - **Ownership & maturity**: public + ticker, PE-owned + when, founder-controlled,
     taken-private + date, going-public path if material
   - **Regulatory regime**: DEA / HIPAA / ITAR / OSHA / FAA / DOT / state-licensing
   - **Workplace-defining structure**: comp shape (flat-rate vs hourly vs
     salary vs commission), union status, certification ladder
     (I-CAR, ASE, AIA, PharmD, PE), shift-coded work
   - **Recent material events**: acquisitions, layoffs (% + date), location
     closures, regulatory enforcement, leadership change, going-private/public
   - **Founding context**: year, founders' prior work *if material*

3. **Hard exclusions** (explicit list): What they're hiring for. Open
   positions. Salary ranges from current job postings. Office locations the
   JDs themselves disclose. Engineering blog cadence (this is recruiting
   marketing, not company identity). Glassdoor numeric ratings.

4. **Multi-industry examples in the prompt itself.** Anthropic + Walgreens
   + Caliber + Anduril side-by-side, each as an 80-word distillation of the
   corresponding hand-researched profile. Show the model how density and
   fact-elevation shift with industry.

5. **Keep what works in v1**: NPOV stance, banned-phrases list, source-
   contradiction handling ("when sources contradict, prefer recent +
   authoritative + date-stamp; if can't resolve, use vaguer phrasing"),
   "say less when info is thin," meta-line ban (lines 78-82).

## Pickup checklist

When this gets resumed (post-Phase-1):

- [ ] Read this doc + `docs/brainstorms/2026-05-05-job-search-redesign-requirements.md`
      Phase 2 section
- [ ] Look at `scratchpad/runs/` for the empirical evidence (gitignored;
      regenerate by running the script if needed)
- [ ] Decide whether to do the two-mode pipeline (profile-summary vs
      web_search) or web_search only
- [ ] Distill 3-4 of the `docs/company-profiles/*.md` files into 80-word
      example bios — these become in-prompt examples
- [ ] Draft `prompts/company-research-v2.txt` per the redesign principles
- [ ] Run the test harness on the same 5 reference companies (Anthropic,
      Anduril, 9-mothers, Walgreens, Caliber Collision) — picked for industry spread
- [ ] Compare v2 to v1 outputs against the empirical-failures table above
- [ ] If `gpt-5.4-mini` exists at the time, run a price/quality sweep on
      that variant
- [ ] Wire content_filter retry path into the production phase regardless

## Infrastructure handoff facts

- gpt-5.4 deployment exists in the project's Azure OpenAI resource at
  capacity 250 (KTPM), with `no-filter` RAI policy attached
- Auth pattern: `OpenAI(base_url="$ENDPOINT/openai/v1/", api_key=token_provider)`
  where `token_provider = get_bearer_token_provider(DefaultAzureCredential(),
  "https://cognitiveservices.azure.com/.default")` — same managed-identity
  path used in production for strip/embed
- `web_search` tool is invoked via `tools=[{"type": "web_search"}]` on the
  Responses API. To audit which URLs Bing returned per search, pass
  `include=["web_search_call.action.sources"]` in the request

## What we explicitly chose NOT to do

- Build a custom agent loop (the Responses API runs the loop internally)
- Deploy `o3-deep-research` (minutes per query; rejected for synchronous use)
- Switch search backends to Tavily / Brave / Serper (Bing is cheap enough
  at $14/1K and is first-party in Foundry; switching adds custom-tool
  plumbing for marginal savings)
- Generate `embed_text` alongside `short_bio` in this prompt (the brainstorm
  doc's two-output design was rolled back to single-output `short_bio`
  in `prompts/company-research-v1.txt`; that remains the right call)
