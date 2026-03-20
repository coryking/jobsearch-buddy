# Company-Enriched Embeddings

## What This Is

Job search is "find me a job that fits." Fit is more than keyword matching — it's
domain, culture, vibe, what the company actually builds, who they sell to, what
it's like to work there. The embedding pipeline combines company intelligence
with job descriptions to produce embedding text that captures the full picture
of what a job *is*, not just what the posting says.

Two agents, two jobs, two lifecycles:

1. **Company Researcher** — investigates a company, produces a durable profile
2. **Embedding Text Generator** — combines that profile with a specific job
   posting to produce the text that gets embedded

The strip phase no longer removes boilerplate. It produces the complete,
normalized document that the embedding model sees. The job description is a
component of that document — arguably the most important component — but it's
not the whole thing.

## What This Unlocks

A job seeker types "AI-native product manager jobs around Seattle." Today that
query matches job titles and whatever keywords happen to appear in the JD. If
the posting doesn't say "AI-native" (and most don't — they say "experience with
machine learning" or "LLM integration"), the match is weak.

With company context in the embedding, the query also matches because the
company profile says "builds large language models, AI safety research company,
San Francisco HQ." The embedding model connects "AI-native" to the company
context even when the JD never uses that phrase.

More examples of queries this enables:

- "startups who are building things with React"
- "companies working on developer tools"
- "defense and intelligence contractors"
- "healthcare companies that aren't just another EHR"
- "remote-first companies, not remote-tolerant"
- "small teams where engineers ship fast"
- "fintech but not crypto"
- "places with startup energy but real revenue"
- "companies where I'd be the first PM"
- "chill company that ships fast and doesn't do leetcode"
- "anyone doing interesting things with LLMs besides chatbots"

These queries work because the company profile captures domain, stage, culture,
and organizational reality — signal that no individual job description contains.

### The Vibe Problem

Factual company data (what they build, who they sell to, where they're located)
is necessary but insufficient. Job seekers also search by *vibe* — what it's
actually like to work somewhere. "Fast-paced" and "chaotic" describe the same
company from different perspectives. "Structured and predictable" and
"bureaucratic and slow" are the same place seen through different eyes.

The embedding pipeline captures organizational reality in behavioral terms:
how decisions get made, what the pace feels like, how much autonomy people
have, whether the mission feels authentic. It describes, it does not judge.
See `docs/bullshit-compatibility.md` for the full framework.

This is an elegant use of embeddings. Someone searching "scrappy, move fast,
wear many hats" lands near companies described as "startup energy, unclear role
boundaries, priorities shift weekly." Someone searching "structured, clear
expectations, predictable roadmap" lands near companies described as
"heavyweight process, quarterly planning cycles, clear ownership boundaries."
Same company might surface for either query depending on how employees describe
it — because the embedding model is good at recognizing that "chaotic and
disorganized" and "scrappy and fast-moving" occupy the same semantic
neighborhood. The compatibility judgment belongs to the seeker, not the system.

## The Pipeline

```
                    ┌─────────────────────┐
                    │  Company Researcher  │  (runs per-company, infrequently)
                    │  agent + web search  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  Company Profile     │  (stored in companies table)
                    │  durable facts +     │
                    │  behavioral reality  │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
              ▼                ▼                ▼
     ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
     │  Job Post A  │  │  Job Post B  │  │  Job Post C  │
     └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
            │                │                │
            ▼                ▼                ▼
     ┌─────────────────────────────────────────────┐
     │        Embedding Text Generator              │
     │  (LLM: company profile + job description     │
     │   + metadata → normalized embedding text)    │
     └──────────────────────┬──────────────────────┘
                            │
                            ▼
     ┌─────────────────────────────────────────────┐
     │        Embedding Model                       │
     │  (text in → vector out, purely mechanical)   │
     └─────────────────────────────────────────────┘
```

### Agent 1: Company Researcher

Runs when a company is added to the registry or when profiles are refreshed.
This is actual work — web searches, reading Glassdoor, synthesizing sources.
It does not run on every sync.

**Input:** Company name (and slug, ATS URL, board identifier — whatever is in
the registry).

**Output:** A natural-language company profile stored in the `companies` table.
The researcher has latitude to discover what's relevant — no rigid template.
The kinds of signals that matter for job search include:

**Factual signals** — what the company builds, who their customers are, what
domain they operate in, business model, technology and platform bets, size,
stage, funding, geography, origin story, founder background, competitive
positioning. Report these however they're most accurate — "$2B revenue" and
"Series C" are fine here. The downstream embedding text generator handles
translation to semantic language; the company profile is a factual record.

**Behavioral/cultural signals** — how decisions get made, what the pace feels
like, how much process exists, communication culture, stability vs. chaos,
mission authenticity, technical culture (build vs. buy, over-engineering vs.
pragmatism), performance and accountability norms. These come from employee
reports (Glassdoor, Blind), engineering blogs, leadership public statements,
and the negative space — what the company conspicuously doesn't talk about.

Not every company will have signal for every category. A 50-person startup
with no Glassdoor presence yields a different profile than a public company
with quarterly earnings calls and 500 employee reviews. The agent works with
what it can find.

The profile is descriptive, not prescriptive. It reports specific behavioral
patterns — "decisions get revisited constantly," "engineers have real ownership
of their systems," "always-on urgency" — not star ratings or verdicts. A pile
of bad Glassdoor reviews doesn't make a job bad for a particular seeker. The
same organizational reality that one person calls "toxic" another calls
"high-performance." The profile captures the reality; the seeker's query
determines whether it's a match.

The researcher agent is written like a corpus researcher — it gets a lens
(research this company for job search context), the downstream use (this feeds
embeddings for semantic search), and latitude to discover what's relevant. Some
companies have rich signal: engineering blogs, Glassdoor patterns, conference
talks, public financials. Others have a landing page and three job postings.
The agent works with what it finds. No rigid template.

**Sources the agent draws from:**
- Official company website, about page, careers page
- Engineering blog, tech talks, conference presentations
- Glassdoor and Blind reviews (for behavioral/cultural signal)
- Crunchbase, PitchBook (for stage, funding, headcount)
- Wikipedia (for public companies, established orgs)
- Press coverage, earnings calls (for public companies)
- The company's own job descriptions (recurring language across JDs is
  culture signal — but job titles and open positions are transient and
  excluded from the profile)

**Staleness:** Company profiles go stale. A company stops being "growth stage"
at some point. Profiles carry a generation timestamp and can be refreshed. The
refresh cadence is a future design decision — for now, generating profiles once
is the first milestone.

### Agent 2: Embedding Text Generator (replaces the strip phase)

Runs per-job during sync, in the phase that was previously "strip." This is
the phase that was optimized for purity — "just remove the boilerplate." That
framing is gone. The new job is: produce the complete, normalized text that
the embedding model sees.

**Input:**
- Raw job description
- Job metadata: title, location, department, salary
- Company profile (from Agent 1)

**Output:** A single normalized document — the exact text that gets sent to
the embedding model. `embed_text()` in `models.py` disappears. The embed
phase becomes purely mechanical: text in, vector out.

The embedding text generator takes the job description (the most important
input), enriches it with company context, and produces a document optimized
for semantic retrieval. "Optimized for semantic retrieval" means:

- **Structured facts translated to natural language.** The embedding model
  does not understand numbers. See "Embedding Model Limitations" below for
  the research basis. The company profile may say "$2B revenue" and "500
  employees" — that's accurate for the factual record. The embedding text
  generator translates these into "large publicly traded enterprise" and
  "mid-size company" — semantic territory the embedding model handles well.
  This translation is mandatory, not cosmetic.

- **Boilerplate removed.** EEO statements, generic benefits lists, legal
  disclaimers — the same work the strip phase did before. This is now one
  concern among several, not the whole job.

- **Company context woven in.** The company profile provides domain, culture,
  and organizational signal that the JD alone doesn't contain. A "Senior
  Software Engineer" at a privacy company should embed differently than the
  same title at an adtech company. The company context pushes the vector
  into the right neighborhood.

- **Consistent structure across ATS platforms.** A Greenhouse JD and a Workday
  JD and a hand-written job posting all get normalized into a consistent
  format. The embedding model sees uniform documents regardless of source.

- **Neutral tone.** No marketing language ("exciting opportunity!"), no LLM
  opinions about the role. Declarative descriptions of what the job is and
  what the company is.

### The Embed Phase

Unchanged. Takes normalized text, sends it to the embedding model in batches,
stores vectors. Purely mechanical. The intelligence is upstream in the
embedding text generator.

## Why Natural Language, Not Structured Fields

An early design considered structured metadata columns on the companies table
(industry enum, stage enum, size bucket, remote policy boolean) that would
support faceted filtering alongside vector search. This is a valid feature
but a separate product decision with its own schema, UI, and query mechanics.

For the embedding pipeline, structured fields are unnecessary because the LLM
pre-digests structured facts into natural language. Pinecone tested four
approaches to embedding structured data — simple concatenation, with headers,
with descriptions, and natural language conversion. Natural language conversion
correctly answered queries that the other three got wrong.

The company profile says "large publicly traded infrastructure company" instead
of `stage: public, revenue: $2B, industry: infrastructure`. The embedding
model works with the natural language version. Faceted search (filter by
company size, industry, remote policy) is a separate feature that can be
built independently if needed.

## Why Two Agents, Not One

The company profile and the embedding text have different lifecycles. Company
facts are durable — what Anthropic does, who they compete with, what it's like
to work there. These change slowly. Job postings are transient — new ones
appear daily, old ones close.

If a single LLM call tried to research the company AND normalize the JD, it
would re-research the same company for every job posting. 540 companies times
~20 jobs each is ~10,800 calls that each need web search. Separating the
research (once per company) from the normalization (once per job) is the
obvious architecture.

The company profile also serves as a human-readable artifact. Someone can
read the profile and understand what the system knows about a company. If
the profile is wrong or incomplete, it can be edited directly. The embedding
text generator's output is ephemeral — it exists to feed the embedding model,
not to be read by humans.

## Why Strip and Embed Stay Separate Phases

Two constraints prevent merging into one pass:

- **Embedding text generation** = LLM inference. One call per job. Serial,
  rate-limited, expensive.
- **Embedding** = embedding API. Batch-optimized — 2,048 texts in one API
  call. Fast, cheap per-unit.

The normalized text stored in the database is a buffer between a serial
producer and a batch consumer. Streaming LLM output directly into batch
embedding calls is not efficient.

## The Cascade (Change Detection)

```
raw inputs change (JD, title, company profile...)
  → input_hash changes
    → embedding text generator detects mismatch, re-generates normalized text
      → content_hash changes (recomputed from new normalized text)
        → embed phase detects mismatch, re-embeds
```

`input_hash` = hash of all inputs to the embedding text generator (raw
description + title + location + department + company profile). Detects when
ANY input changes — including company profile updates.

`content_hash` = hash of the normalized output text. Detects when the
embedding text actually changed (the LLM might produce identical output
from slightly different inputs).

Each hash guards its own phase. Two invalidation signals, two phases, clean
separation. This replaces the current `description_stripped IS NULL` check,
which silently ignores in-place description changes.

## Embedding Model Limitations (Research Basis)

Design decisions in this pipeline are grounded in published research on what
embedding models can and cannot do. Key findings:

**Numerical blindness.** "Revealing the Numeracy Gap" (arXiv 2509.05691)
tested 13 embedding models (BERT-based, LLM-based, domain-specific financial
models) on numerical understanding. Average accuracy: 0.54 — barely above
coin flip. Sentences like "grew by 2%" and "grew by 20%" produce nearly
identical embeddings. Even domain-specific financial models showed the same
weakness. This is why the embedding text generator translates structured
facts to natural language — the embedding model cannot reason about numbers.

**Range blindness.** Testing reported by IoT For All found that "costs between
$50-$100" and "costs exactly $101" scored 0.98 cosine similarity. The model
cannot distinguish a value inside a range from one outside it.

**Negation blindness.** Research on negation awareness (arXiv 2504.00584)
found LLM-based embeddings ~6% better than BERT-based on negation benchmarks,
but still unreliable. "Effective" and "ineffective" score high similarity.
This means queries like "not adtech" may not reliably exclude adtech companies
through vector search alone.

**Natural language conversion works.** Pinecone tested four approaches to
embedding structured/tabular data: simple concatenation, with column headers,
with table descriptions, and natural language conversion. Natural language
conversion correctly answered queries that the other three approaches got
wrong. (Source: pinecone.io/learn/structured-data/, with accompanying
notebook at github.com/pinecone-io/examples.)

**Embeddings capture semantic relatedness, not deep reasoning.** Analysis by
Nathan Bos ("Embeddings Are Kind of Shallow," Towards Data Science) argues
that embeddings capture surface-level semantic processing — topic similarity,
vocabulary patterns, synonym relationships — but struggle with deeper
abstraction. This is consistent with the numeracy and negation findings:
embeddings are excellent at "these concepts are related" and poor at "this
number is larger than that number."

**Retrieval has mathematical bounds.** Google DeepMind (arXiv 2508.21038)
proved that the number of possible top-k document sets is bounded by
embedding dimension. On their LIMIT stress test, state-of-the-art models
achieved less than 20% recall@100 on simple combinatorial queries. Single-
vector retrieval has fundamental limits.

**Practitioner consensus: hybrid search is the baseline.** DevX, Pinecone,
Azure AI Search, Weaviate, and Elastic all document hybrid search (vector +
structured filters + keyword matching) as the recommended production
architecture. This pipeline focuses on the vector component; faceted
filtering is a separate feature that complements it.

## What This Does NOT Do

- **Faceted search.** No structured filter columns, no "WHERE industry =
  'healthcare'" queries. That's a separate feature. The embedding pipeline
  captures these concepts semantically, not structurally.

- **Job title trend analysis.** What a company is hiring for right now is
  interesting signal, but it's transient — it changes quarterly. The company
  profile captures what the company IS, not what positions are open. Job
  title patterns across a company could be a separate embedding axis.

- **Compatibility scoring.** The system does not tell a seeker whether a
  company is "good" or "bad" for them. It captures organizational reality
  in behavioral terms and lets the seeker's query determine fit. The
  bullshit compatibility framework (`docs/bullshit-compatibility.md`)
  articulates why: every company's dysfunction is someone else's dream job.

- **Automated profile refresh.** Profiles go stale. The refresh mechanism
  is a future concern. For now, the goal is generating profiles once and
  validating the pipeline.
