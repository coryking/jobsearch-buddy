# Issue #40 — `published_at` backfill: handoff for completion

**Status:** Step 1 (sync enrich) executed for all stub-fetcher tenants. Two parser-variant fixes were required mid-run (see "Update 2026-05-06" below) and are now landed. Steps 2 (Avature sitemap backfill) and 3 (NOT NULL migration) still pending.

## Current state of active rows missing `published_at`

| Company | ATS | Active missing pubdate | Path to fix |
|---|---|---:|---|
| tesla | (excluded, ats=NULL) | 5911 | Out of scope per operator decision; do not include in this issue |
| bloomberg | avature | 501 | Step 2 (sitemap backfill script) |
| zoom | workday | 131 | Step 2 fallback (`last_seen::date`) — Workday's `enrichment_fills=(description,)` so enrich won't fill it |
| broad-institute | avature | 41 | Step 2 (sitemap backfill script) |
| northropgrumman / intel / salesforce / singlestore / nvidia / hp | workday/eightfold/greenhouse | 1–4 each | Step 2 fallback |

Excluding Tesla, **~681 active rows** still need `published_at`. The migration's `last_seen::date` UPDATE will catch all of them as a safety net even if Step 2 is skipped — but Step 2 recovers ~543 *real* dates from Avature sitemaps that scrape-date can't.

## Update 2026-05-06: parser-variant fixes during step 1 execution

When Step 1 was first run, the enrich phase reported "924 items, 13 done, 0 errors" and exited — looking like it had completed cleanly. Investigation revealed the phase was silently terminating because most fetchers were returning empty payloads (no exception, just `{}`), and the display counter only advanced on non-empty payloads, so the run *appeared* complete while real progress was zero. Five distinct parser bugs were fixed:

| Tenant shape | Cause | Fix | Commits |
|---|---|---|---|
| **TalentBrew JSON-LD** (Boeing) | JSON-LD `description` field embeds raw tabs/newlines without escaping; strict `json.loads` rejects | `strict=False` | `8135dcd` |
| **TalentBrew microdata** (Amtrak) | No JSON-LD at all; uses `<meta itemprop="datePosted">` + `<span itemprop="description">` schema.org HTML microdata | Added microdata fallback parser | `8135dcd` |
| **TalentBrew v3 HTML** (Disney) | No JSON-LD or microdata; description in `<div class="ats-description">`, posted date in visible "Date posted: Apr. 24, 2026" label | Added v3 fallback parser; `dateutil` for the visible date variants | `8135dcd`, `2cc6351`, `bf3f321` |
| **SuccessFactors span variant** (Amtrak / EY / Gulfstream / Paccar) | Existing extractor only matched `<div class="jobdescription">`; these tenants ship `<span class="jobdescription">` | Added span fallback after the div path | `ab6d301` |
| **SmartRecruiters layout abuse** (Canva) | Canva stuffs the entire job ad — role responsibilities, qualifications, all of it — into `companyDescription` and leaves the dedicated sections empty. Existing parser intentionally skipped `companyDescription` to avoid boilerplate pollution | Use `companyDescription` only as a last-resort fallback when the three standard sections are all empty | `200aee5` |

Plus: hand-rolled date regexes/strptime in `talentbrew.py` and `successfactors.py` were replaced with `dateutil.parser` (`bf3f321`). `python-dateutil` is now a direct dependency.

## Tooling improvements that came out of this debug session

These aren't issue #40 deliverables, but they prevent the same class of silent-failure bug going forward and are worth knowing about:

- **404 → `listing_status = 'removed'`** (`86567dc`). When an ATS detail page 404s, the row was previously logged WARNING and re-enriched on every subsequent run. Now it's marked removed (with `removed_at` set by the existing trigger) and logged INFO once. Stops permanent re-fetch loops on dead URLs.
- **Per-job outcome instrumentation** (`8135dcd`, `cdce104`). `fetch_enrichments` now emits exactly one outcome per job (`filled` / `filled_partial` / `empty` / `gone` / `error`), each with a callback. `outcome=empty` (request succeeded, parser extracted nothing) logs WARNING — silent parse failures are now loud.
- **MDC-style log context** (`cdce104`). `jobbuddy.logctx.bind(company=..., ats=..., job_id=..., url=...)` sets context-local vars that flow into every LogRecord via a factory. Format string prefixes `[disney/talentbrew]` and suffixes `(job=… url=…)`. Net effect: a parse failure now shows you the exact URL to curl, without each log call having to remember to pass it.
- **Per-company DB-state delta in phase summary** (`bac7560`). Phase end logs `pending=N→M advanced=N-M` per slug, querying the same `get_jobs_needing_enrichment` predicate that drove `count_remaining`. If the parser-side `filled` counter ever disagrees with the actual DB advancement, the parser is lying and rows are looping — now visible at a glance.

The combined effect: when Step 2's script runs (or any future enrich variant breaks), the operator gets immediate, specific feedback rather than a clean-looking "phase complete" log that hides zero progress.

## What's already shipped

Five commits landed in branch `issue-40-published-at` (now merged to `main`):

| Commit | What |
|---|---|
| `19a748b` | Initial steps 1–8: `enrichment_fills` mechanism, TalentBrew JSON-LD parsing, Avature sitemap, Rippling stub-mode conversion, original (over-clever) upsert SQL |
| `39debf0` | Address ce-review F1–F7: revert step-8 `CASE`/`COALESCE` cleverness, fix Rippling string-slice date, TalentBrew `isinstance` guard, `update_enrichment` preserves existing `published_at`, delete dead shims |
| `33e3f43` | **Pure-insert upsert refactor.** Content fields fixed at first insert. Only `last_seen` and `listing_status` mutate on conflict. Distill-invalidation cascade deleted. |
| `a8632b5` | `update_enrichment` uses `COALESCE(jobs.X, %s)` for **all** columns (description, salary, published_at) — first-fill-wins. Adds `scripts/backfill_published_at.py` throwaway. |
| `1255510` (→ `aaec9d1` after rename) | Migration `014_published_at_not_null.sql`: backfill safety net + `DEFAULT CURRENT_DATE` + `SET NOT NULL`. |

**Per-fetcher behavior under the new code:**

| ATS | `enrichment_fills` | Source of `published_at` for new rows | Backfill path for existing NULLs |
|---|---|---|---|
| TalentBrew | `(description, published_at)` | JSON-LD `datePosted` on detail page | `jsb sync enrich` matches `published_at IS NULL`, fetches detail page, fills via `update_enrichment` |
| Avature | `(description,)` | `<lastmod>` from `/{section}/sitemap.xml` (attached in `list_jobs`) | Backfill script Phase 1 (sitemap recovery) |
| Rippling | `(description, published_at)` | `createdOn` from detail endpoint | `jsb sync enrich` (newly stub-mode; was previously a "full" fetcher silently dropping data) |
| Tesla | default `(description,)` | None — source has no field | Backfill script Phase 2 (`last_seen::date`) |
| Workday / SF / Eightfold leakage | default `(description,)` | List-shape field (when present) | Backfill script Phase 2 (`last_seen::date`) |

## Runbook to close the issue

Run in this order. Each step depends on the previous. Don't skip step 1 — it's the only thing that gets real `datePosted` values onto the existing 21k TalentBrew rows; `last_seen::date` is a quality-floor fallback that's strictly worse.

### Step 1: Run a full sync to backfill real source dates ✅ done

Originally: "re-fetches detail pages for ~21k rows" (Walgreens, Citi, etc.). This actually shipped in an earlier sync — those tenants were already at 0 missing `published_at` before Step 1 was re-run. The 2026-05-06 work cleaned up the remaining stub-fetcher tenants whose parsers were silently empty (Boeing, Amtrak, EY, Gulfstream, Paccar, Disney, Canva) — see the parser-variant fix table at the top of this doc. After fixes:

- TalentBrew tenants now at 0 missing across the board
- SuccessFactors tenants now at 0 missing
- SmartRecruiters / Canva at 0 missing
- Workday tenants don't have `published_at` in `enrichment_fills` so enrich won't fill it; remaining ~140 active rows are handled by Step 2's safety-net fallback

Re-run the command if you want to verify it's a no-op:

```bash
jsb sync           # or: jsb sync enrich   (only the enrich phase if you want to scope down)
```

What this does:
- TalentBrew: re-fetches detail pages for ~21k rows (Walgreens 10k, Citi 3.8k, etc.). Each row gets its real JSON-LD `datePosted`. Long-running — TalentBrew has `enrich_delay = 0.0` (unbounded), so plan for an hour-ish total. Walgreens may 429 us mid-run; the existing retry/backoff handles it.
- Rippling: 503 rows go through enrich (newly activated since `descriptions_in_listing` is now `False`). Detail endpoint fills description + `createdOn` + salary in one shot.
- Other stub fetchers (Workday, SF, Eightfold): unchanged — they still fill `published_at` from list-shape data on initial insert, and they don't ask for `published_at` enrichment.

**Important:** the `jsb sync` systemd timer was disabled during this work (see `~/.config/systemd/user/jsb-sync.timer`). To re-enable after the runbook is done:

```bash
systemctl --user enable --now jsb-sync.timer
```

### Step 2: Run the backfill script

```bash
python scripts/backfill_published_at.py --dry-run   # preview
python scripts/backfill_published_at.py             # actually write
```

What this does:
- **Phase 1: Avature sitemap.** Avature jobs already have descriptions, so the enrich predicate (`description IS NULL OR published_at IS NULL` for fetchers declaring `published_at` in `enrichment_fills`) doesn't match — and Avature's `enrichment_fills` is `(description,)` anyway. So Avature's sitemap dates only flow on initial insert via `list_jobs`, which under pure-insert can't backfill existing rows. The script directly fetches each Avature tenant's sitemap and writes via `update_enrichment`. Last dry run showed: Bloomberg 502 of 868 covered, Broad Institute 41 of 86 covered. The remainder is removed listings already aged out of the sitemap.
- **Phase 2: `last_seen::date` fallback.** Any row still NULL gets `last_seen::date`. After step 1, this is roughly Tesla (5.9k) + small leakage in Workday/SF/Eightfold + the Avature rows that the sitemap didn't cover.

### Step 3: Apply migration 014

```bash
jsb migrate
```

The migration is self-sufficient: it includes its own `UPDATE ... SET published_at = last_seen::date WHERE published_at IS NULL` as a safety net before the `ALTER COLUMN ... SET NOT NULL`. So if step 2 was skipped or crashed mid-flight, the migration still lands cleanly. Step 2's value is recovering the ~543 Avature sitemap dates that the migration's last-resort `last_seen::date` fallback can't.

### Step 4: Verify

```bash
azpg "service=job-search-buddy-azure" -c "
  SELECT COUNT(*) FROM jobs WHERE published_at IS NULL;
"
# Expected: 0

azpg "service=job-search-buddy-azure" -c "
  \d jobs
" | grep published_at
# Expected: 'not null' constraint, default 'CURRENT_DATE'
```

### Step 5: Cleanup

```bash
git rm scripts/backfill_published_at.py
git commit -m "chore: remove issue-40 backfill throwaway"
```

The script's job is done after one successful run. Delete it.

### Step 6: Close issue #40

The original issue stated four pieces:
1. Update TalentBrew + Avature to extract real dates — done.
2. Add scrape-date fallback — done as `last_seen::date` via the script + the migration's UPDATE.
3. Backfill existing NULLs — done by step 2.
4. Migration: `published_at SET NOT NULL` — done by step 3.

Plus the bonus that fell out of investigation: Rippling was silently broken (returning empty stubs and no enrich path). That's now fixed — see `19a748b`.

## Known tradeoffs going forward (post-migration)

These are conscious choices, not regressions waiting to bite. Documented so the next person doesn't have to re-derive them.

### Pure-insert: content is fixed at first insert

Re-syncs no longer overwrite `title`, `location`, `description`, `salary`, `published_at`, `ats_metadata`, etc. on existing rows. Only `last_seen` and `listing_status` (and `removed_at` via the trigger) mutate.

**What this gains:**
- Distill never re-fires from cosmetic re-strips. `short_jd` is now exactly-once per `(slug, job_id)` pair.
- The `fts_vector` generated column doesn't recompute on re-sync, since none of its inputs (title, short_jd, description_normalized, location, department) change in the conflict path.
- Idempotent re-syncs: running `jsb sync` repeatedly produces no content changes after the first pass.

**What this gives up:**
- A Greenhouse / TalentBrew / etc. parsing improvement (whitespace fix, salary regex change) doesn't propagate to existing rows on the next sync.
- A typo fix at the source ATS doesn't reach our DB.
- A salary that gets disclosed mid-listing stays NULL.

**Escape hatch when refresh is needed:** explicit SQL.

```sql
-- Force re-distill of a specific row
UPDATE jobs SET short_jd = NULL, description_normalized = NULL
WHERE company_slug = 'acme' AND job_id = 'foo';

-- Force re-fetch of description (releases row back to the enrich queue)
UPDATE jobs SET description = NULL
WHERE company_slug = 'acme' AND job_id = 'foo';
-- then: jsb sync enrich --company acme

-- Force published_at refresh
UPDATE jobs SET published_at = NULL  -- ONLY before NOT NULL migration applied
-- (post-014, you can't NULL it; it's NOT NULL. Use a real date instead.)
```

Or build a `jsb refetch <slug> <id>` CLI command if explicit refresh becomes a recurring need.

### New stub-fetcher rows get "today" instead of real `datePosted`

Under the new model:
1. Stub fetcher's `list_jobs()` returns a Job with `published_at=None`.
2. Upsert INSERTs the row with `COALESCE(NULL, CURRENT_DATE) → today`.
3. Enrich phase fetches the detail page, gets the real `datePosted`.
4. `update_enrichment` writes via `COALESCE(jobs.published_at, %s)` → existing today wins → real date is discarded.

Why: NOT NULL forces the INSERT to write *something*, and once written, `update_enrichment`'s old-wins policy locks the row to that value.

**Quality cost:** new TalentBrew/Rippling rows get scrape-date instead of source-date. For active jobs synced daily, scrape-date is roughly equal to posted-date (off by a day or two). Recency-aware ranking still works correctly. The 21k existing TalentBrew rows DO get real source dates because they were already NULL at deploy time and the enrich path successfully fills NULLs.

### Avature sitemap doesn't help re-syncs

Avature attaches sitemap dates in `list_jobs()` only. On a re-sync of an existing row, the upsert's ON CONFLICT path doesn't touch `published_at`, so a new sitemap entry can't land on an existing row. Initial insert is the only window for sitemap dates to take effect.

This is consistent with the pure-insert rule. If a tenant ever needs Avature sitemap re-import, do it via SQL.

### `enrichment_fills` is the one piece of declarative cleverness

Each fetcher declares which columns its detail-page fetch can fill. The enrich phase polls `WHERE column IS NULL OR ...` for those columns. The store's `update_enrichment` writes via per-column expressions:

```python
_ENRICHMENT_WRITE_EXPR = {
    "description":  "COALESCE(jobs.description, %s)",
    "salary":       "COALESCE(jobs.salary, %s)",
    "published_at": "COALESCE(jobs.published_at, %s)",
}
```

If we add a fourth enrichable column later (e.g., `team`, `department` from a richer detail page), it goes here.

### Stale doc reference: `.claude/rules/sync-pipeline.md`

That file still says "The upsert nulls `short_jd`/`description_normalized` whenever a job's description body changes, so the distill phase will pick the row up again on the next pass." That sentence is **no longer true** after `33e3f43` (pure-insert refactor). The sentence should be updated to describe the new contract:

> Under the pure-insert upsert, distill runs exactly once per `(slug, job_id)` pair. The `short_jd`/`description_normalized` outputs are not invalidated on re-sync because the description column doesn't change. To re-distill a specific row, manually NULL out `short_jd` via SQL (the partial index `idx_jobs_needs_distill` will pick it up on the next distill pass).

Update this when next touching that doc — not blocking for the runbook.

## Why this is open work, not "merge complete"

The branch shipped the *infrastructure* changes (fetcher improvements + pure-insert upsert + migration file). The actual data backfill (steps 1–3 above) requires running synchronous, expensive operations against production:

- ~22k HTTP requests for TalentBrew + Rippling enrich.
- One sitemap fetch per Avature tenant.
- One UPDATE statement against the full `jobs` table for the safety-net backfill.
- One ALTER TABLE for the NOT NULL constraint.

Doing all of that as part of the merge would have been irresponsibly synchronous. So the merge is "infrastructure ready"; the runbook is the operational completion.

After step 6 (issue closed), this document should also be moved to `docs/archive/`.
