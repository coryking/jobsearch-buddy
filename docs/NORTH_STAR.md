# NORTH_STAR.md

> The design compass for jobsearch-buddy. Doctrine. When a design decision is
> contested, this document wins. When this document is wrong, update it *here*
> before changing designs downstream.

---

## What jobsearch-buddy is

jobsearch-buddy (`jsb`) is a structured, fact-dense data provider for an LLM
that does the actual ranking. Its load-bearing asset is a normalization layer
over a dozen-plus ATS wire dialects: give it any job URL or a registered
company and it returns clean, comparable JSON — fetched from the ATS at call
time, never served from a cache. The stateful half is application tracking
(what the seeker applied to, who they talked to), plus a small operator-curated
company registry that maps names to board configs.

The thing it is *not*: a search engine, a ranker, or a recommender. It does not
decide which jobs fit a seeker. The calling LLM — Claude Desktop, ChatGPT, an
agent on the seeker's machine — does that, reading live listing rows
in-context. The context window is the database; the LLM is the query engine;
jsb is the wire-protocol adapter. Cross-company *discovery* ("who is hiring
for X anywhere?") is explicitly ceded to Indeed/LinkedIn — jsb starts from
companies the seeker already cares about.

A corpus-backed search surface (scraped jobs table, FTS, distilled snippets,
bio embeddings) exists in the codebase but is withdrawn from the MCP surface:
a cache of yesterday's fetch presents itself as today's truth, and both
data-integrity failures that motivated the withdrawal were staleness bugs in
that cache while the live fetch path was correct. The modules stay on disk;
`src/jobbuddy/mcp_tools/__init__.py` documents the one-line restore if usage
proves the corpus tools are missed.

## Who the user is

**The user is the human searching for a job, not the LLM that called the MCP
tool.** Every design decision starts from "did the human get a better
outcome?", not "was the LLM's call easier?" The LLM is infrastructure. It
intermediates, summarizes, paraphrases, and gates output to the human — but
optimizing for *its* convenience without checking through to the human's
outcome is the dominant failure mode.

A concrete consequence: tool parameters and descriptions are written for the
intent the human is trying to express ("I applied for X"; "find me jobs at
Y-type companies in Z"), not for the shape of API the LLM finds easiest to
call. If a parameter would let the LLM work around a tool limitation by
enumerating synonyms, we don't add that parameter — we make the tool good
enough that intent-shaped calls work.

## The fetch chain

This is the actual flow when somebody uses the MCP server. Every architectural
decision in this repo is downstream of these steps.

1. **The seeker's watch list lives with the calling LLM** — in its project
   context or preferences, never as server state. jsb's `ats://companies`
   resource is the phone book (slug, name, ATS) for resolving names.
2. **The LLM pulls live boards with `list_company_jobs(company,
   posted_since=…)`** — one call per company of interest, compact rows
   (title, locations, salary, publish date, last ATS touch, id, url),
   newest first. The rows are what the board says right now.
3. **The LLM ranks and filters those rows in-context.** Location, level,
   domain, the seeker's quirks — that judgment happens in the caller, over
   complete fresh rows, not in SQL over a cache. jsb returns rows in a
   stable newest-first order, never a "best-fit" order.
4. **For the finalists, the LLM pulls `get_job(url=…)`** — the full JD,
   normalized, plus `get_application_form` to preview the questions behind
   the Apply button.
5. **The LLM matches against what it knows about the seeker.** That's the
   compatibility judgment. jsb does not make it; jsb does not encode the
   seeker's preferences anywhere.

For company context, `ats://companies/{slug}` carries a researched NPOV bio
where one exists — evidence about what working there empirically looks like,
for the LLM to reason over.

## NPOV — neutral point of view

Nothing jsb generates encodes judgment about whether a job or a company is
good. The seeker may be hunting for the unattractive fact — a swing-shift
slot, a clearance requirement, a flat-rate pay structure, a company that ships
weapons. The same reality reads as "chaotic" to one seeker and "scrappy" to
another; one calls it "bureaucratic," another "process-mature." We surface the
concrete fact and let the calling LLM, which has the seeker's context, decide
how it reads.

Banned vocabulary across all distill and bio outputs: *fast-paced*, *scrappy*,
*chaotic*, *world-class*, *innovative*, *cutting-edge*, *family-friendly*,
*appeals to engineers who care about X*. Allowed and encouraged: founding
year, employee count with date, regulatory regime, ownership structure,
named programs, named regulatory bodies, schedule shape, hard gates
(clearance / citizenship / drug test / licensure), explicit pay floors and
ceilings with currency and unit.

The rule is recursive: the bio is NPOV. The distill is NPOV. Tool descriptions
are NPOV. `list_company_jobs` returns rows in a stable newest-first order, not
a "best-fit" order — sort comes from an explicit signal (publish date) the LLM
can reason about, not a hidden score.

## Workplace-defining facts beat company-press-release facts

The empirically dominant bio failure is reaching for the facts that define
the *company* — founding year, HQ, engineering blog — and missing the facts
that define *the work*. Caliber Collision is its flat-rate pay structure
before it is anything else. Anduril is the ITAR / U.S.-Person requirement
before it is its Series. Walgreens is the DEA $300M opioid settlement and the
seven-year compliance MOA before it is a retail pharmacy. When judging bio
output, the test is: did the bio surface the facts that define what it's like
to work there, or just the facts a press release would lead with?

The corollary for `short_jd`: surface specifics that differ between two
postings with the same title at different companies. Two "Software Engineer
III" rows with identical `short_jd`s have failed the snippet's job. Named
products, named systems, named regulatory regimes, schedule shape, ownership
scope, hard gates — these are what distinguish.

## Evidence beats ranking

jsb's competition is `web_search` + `web_fetch`. Those tools can find a job
posting's *page*; they hand back scraped HTML that drops structured fields
(secondary locations, salary tiers, publish dates, apply-form questions) and
can't reliably enumerate a whole board. jsb hits the ATS's own API and returns
every posting on a board as normalized, comparable rows in one round trip —
the same shape whether the board speaks Greenhouse, Ashby, or Workday. That
normalization *is* the edge. Architectural changes that make one ATS's rows
richer at the cost of cross-ATS comparability move us toward web_search and
away from where the edge is.

## Practical, not enterprise

This is a personal tool that ended up shaped like a polished open-source MCP
server. The repo is public, the architecture is documented, the test suite
exists — but the operating premise is "an individual is searching for a job
and this tool helps." Bias toward shipping. 80% today beats 99% tomorrow.
Hobby-quality at every joint is wrong; production-grade ceremony at every
joint is also wrong. The bar is: would another careful person, reading the
code cold, understand what's going on and trust the data? If yes, ship.

## What this implies for the work

- New MCP tools earn their place by giving the calling LLM evidence it cannot
  cheaply assemble from `web_search`. "It's nicer than web_search for this
  one company" is a bad reason; "no one-shot web_search can answer this
  across the whole company set" is a good reason.
- Tool descriptions are routing hints written in the seeker's intent
  language, not API docs.
- Distill prompts, bio prompts, and tool descriptions live and die by NPOV.
  When the eval drifts toward verdict words, the prompt is broken, not the
  examples.
- `short_jd` is judged by whether it distinguishes one posting from its
  same-titled sibling at a different company. Bio leaks (importing bio
  facts the JD didn't gesture at) and title restatement are the two
  dominant failure modes.
- Normalization across ATS dialects is the load-bearing property. When
  changes improve one platform's data at the cost of the shared row shape,
  the change is suspect.
- The seeker's context never lives in jsb. Filters and preferences flow
  through the calling LLM. If a feature would require us to encode the
  seeker's preferences, it belongs in the calling agent, not here.

---

This doc is the answer to *why* the system is shaped the way it is.
`docs/architecture.md` is the answer to *how* it's shaped. `CLAUDE.md` is the
navigation layer over both.
