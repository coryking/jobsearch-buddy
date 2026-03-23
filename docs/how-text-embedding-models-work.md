# How text embedding models work: a practitioner's field guide

**The single most important thing to understand about text-embedding-3-small is that it compresses your entire input into 1,536 floating-point numbers by running text through a transformer that progressively builds contextual representations, then extracts a single summary vector from the final token.** Everything downstream — what retrieval catches, what it misses, and why your enriched job descriptions sometimes perform worse than bare ones — traces back to what survives that compression. This document explains the mechanism end-to-end so you can predict how your text choices affect retrieval quality, not just follow recipes.

The model is a black box in the sense that OpenAI hasn't published its architecture details. But enough is known from their research papers, the open-source models that work similarly, and extensive probing research to build a reliable mental model. Where claims rest on inference rather than confirmed facts, that's noted explicitly.

---

## Part 1: The pipeline from text to vector

### Tokenization turns your text into integer IDs

Text-embedding-3-small uses OpenAI's `cl100k_base` tokenizer, a byte-pair encoding (BPE) scheme with a vocabulary of **100,277 tokens**. Before BPE merges kick in, a regex pattern pre-splits text into chunks: words (with leading spaces attached), contractions, punctuation sequences, and — critically — **digit groups of 1–3 characters**. That `\p{N}{1,3}` regex means the tokenizer processes numbers in at most three-digit chunks: "147" is one token, "1000000" becomes `100|000|0` (three tokens), and "2%" becomes `2` + `%` while "20%" becomes `20` + `%`.

The vocabulary contains exactly **1,110 pure-digit tokens**: the ten single digits, one hundred two-digit numbers (00–99), and one thousand three-digit numbers (000–999). These tokens have no mathematical relationship to each other. To the model, `2` and `20` are arbitrary symbols — like `cat` and `dog` — whose meanings are learned entirely from context during training.

For your preprocessing decisions, here's what matters about the tokenizer:

- **Markdown formatting consumes tokens**. `## Heading` produces roughly `##` + ` Heading` (2 tokens). `• Item` costs a token or more for the bullet character (Unicode `•` is 3 UTF-8 bytes). `**bold text**` adds 2 tokens just for the asterisks. These tokens carry structural information that is essentially noise for retrieval.
- **Spaces attach to the following word**. The tokenizer produces `" is"` (space + word), not `" "` + `"is"`. This means natural English prose tokenizes efficiently.
- **Accented characters cost more**. "café résumé naïve" produces ~9 tokens versus fewer without accents. Relevant if you're processing international job descriptions.

### The transformer builds contextual representations layer by layer

Each token ID maps to a dense vector via a learned embedding table, then gets a positional encoding added (encoding where in the sequence it sits). These combined vectors enter the transformer stack.

The model is almost certainly a **decoder-only (GPT-family) transformer** — OpenAI's embedding paper (Neelakantan et al., 2022) describes using GPT-series models as the backbone. This means it uses **causal (left-to-right) self-attention**: each token can only attend to tokens before it and itself. The last token in the sequence has attended to everything.

Each transformer layer does two things:

**Self-attention** lets tokens exchange information. Each token computes a query ("what am I looking for?"), and every preceding token provides a key ("here's what I offer") and a value ("here's my information"). The attention weights — how much each token listens to each other token — are computed as softmax-normalized dot products between queries and keys, then used to create a weighted combination of values. Multiple attention heads run in parallel, each learning to capture different types of relationships (syntactic structure, semantic similarity, coreference, etc.).

**Feed-forward networks** process each token independently after attention, acting as learned memory lookups. Research suggests attention layers handle contextual routing and relational patterns, while feed-forward layers store factual and world knowledge (Rogers et al., "A Primer in BERTology," TACL 2020).

Probing studies on transformer models reveal a consistent progression through layers. **Early layers** (first third) encode surface features: token identity, morphology, sentence length. **Middle layers** encode syntactic structure: dependency parses, part-of-speech. **Late layers** encode semantic information: entity types, semantic roles, topic. The final-layer representations are what matter for embedding quality — they're the most semantically rich. (These findings come primarily from BERT-family studies by Jawahar et al. 2019 and Tenney et al. 2019, but the general pattern holds for decoder-only models.)

### Pooling compresses the sequence into one vector

This is where the critical compression happens. Based on OpenAI's published research and corroboration from the NV-Embed paper (ICLR 2025), text-embedding-3-small almost certainly uses **last-token (EOS) pooling**: it appends a special end-of-sequence token, runs the full transformer stack, and extracts the final-layer hidden state at that last token's position. Because the causal attention mask means the last token has attended (directly or transitively) to every preceding token, it serves as a learned summary of the entire input.

This is different from **mean pooling** (averaging all token representations), which is standard in bidirectional models like Sentence-BERT and E5. But the fundamental constraint is the same: an arbitrarily long, rich input gets compressed into a single fixed-size vector. Whether you summarize via a dedicated summary token or by averaging, you're pushing everything through a **1,536-dimensional bottleneck**.

The pooled hidden state then passes through a **learned linear projection** to the output dimension (1,536) and is **L2-normalized** to unit length. Because every output vector sits on the unit hypersphere, cosine similarity equals dot product — a useful computational property.

### Matryoshka training packs importance into early dimensions

Text-embedding-3-small uses **Matryoshka Representation Learning** (Kusupati et al., NeurIPS 2022), confirmed by Pinecone's analysis of OpenAI's models. During training, the loss function is computed not just on the full 1,536-dimensional output but simultaneously on truncated prefixes (e.g., the first 512 dimensions). This forces the model to pack the **most important semantic information into the earliest dimensions**, with progressively finer-grained details in later ones — like Russian nesting dolls. This is why you can set `dimensions=512` in the API and still get reasonable results: the first 512 dimensions carry the bulk of the semantic signal.

---

## What geometry means in embedding space

### Cosine similarity measures topical co-occurrence, not logical relationship

When two texts have **high cosine similarity (say 0.65+)**, it means: the model's training taught it these texts would plausibly appear as a positive pair — they share topical content, vocabulary patterns, and contextual signals as seen in the training corpus. Concretely, "Senior Software Engineer" and "Lead Developer" score high because the training data contains vast numbers of job postings where these titles co-occur and map to similar descriptions.

What cosine similarity does **not** capture:

- **Logical entailment or contradiction**: "The company is profitable" and "The company is not profitable" score nearly identically (~0.96 similarity) because they share topic and vocabulary.
- **Numerical magnitude**: "5 years experience" and "15 years experience" are near-neighbors.
- **Causal relationships**: There's no "because" direction in the space.
- **Compositional meaning**: "Dog bites man" and "man bites dog" produce very similar vectors.

A Netflix research paper (Steck et al., 2024) went further, proving that cosine similarity can yield "arbitrary and therefore meaningless similarities" depending on how the model was trained — the learned embeddings have degrees of freedom that can render cosine similarity non-unique. **Relative rankings within a single model are meaningful; absolute scores are not.**

### The score distribution is narrower than you'd expect

For text-embedding-3-small, genuinely unrelated texts can score near **0.0–0.2**, with related texts typically in the **0.3–0.6** range and highly similar texts reaching **0.6–0.8**. This is a wider spread than the older ada-002, which compressed unrelated texts to ~0.70 and related ones to 0.85–0.95. The v3 models use a more discriminating space, which is why practitioners who migrated from ada-002 had to lower their similarity thresholds substantially.

### Anisotropy compresses the usable range further

Embedding spaces tend toward **anisotropy** — vectors concentrate in a narrow cone rather than uniformly covering the hypersphere. Li et al. (2020) showed pre-trained language models induce non-smooth anisotropic spaces where high-frequency words cluster near the origin and low-frequency words scatter sparsely. Contrastive fine-tuning (which OpenAI's models undergo) significantly mitigates this — it's one of the most effective remedies for anisotropy (Gao et al., SimCSE, 2021) — but the residual effect still compresses the effective similarity range.

---

## How contrastive training shapes the space

Text-embedding-3-small was trained using **contrastive pre-training** on massive text pair datasets (Neelakantan et al., 2022). The core objective is InfoNCE loss: given a positive pair (query, relevant passage), make their embeddings close while pushing apart embeddings of the query and all other passages in the batch (in-batch negatives). With batch sizes up to **131,072**, the model sees enormous numbers of negative examples per step.

**The choice of training pairs defines what "similar" means.** OpenAI's training pairs are predominantly neighboring text segments from web pages and (query, document) pairs — the model learns that texts co-occurring on the same page or answering the same query should be close. This produces excellent topical relatedness but no explicit signal for logical reasoning, numerical comparison, or negation handling.

This is why contrastive embeddings are strong at "these texts are about the same thing" and weak at "these texts have opposite truth values about the same thing." The training data overwhelmingly pairs texts that share topics, not texts that logically relate to each other. **The model has never been taught that "not X" should be far from "X" — only that texts about different topics should be far apart.**

---

## Part 2: Why specific failure modes exist

Each failure below traces to specific mechanisms. The goal is for you to be able to predict new failure modes from these principles.

### Numerical blindness is a three-layer problem

"Grew revenue by 2%" and "grew revenue by 20%" produce nearly identical vectors (practitioners report cosine similarity ~0.97). This is a compound failure across three mechanisms:

**Tokenization provides no magnitude signal.** BPE assigns `2` and `20` to unrelated token IDs. There is no mechanism mapping these IDs to numerical values — they're arbitrary symbols like any other vocabulary item. Wallace et al. (EMNLP 2019, "Do NLP Models Know Numbers?") showed that sub-word tokenization specifically harms numerical representation compared to character-level models, because it breaks the character-level patterns that could encode magnitude.

**Training data doesn't reward numerical discrimination.** Contrastive training pairs "revenue grew by X%" sentences with documents about revenue growth. Whether X is 2 or 20 doesn't determine whether the passage is a relevant match for the query. The model learns that the *context* around numbers (financial reporting, growth metrics) determines relevance, not the numbers themselves.

**The single-vector bottleneck dilutes any residual signal.** Even if attention heads in middle layers capture some numerical distinction, that signal becomes one small component of a vector that encodes the entire input's meaning. The overwhelming majority of the vector's capacity goes toward encoding topic, domain, and entity information.

**Prediction from first principles:** Any input pair that differs only in numerical values but shares context and vocabulary will produce near-identical embeddings. This applies to dates, percentages, counts, prices, and measurements. If your pipeline needs to distinguish "$50K salary" from "$500K salary," embeddings cannot do this — you need structured metadata filtering or BM25.

### Negation blindness stems from the dominance of shared content

"The treatment was effective" and "the treatment was not effective" score ~0.96 cosine similarity. A 2025 study (arXiv:2504.00584) tested all major embedding models and found that a sentence is more similar to its negation than to a random different sentence in **99.27% of cases**.

The mechanism is straightforward. In a sentence of 10 tokens, adding "not" changes one token out of eleven. Whether the model uses last-token pooling (OpenAI) or mean pooling (most open models), the final vector is dominated by the **10 shared tokens** encoding "treatment," "was," "effective," and their contextual interactions. The word "not" contributes a fraction of the total signal. Attention does contextualize "not" — the representation of "effective" shifts when "not" is present — but this shift is overwhelmed by the unchanged content.

The training data compounds this. Positive pairs in contrastive learning rarely differ only by negation. The model has seen "effective treatment" and "ineffective treatment" as topically related (both about treatment efficacy), not as contradictory. The same paper showed that the negation information *is encoded* in certain dimensions but is not surfaced by standard cosine similarity — a parameter-free "negation adapter" using dimension re-weighting can partially recover it.

**The antonym extension**: "effective" and "ineffective" are particularly close because BPE tokenizes them as `effective` vs `in` + `effective` — they share the dominant morpheme. Prefix-based negation ("un-," "in-," "non-") creates tokens with high overlap, further suppressing the distinction.

### Pooling destroys compositional order

"Dog bites man" and "man bites dog" contain identical tokens. The key question is: does the final vector preserve which noun is the agent and which is the patient?

**With mean pooling (most open models):** The operation is `(1/n) Σ hᵢ` — a commutative sum. If the contextual representations were completely position-agnostic, the results would be identical. Transformer attention *does* condition on position via positional encodings, so `h("dog" at position 0)` differs from `h("dog" at position 3)`. But the mean of the full sequence converges to very similar points because the same token set produces similar aggregate statistics.

**With last-token pooling (OpenAI):** The EOS token has attended to all preceding tokens with causal masking, so the order of preceding tokens does influence its representation. This provides somewhat better order sensitivity than mean pooling. But empirical evidence from the CLIP bag-of-words studies (Yuksekgonul et al., ICLR 2023) shows that contrastive models generally demonstrate "severe lack of order sensitivity" — the training objective doesn't penalize ignoring word order because most training pairs differ in topic, not just arrangement.

**The practical implication**: For your job search use case, word order sensitivity matters less than you might think. "Python developer with leadership experience" and "Leadership role requiring Python development" carry similar meaning and should reasonably match similar queries. Where it matters more — "reports to the VP of Engineering" versus "VP of Engineering reports to" — the failure is real but rare in natural job description text.

### Length dilution pushes vectors toward a generic centroid

You've observed this empirically: enriched job descriptions at 2,200–3,500 tokens dilute role-specific query matches versus bare JDs at 400–1,800 tokens. Here's the mechanism.

**Attention spreads thinner with more tokens.** Self-attention distributes weight across all tokens via softmax. As sequence length grows, each token's attention to any specific other token decreases. A 2024 paper ("Length-Induced Embedding Collapse," arXiv:2410.24200) showed formally that the attention mechanism acts as a **low-pass filter** that intensifies with sequence length — longer texts retain more low-frequency (common, generic) components while losing high-frequency (distinctive, specific) signals. The result: embeddings for longer texts cluster in a smaller central region of the space.

**With last-token pooling**, the EOS token must compress 2,500 tokens of information into the same 1,536-dimensional vector that would otherwise represent 500 tokens. The information density per input token in the output vector drops by 5×. Distinctive signals — the specific job title, the key technical requirement, the unusual skill combination — become proportionally smaller components of the final vector.

**With mean pooling**, the arithmetic is even more direct: each token contributes 1/n to the average. A critical keyword at token 47 contributes 1/500 in a short document but 1/2500 in a long one. The vector converges toward the centroid of the token distribution — which represents "average topic" rather than specific content.

**Why OpenAI implicitly recommends moderate length**: their documentation and benchmarks optimize for retrieval scenarios where documents are **passage-length** (a few hundred tokens). The GDELT Project's empirical testing confirmed that "all models strongly stratify sentences by length" and that "longer texts tend to cover more topics, yielding weaker similarity scores." Your observation — that enriched 2,500-token descriptions match worse than focused 800-token descriptions — is exactly what the mechanism predicts.

### The shallowness ceiling is mathematically proven

A landmark 2025 paper from Google DeepMind — "On the Theoretical Limitations of Embedding-Based Retrieval" (Weller et al., arXiv:2508.21038) — established formal bounds on what single-vector retrieval can represent, using sign-rank theory from communication complexity.

**The core result**: for a given embedding dimension *d*, there exist top-*k* document combinations that cannot be returned by *any* query vector, no matter how the embeddings are arranged. A **d=1536** model (text-embedding-3-small's dimension) breaks down at a specific document count — the paper estimates critical points at ~500K documents for d=512 and ~4M for d=1024, with the relationship being polynomial, not exponential. More dimensions help, but with diminishing returns.

The researchers created the **LIMIT dataset**: 46 trivially simple documents ("Ellis Smith likes apples"), 1,035 queries ("Who likes apples?"), with a dense relevance graph. State-of-the-art embedding models achieved **less than 20% Recall@100** — not because the language is hard, but because the combinatorial structure of relevance cannot be encoded in fixed-dimensional vectors.

**What single-vector similarity fundamentally cannot capture:**

- **Multi-aspect relevance**: a query requiring simultaneous matching on independent criteria ("Python developer with healthcare experience in Boston") cannot be well-served by a single distance measurement
- **Conditional relevance**: when a document's relevance depends on what else has been retrieved
- **Fine-grained token-level matching**: specific phrases buried in long documents get compressed away

This is why **hybrid search isn't a crutch — it's the correct architecture**. BM25 operates in a much higher-dimensional sparse space where exact term matching is preserved. Combining dense retrieval (semantic) with sparse retrieval (lexical) via reciprocal rank fusion typically improves recall by **15–30%** over either method alone. For production systems, the recommended pipeline is: bi-encoder retrieval → BM25 retrieval → fusion → cross-encoder reranking of top results.

---

## Part 3: What to feed the model

Every recommendation below traces to the mechanisms above.

### Strip formatting tokens — they don't earn their keep

Markdown syntax (`##`, `**`, `•`, `-`, `\n`) tokenizes into separate tokens that participate in self-attention and contribute to the final vector. They carry structural information that is irrelevant to semantic retrieval. The `##` before a heading consumes 2 tokens and adds nothing a query would match against. The bullet character `•` adds noise.

**Do this**: Convert `## Requirements\n- Python\n- SQL\n- AWS` into `"The requirements for this role include Python, SQL, and AWS."`

**Don't do this**: Aggressively strip everything. Keep natural punctuation (periods, commas) and paragraph structure — these help the model build grammatical context. **Do not remove stop words.** Modern transformer models use words like "the," "is," and "and" to build syntactic representations through attention. Removing them degrades performance — this is well-established practitioner consensus and a departure from older TF-IDF conventions.

### Prose outperforms lists for embedding quality

Converting structured data to flowing prose is the right instinct, and here's why it works mechanistically.

In a list format like `Requirements: Python, SQL, AWS`, the model has very few contextual tokens to work with. "Python" can only attend to "Requirements," a colon, and bare skill names. The resulting contextual representation for "Python" is semantically thin.

In prose like `"The ideal candidate has deep experience with Python programming, SQL database management, and AWS cloud services"`, each skill token attends to "experience," "candidate," "programming," "database management," "cloud services." These cross-token interactions build **richer contextual representations** that encode not just the skill name but its role in the job. This matters because queries are also in natural language — "looking for a Python backend developer" matches better against prose that contextualizes Python as a development skill than against a bare keyword list.

Harris et al. (2024) demonstrated this empirically: using GPT-3.5 to enrich and restructure sparse text inputs before embedding improved average precision from **81.52 to 85.34** on a retrieval benchmark — a significant gain from text reformulation alone.

### Translating structured data to natural language aligns with the training distribution

Your instinct to convert "$147B revenue, 311K employees" into "one of the largest companies in the United States" is correct, for two reasons.

**First**, the model's training data contains vastly more natural language descriptions of company scale than raw financial tables. Passages like "Apple, one of the world's largest technology companies with over 160,000 employees" appeared billions of times in training. Raw strings like `$147B` appeared rarely and in formats the tokenizer handles poorly (dollar sign, digit chunks, letter suffix — each as separate tokens with weak learned representations).

**Second**, qualitative descriptions match how users actually query. Someone searching for "large enterprise technology company" will match far better against prose describing company scale than against tokenized numbers. However, **if users might search with specific numbers** ("companies with $100B+ revenue"), you need hybrid search with BM25 or structured metadata filtering — embeddings cannot do numerical comparison.

### The length sweet spot is 200–800 tokens for retrieval

Based on the dilution mechanism, your preprocessing should target the range where information density per dimension is maximized:

**Below ~100 tokens**: insufficient context for robust semantic representation. Very short inputs have high variance — minor wording changes produce large vector shifts. Pinecone practitioners found that excluding text under 200 characters improved retrieval quality.

**200–800 tokens**: the sweet spot. Enough context for rich contextual representations. Each token makes a meaningful contribution to the final vector. Distinctive signals (specific job titles, technical requirements, unusual skill combinations) maintain significant weight.

**800–1,500 tokens**: still functional but dilution begins. Generic content (company boilerplate, benefits, legal disclaimers) starts competing with distinctive content for representation capacity.

**Above 2,000 tokens**: significant dilution, consistent with your empirical observation. The vector converges toward a generic topic centroid. Role-specific signals that a query needs to match against get proportionally diminished.

**Near the 8,191 token limit**: substantial degradation. The LongEmbed benchmark (EMNLP 2024) showed even the best models achieve only 64.4 points on average for long-context retrieval.

### Information density determines embedding quality

The guiding principle: **every token should earn its keep in the final vector.** A token like "the" contributes grammatical structure (useful). A token like "Inc." contributes almost nothing (noise). A token like "Kubernetes" contributes distinctive semantic signal (high value).

When combining company profile with job description, the risk is that company boilerplate — generic mission statements, benefits lists, legal disclaimers — dilutes the role-specific signal that queries need to match. Two approaches work:

**Concise integration**: Create a single focused text that weaves company context tightly with role specifics. "Senior Machine Learning Engineer at Meridian Health, a mid-size healthcare analytics company. Building clinical prediction models using Python, PyTorch, and large-scale patient data on AWS infrastructure." Every token here either identifies the role, the domain, or a technical requirement.

**Separate embeddings**: Store a role embedding and a company embedding as separate vectors. Query against both with different weights. This avoids dilution entirely at the cost of more complex retrieval logic.

### Four concrete examples

**Example 1: Raw job description (poor embedding input)**
```
## Senior Software Engineer

**Location:** San Francisco, CA | **Department:** Engineering

### About Us
Founded in 2015, TechCorp is a B2B SaaS company building the future
of enterprise collaboration. We've raised $50M in Series C funding...
[200 words of company history]

### Requirements
- Python
- SQL
- AWS
- 5+ years experience
- B.S. in Computer Science

### Benefits
- 401k matching | Unlimited PTO | Health/dental/vision

TechCorp is an equal opportunity employer...
```

**Why it's bad**: ~400+ tokens, ~40% consumed by markdown formatting, boilerplate, legal text, and benefits that no query will match against. The core signal (Python, SQL, AWS, senior-level engineer, SaaS company) is scattered across a diluted vector.

**Good normalized version** (~120 tokens):
```
Senior Software Engineer at TechCorp, a Series C B2B SaaS company
in San Francisco building enterprise collaboration tools. The role
involves backend development and cloud infrastructure using Python,
SQL databases, and Amazon Web Services. Requires at least five years
of professional software engineering experience with a computer
science background. The engineer works within the core engineering
team reporting to the VP of Engineering.
```

**Why it's good**: Every token carries signal. Skills are contextualized in prose. Acronyms are expanded ("Amazon Web Services"). Company context is integrated concisely (stage, domain, location). Formatting tokens eliminated. Information density is high.

**Example 2: Company data translation**

**Bad** (raw structured data):
```
Revenue: $147B | Employees: 311K | Fortune 50 | HQ: Bentonville, AR
Founded: 1962 | Industry: Retail | CEO: Doug McMillon
```

**Good** (natural language):
```
A Fortune 50 retail corporation and one of the world's largest
employers, headquartered in Bentonville, Arkansas. The company
generates over one hundred billion dollars in annual revenue and
employs more than three hundred thousand people across global
operations.
```

**Why the translation works**: BPE tokenizes `$147B` as dollar sign + digit tokens + letter — weak representations. But "one of the world's largest" activates well-trained semantic clusters from billions of training examples. Queries like "large enterprise company" or "major retailer" match the natural language description far better than tokenized financial figures.

**Example 3: Enriched description (too long — dilution risk)**

**Bad** (~2,800 tokens):
```
Senior Software Engineer at TechCorp. [200 words of role description]
[300 words of company history and mission] [200 words of team
structure and culture] [150 words of technical stack details]
[200 words of growth trajectory and funding] [150 words of benefits
and perks] [100 words of interview process] [200 words of DEI
statement and legal disclaimers]
```

**Why it's bad**: The role-specific signal (title, skills, level, domain) is perhaps 200 tokens out of 2,800. The vector represents "a technology company that exists and has a culture and benefits" rather than "a senior Python engineer role building SaaS infrastructure."

**Good** (focused ~300 tokens):
```
Senior Software Engineer at TechCorp, a high-growth Series C SaaS
company with 200 employees building enterprise collaboration
software in San Francisco. [Core role description: what the engineer
builds, what technologies they use, what team they join — 150 words]
[Key technical requirements with context — 80 words]
```

**Example 4: Query-document mismatch**

**Query**: "machine learning engineer healthcare"

**Bad document embedding**: A 2,000-word job description that mentions "machine learning" once in paragraph 6 and "healthcare" in the company description section, surrounded by extensive content about the company's 401k plan, office locations, and reporting structure.

**Good document embedding**: "Machine Learning Engineer at Meridian Health, building clinical prediction models for patient outcomes using Python, PyTorch, and healthcare data infrastructure."

The good version puts the query-relevant terms in dense, contextualized proximity. The model builds strong cross-token representations between "machine learning," "healthcare," "clinical," and "prediction models" because they attend to each other directly.

---

## Part 4: How to know if your embeddings are working

### Designing test suites for similarity matrix evaluation

Your CLI tool that prints cosine similarity matrices is the right evaluation approach. The key is designing the test suite well. A good suite needs four categories:

**True positives**: 10–20 query-document pairs where you know the match is correct. "Machine learning engineer" → job description for an ML engineer role. These establish your upper bound.

**True negatives**: 10–20 pairs where the match would be wrong. "Machine learning engineer" → job description for a marketing manager. These establish your lower bound.

**Hard negatives**: 10–20 pairs that are topically adjacent but wrong. "Machine learning engineer" → job description for a data analyst with no ML component. "Senior Python developer" → job description for a junior Python developer. These test discrimination within the same domain — the most important category.

**Edge cases**: Negation ("not remote" vs "remote"), numerical ("5 years" vs "15 years"), short queries ("python engineer" — just two tokens), long queries (a full paragraph describing an ideal role).

Look at the **full matrix**, not individual pairs. The signal you want: true positives score distinctly higher than hard negatives, with a visible gap. If hard negatives score within 0.02 of true positives, your text normalization isn't creating enough discrimination.

### When score deltas matter

A 0.03 cosine similarity delta in text-embedding-3-small operates in an effective range of roughly 0.0–0.7, making it about 4% of the usable range. **This is borderline** — potentially meaningful for a single pair but not reliable without testing across many pairs.

The more reliable signal is **rank changes**. If a preprocessing change causes the correct document to move from rank 2 to rank 5 across multiple test queries, that's real degradation regardless of absolute score movement. If it moves from rank 3 to rank 1 while absolute scores drop slightly, that's improved discrimination — the model is being more selective, pulling the correct match ahead of hard negatives.

One important caveat: **OpenAI embedding models are not fully deterministic.** Community testing found that embedding the same 600-token text 10 times produced 3 unique vector variants, though cosine similarity between variants was typically ≥0.999. For evaluation, embed your test corpus once and compare against it consistently.

### Dilution versus better discrimination

You encountered this: enriched text scored lower on some queries, and you wondered whether it was dilution or more honest distinction-making. Here's how to tell:

**It's dilution if**: The correct document drops in rank relative to competitors across multiple queries. The enriched embedding is closer to generic topic centroids (you can test this by computing similarity to a clearly generic query like "job opening at a company"). Recall@K drops when you test on a broader set.

**It's better discrimination if**: The correct document's absolute score drops but its *rank* improves or holds steady. The gap between the correct document and the top hard negative widens. Precision@K improves even if some absolute scores decrease.

The diagnostic: build a test set of 50+ queries with known relevant documents, compute NDCG@10 and Recall@10 before and after the enrichment change. If NDCG improves (or holds) while some absolute scores drop, discrimination improved. If NDCG drops, you have dilution. **NDCG@10 is the single best metric** for this evaluation because it penalizes relevant documents appearing lower in the ranking.

### Building an evaluation dataset

A good evaluation dataset for your job search system should include:

**50–100 queries** drawn from real user search behavior, covering different specificity levels (broad: "engineering roles"; specific: "senior Kubernetes platform engineer in fintech"), different facets (skills, seniority, industry, location), and edge cases (negation, numerical requirements).

**Graded relevance labels** (0 = irrelevant, 1 = marginally relevant, 2 = highly relevant) for each query-document pair. This enables NDCG computation, which is more informative than binary precision/recall.

**Hard negatives for every query**: For "senior Python developer," include the junior Python role, the senior Java role, and the Python data analyst role. These are what separate a good embedding pipeline from a mediocre one.

**Refresh regularly**: Pull new queries from production logs. As your text normalization evolves, re-label high-impact slices. Version your evaluation dataset alongside your code.

Consider using an LLM to generate initial relevance labels at scale, then validate a sample with human judgment. This is a cost-effective way to build a 500+ pair dataset without manual labeling of every pair.

---

## Conclusion: the mental model that predicts everything

The entire behavior of text-embedding-3-small follows from three facts: (1) BPE tokenization converts text to arbitrary symbol sequences with no mathematical structure for numbers; (2) contrastive training on text pairs teaches topical co-occurrence, not logical reasoning; (3) compressing a variable-length token sequence into a fixed 1,536-dimensional vector is a lossy bottleneck that preserves dominant themes and discards fine-grained distinctions.

From these three facts, you can predict every failure mode and every best practice. Numbers fail because tokenization has no magnitude and training has no numerical discrimination. Negation fails because one token can't override the dominant topical signal. Length dilutes because the bottleneck is fixed-size. Prose beats lists because cross-token attention builds richer representations when context tokens are present. Stripping boilerplate helps because it removes tokens that consume representation capacity without contributing retrieval-relevant signal.

**The architecture tells you where embeddings end and other tools begin.** Use embeddings as a fast first-stage retrieval mechanism that captures topical relevance. Use BM25 in parallel for exact term matching, numerical values, and negation-sensitive queries. Use cross-encoder reranking on top results for fine-grained relevance judgments. Use structured metadata filtering for attributes (location, salary range, seniority level) that embeddings cannot reliably encode.

Your pipeline's LLM-based normalization step is the right idea — it's the leverage point where you control what information enters the bottleneck. The goal is maximum information density: every token in the normalized text should be something a realistic query might match against. Strip the boilerplate, convert structure to prose, contextualize skills with descriptive language, integrate company context concisely, and target 300–800 tokens. Then validate with NDCG@10 on a hard-negative-rich evaluation set.

The embedding model is a compression algorithm with known lossy properties. Once you understand what it preserves and what it discards, you stop being surprised by its behavior and start designing inputs that work *with* the mechanism rather than against it.