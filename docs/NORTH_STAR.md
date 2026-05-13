# NORTH_STAR.md

> The design compass for jobsearch-buddy. Doctrine. When a design decision is
> contested, this document wins. When this document is wrong, update it *here*
> before changing designs downstream.

---

## What jobsearch-buddy is

jobsearch-buddy (`jsb`) is a structured, fact-dense data provider for an LLM
that does the actual ranking. It scrapes ATS job boards, distills postings into
comparable fields, researches the companies posting them, and exposes the
result through a small MCP surface and a CLI.

The thing it is *not*: a search engine, a ranker, or a recommender. It does not
decide which jobs fit a seeker. The calling LLM — Claude Desktop, ChatGPT, an
agent on the seeker's machine — does that. jsb's job is to make the LLM's job
possible by handing it evidence it could not assemble in one shot from
`web_search` + `web_fetch`.

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

## The search chain

This is the actual flow when somebody uses the MCP server. Every architectural
decision in this repo is downstream of these steps.

1. **The calling LLM filters with `search_jobs(title=…, location=…,
   company=…, posted_since=…, query=…)`.** PostgreSQL full-text search, not
   vector. The LLM issues structured keyword queries (`staff software engineer
   AND ai`), plus location and company filters — it does not issue "vibe"
   queries like "high-energy AI startups." Embeddings cannot help with the
   queries the LLM actually issues against jobs.
2. **Each result row carries `short_jd` inline.** This is the SERP snippet the
   LLM reads to decide which postings are worth deeper inspection. The LLM
   scans many; it reads few in full.
3. **For company context, the LLM either flips `include_company_bio=true` on
   `search_jobs` or calls `get_company(slug)`** one company at a time. Company
   bios disambiguate vague JD language (a JD that says "support compliance
   programs" reads differently when the bio says "ITAR-regulated defense
   contractor"). They do not get restated inside `short_jd`.
4. **For jobs worth a real read, the LLM pulls `get_job_post_details`,**
   which returns `description_normalized` (boilerplate-stripped JD).
5. **The LLM matches against what it knows about the seeker.** That's the
   compatibility judgment. jsb does not make it; jsb does not encode the
   seeker's preferences anywhere.

The company-side path is similar in shape: `find_companies` is hybrid
vector + FTS over researched bios, but again it returns evidence — what these
companies are, what working there empirically looks like — for the LLM to
reason over, not a ranked list ordered by fit.

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
are NPOV. `search_jobs` returns rows in a stable order, not a "best-fit"
order — sort comes from explicit signals (posting freshness, location match)
the LLM can reason about, not a hidden score.

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

jsb's competition is `web_search` + `web_fetch`. Those tools can find any one
job posting or company page faster than a sync pipeline can. They cannot
assemble normalized, comparable data across many companies in a single round
trip — they cannot tell the calling LLM that of 47 companies in the seeker's
filter, 6 are on ITAR-regulated work, 12 have published pay floors above
$X, 3 have multi-year-old reqs that are still listed. That comparability *is*
the edge. Architectural changes that make individual rows richer at the cost
of cross-company comparability move us toward web_search and away from where
the edge is.

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
- Comparability across companies is the load-bearing property. When changes
  improve one company's data at the cost of cross-company comparability,
  the change is suspect.
- The seeker's context never lives in jsb. Filters and preferences flow
  through the calling LLM. If a feature would require us to encode the
  seeker's preferences, it belongs in the calling agent, not here.

---

This doc is the answer to *why* the system is shaped the way it is.
`docs/architecture.md` is the answer to *how* it's shaped. `CLAUDE.md` is the
navigation layer over both.
