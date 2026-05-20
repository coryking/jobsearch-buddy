# State of jsb — 2026-05-20

Rewritten by each dream run, not appended. Run 6.

## TL;DR

- **Corpus: stable.** 99,331 active jobs, 0 distill backlog, 100% bio coverage.
- **Tesla orphan backlog unchanged.** PR #69 (orphan cleanup) still unmerged. 5,911 jobs with `description=NULL` continue to appear in search results and enrich backlog.
- **New sync finding: 7 companies with active jobs are 404-ing.** This is a scraper regression class — not dead configs, but boards where the slug changed or ATS migrated after the corpus was populated. Their jobs will never re-fetch and will eventually go stale.
- **cc-explorer unavailable in this terminal environment.** Can't confirm operator's session focus this run. Prior session signal (run 5): operator in job-application mode, focused on search quality + `get_application_form`.
- **PRs #68/#69 still open, zero engagement (now 1 day old).** PRs #65/#67 unchanged.
- **What to look at first:** Merge PR #69 (removes 5,911 dead jobs from search). Investigate the active-jobs-with-404 companies (below) — their existing corpus entries will drift stale.

---

## Corpus health

| Metric | Run 6 (today) | Run 5 |
|---|---|---|
| Active jobs | 99,331 | 99,499 |
| Distill backlog | 0 | 0 |
| Enrich backlog (active, description IS NULL) | 5,919 | 5,920 |
| → Tesla orphans | 5,911 | 5,911 |
| → Non-Tesla | 8 | ~9 |
| Companies with ats | 645 | 694 |
| Companies missing bio | 0 | 0 |

PR #69 is filed but unmerged. Until it lands, the Tesla enrich backlog persists and those 5,911 jobs appear in search results with no description.

## Sync health

32 companies in error state. New breakdown distinguishing two error classes:

### Class A: Active-jobs-with-404 (scraper regression)

Companies that successfully populated the corpus but are now returning 404 — board slug changed or ATS migrated. Their existing job rows accumulate as unfetchable stale listings.

| Company | ATS | Board slug | Active jobs | Notes |
|---|---|---|---|---|
| Mistral AI | Ashby | mistral | 178 | — |
| Coinbase | Greenhouse | coinbase | 101 | — |
| Airwallex | Ashby | airwallex | 612 | Timeout (not 404; may be transient) |
| Snowflake | Ashby | snowflake | 417 | Timeout |
| Skydio | Ashby | skydio | 110 | Timeout |
| Replit | Ashby | replit | 79 | Timeout |
| Thumbtack | Greenhouse | thumbtack | 35 | — |
| Runway | Greenhouse | runwayml | 35 | — |
| Wordware | Ashby | wordware.ai | 6 | — |
| Synchron | Greenhouse | synchron | 3 | — |
| Continua AI | Ashby | continua | 2 | — |

Timeouts (Airwallex, Snowflake, Skydio, Replit) may be transient network failures — their boards may still be valid. The 404s are more definitive.

### Class B: Dead configs (no active jobs, 404)

Config entries that never populated or whose jobs were all removed. Same as prior runs. Candidate for disable/delete per issue #42.

~16 companies: Adept AI, Cal.com, EvenUp, Fly.io, Groq, monday.com, Replicate, Tinybird, Turso, Flywire (board=**flywire2**, obviously wrong slug), Hebbia, Moment, Persona, WellSaid Labs, Weights & Biases, Test Corp (testcorp, looks like a test entry).

### Class C: Persistent 403s

| Company | ATS | Note |
|---|---|---|
| Qualcomm | Eightfold | Persistent. Not a slug issue. |
| Tesla | Custom | 403 on new fetches; orphan jobs via PR #69. |
| MX | Workday | Persistent. |

### Other

- **NetApp** (eightfold_v2): JSON parse error "Expecting value: line 2 column 1 (char 2)". Not a 404 or 403 — either empty response or HTML error page. 0 active jobs. May need investigation.

## Open dream PRs

| PR | Description | Status |
|---|---|---|
| #65 | Uber faucet config | Open, 0 comments, stale (not operator's current target) |
| #67 | Taleo listing parser fixes | Open, 0 comments, stale |
| #68 | dream.md: cc-explorer mandatory | Open, 0 comments (filed 2026-05-19) |
| #69 | Migration: remove orphan Tesla jobs | Open, 0 comments (filed 2026-05-19) |

## Open-question shapes pending operator judgment

- **Active-jobs-with-404**: which companies had slug/ATS migrations vs. which are genuinely broken? Investigating current board URLs for Coinbase, Mistral, Runway, Thumbtack would distinguish.
- **Dead slugs (Class B)**: fix slug (e.g. flywire2 → flywire), disable via #42 flag, or delete row?
- `sync_status` history retention vs. last-attempt-wins (can't detect "failing for N days")
- Per-distill telemetry (no cost tracking in DB)
