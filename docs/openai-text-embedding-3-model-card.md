# OpenAI text-embedding-3 Model Card

> **Last updated:** March 2026
> **Models covered:** `text-embedding-3-small`, `text-embedding-3-large`
> **Release date:** January 25, 2024
> **Primary sources:** [OpenAI Embedding Guide](https://platform.openai.com/docs/guides/embeddings), [v3 Launch Blog Post](https://openai.com/index/new-embedding-models-and-api-updates/)

---

## Quick Reference

| Property | text-embedding-3-small | text-embedding-3-large |
|---|---|---|
| Max input tokens | 8,191 | 8,191 |
| Default output dimensions | 1,536 | 3,072 |
| Supports dimension reduction | Yes (via `dimensions` param) | Yes (via `dimensions` param) |
| MTEB avg (English tasks) | 62.3% | 64.6% |
| MIRACL avg (multilingual retrieval) | 44.0% | 54.9% |
| Knowledge cutoff | September 2021 | September 2021 |
| Tokenizer | cl100k_base | cl100k_base |
| Output normalization | L2-normalized (length 1) | L2-normalized (length 1) |
| Recommended distance metric | Cosine similarity (or dot product, equivalent for normalized vectors) | Same |

---

## Pricing

### OpenAI Direct API (as of March 2026)

| Model | Standard (per 1M tokens) | Batch API (per 1M tokens) |
|---|---|---|
| text-embedding-3-small | $0.02 | $0.01 |
| text-embedding-3-large | $0.13 | $0.065 |

**Note:** The Batch API provides a 50% discount with results returned within 24 hours. Embedding requests are billed only on **input tokens** — there are no output token charges.

> ⚠️ There was a reported discrepancy (Aug 2025) between OpenAI's model card page showing $0.13/1M for large and the pricing page showing $0.065/1M. The $0.065 figure appears to be the **batch** rate. Verify current pricing at [platform.openai.com/docs/pricing](https://platform.openai.com/docs/pricing).

### Azure OpenAI (pay-as-you-go)

| Model | Per 1,000 tokens |
|---|---|
| text-embedding-3-small | $0.000022 |
| text-embedding-3-large | $0.000143 |

Azure pricing matches OpenAI direct on a per-token basis ($0.022/1M and $0.143/1M respectively). Total Azure cost may run higher due to infrastructure, support plans, and data transfer — one estimate puts overhead at 15–40% above base token pricing for enterprise deployments. Verify current pricing at [azure.microsoft.com/en-us/pricing/details/azure-openai](https://azure.microsoft.com/en-us/pricing/details/azure-openai/).

### Cost Comparison: small vs large

At standard OpenAI pricing, `text-embedding-3-large` costs **6.5x** more per token than `small`. For context, assuming ~800 tokens per page of text:

| Model | ~Pages per $1.00 |
|---|---|
| text-embedding-3-small | 62,500 |
| text-embedding-3-large | 9,615 |

---

## Dimension Reduction (Matryoshka Representation Learning)

Both v3 models were trained using [Matryoshka Representation Learning](https://arxiv.org/abs/2205.13147), which means the embedding vectors encode information in a front-loaded manner — earlier dimensions carry more semantic weight than later ones. This allows truncation of the vector without retraining.

You can pass the `dimensions` parameter at request time to get a shorter embedding. If you truncate manually after the fact, you **must re-normalize to unit length**.

### MTEB Scores at Various Dimensions

| Model | 256 dims | 512 dims | 1,024 dims | 1,536 dims | 3,072 dims |
|---|---|---|---|---|---|
| text-embedding-3-small | — | 61.6 | — | **62.3** | — |
| text-embedding-3-large | 62.0 | — | 64.1 | — | **64.6** |

Key takeaway: `text-embedding-3-large` truncated to **256 dimensions** (62.0 MTEB) still outperforms the full 1,536-dimension `text-embedding-ada-002` (61.0 MTEB). This is the headline claim for the Matryoshka approach.

### When to use dimension reduction

- **Storage-constrained environments:** Fewer dimensions = smaller vectors = less memory/disk. A 256-dim float32 vector is 1 KB vs 12 KB for a full 3,072-dim vector.
- **Vector DB limitations:** Some vector stores cap dimensionality (e.g., at 1,024). You can still use `large` and truncate.
- **Latency-sensitive retrieval:** Shorter vectors = faster distance computation at query time.

---

## What These Models Are Good For

These are general-purpose text embedding models. OpenAI's documentation lists the following use cases:

- **Search / retrieval:** Rank results by semantic relevance to a query. This is the core RAG use case.
- **Clustering:** Group text by semantic similarity.
- **Recommendations:** Find items with related text.
- **Anomaly detection:** Identify outliers with low similarity to the corpus.
- **Diversity measurement:** Analyze similarity distributions.
- **Classification:** Classify text by nearest label embedding (zero-shot or few-shot).
- **Feature encoding for ML:** Use embeddings as input features for downstream models (regression, classification, etc.).

---

## What These Models Are NOT Good For

- **No knowledge of events after September 2021.** OpenAI explicitly states this. For domains where recency matters (news, current events, recent product names), the embeddings may not capture newer concepts well. This is generally less of an issue for embeddings than for generative models, but can affect edge cases.
- **Not code-specialized.** They handle code (OpenAI shows a code search example), but they are not purpose-built code embedding models. Dedicated code models may outperform for pure code similarity tasks.
- **Not cross-modal.** These are text-only. They do not embed images, audio, or other modalities.
- **Not a reranker.** These produce a single-pass embedding. They do not perform cross-attention between query and document the way a cross-encoder reranker would. For high-precision retrieval, a two-stage pipeline (embedding retrieval → cross-encoder reranking) is standard.
- **Max 8,191 tokens per request.** Longer documents must be chunked. The model does not internally handle pagination or summarization — chunk strategy is your responsibility.

---

## Behavioral Notes

### Fundamentals

- **Embeddings are L2-normalized** (unit length). This means cosine similarity can be computed as a simple dot product, and cosine similarity and Euclidean distance produce identical rankings.
- **Deterministic outputs.** For the same input text and model, you get the same embedding vector. No temperature, no sampling — it's a pure function of the input string.
- **No fine-tuning available.** Unlike OpenAI's generative models, the embedding models cannot be fine-tuned. What you get is what you get.
- **Token counting:** Use the `cl100k_base` encoding via [tiktoken](https://github.com/openai/tiktoken) to count tokens before sending requests.
- **Encoding format:** The API returns `float` (32-bit single precision) by default. You can also request `base64` for a more compact wire format.

### Symmetric Embedding Space (No Query/Document Prefixes)

Unlike some open-source models (e.g., BGE, E5) that use instruction prefixes like `"Represent this sentence for retrieval:"` to differentiate query vs. document embeddings, the OpenAI v3 models use a **single symmetric embedding space**. Queries and documents are embedded identically — there is no `task_type` parameter, no query prefix, and no asymmetric mode.

This means the same model call is used regardless of whether you're embedding a search query or a corpus document. It simplifies usage but also means the model cannot specialize its embedding strategy based on whether the input is a short query or a long passage.

### Relationship Between `small` and `large`

Community testing (confirmed by multiple independent experiments, sourced from OpenAI Developer Forum, Jan 2024) has demonstrated that:

- **Within a single model**, the lower-dimensional embedding requested via the `dimensions` parameter is literally the first N dimensions of the full-size embedding, re-normalized to unit length. This is the Matryoshka property — not an approximation, the cosine similarity between a `dimensions=1024` request and a manually-truncated-then-renormalized 3072-dim embedding is ~0.9999993.
- **Between `small` and `large`**, this does NOT hold. They are different models with different embedding spaces. The first 1536 dims of `large` are unrelated to the 1536 dims of `small`. You cannot mix embeddings across models.
- **Practical implication:** If you anticipate needing multiple dimensionalities for the same corpus (e.g., a fast low-dim index for coarse filtering and a full-dim index for precision), you can embed once at max dimensions and derive smaller versions offline by truncation + L2 renormalization. This saves API costs.

### Similarity Score Distribution (Thresholds)

The v3 models have a significantly different similarity score distribution than `ada-002`:

- **ada-002** had a notoriously narrow similarity cone — unrelated texts often scored ~0.7–0.8 cosine similarity, with truly relevant matches around 0.85–0.95. This made threshold-based filtering tricky.
- **v3 models produce a wider spread.** Community testing shows unrelated text pairs scoring as low as 0.02–0.05 on `text-embedding-3-large`, with related pairs significantly higher. The embedding space appears more isotropic (less concentrated in a narrow cone).
- **If migrating from ada-002, you must recalibrate your similarity thresholds.** A threshold of 0.8 that worked for ada-002 will be far too high for v3 models and will filter out nearly everything.

### Sensitivity to Input Formatting

- **Punctuation sensitivity:** Embedding models (including ada-002 and v3) are sensitive to punctuation. Adding or removing a period at the end of a query can change which documents rank in the top-k. This is not a bug — punctuation is tokenized and contributes to the embedding. Documented via community reports and a LangChain issue (#14346).
- **Case sensitivity:** The models are case-aware. "Machine Learning" and "machine learning" produce slightly different embeddings. In practice, this rarely flips top results for well-formed queries, but can matter at the margins. General recommendation: don't normalize to lowercase — the model uses case as a signal.
- **Whitespace and newlines:** The model treats `\n` as a token. OpenAI's own cookbook replaces newlines with spaces before embedding (`text.replace("\n", " ")`). Inconsistent whitespace between your corpus and queries can degrade retrieval quality.
- **Empty or near-empty strings:** Embedding an empty string or very short input (1-2 tokens) produces a valid vector but it will be semantically noisy / low-information. Guard against embedding blank fields.

### Text Length and Chunking Behavior

- **No internal chunking.** If you exceed 8,191 tokens, the API returns an error — it does not silently truncate. You must handle chunking yourself.
- **Longer ≠ always better.** Very long inputs (close to the 8,191 limit) dilute the embedding across many concepts. For retrieval tasks, practitioners consistently find that chunks of 200–1000 tokens (roughly a paragraph to a page) with some overlap produce better retrieval than embedding entire documents.
- **Short queries vs. long documents.** Because these models are symmetric (no query/doc distinction), there's a natural length mismatch between a 5-word query and a 500-word passage. This is a known weakness of single-encoder models. A cross-encoder reranker as a second stage largely solves this.
- **Chunk overlap:** When splitting documents, 10-20% token overlap between chunks helps preserve context at boundaries. This is a chunking strategy concern, not a model behavior, but it significantly impacts retrieval quality with these models.

### Context-Dependent vs. Context-Free

- **These are contextual embeddings.** The same word gets different vectors depending on surrounding text (unlike GloVe or Word2Vec). "Apple" in "Apple announced new products" embeds differently from "Apple" in "I ate an apple."
- **However, the v3 models do NOT have a retrieval-specific attention mechanism.** They embed the full input as a single vector. They cannot attend to a query while reading a document (that's what cross-encoders do). Think of them as producing a "summary vector" of the input's semantic content.

### Version Pinning and Stability

- **Snapshots are available.** Both models support version pinning via snapshot aliases so you can lock behavior for production consistency. If you use the unpinned model name (`text-embedding-3-small`), OpenAI could theoretically update the model (they haven't yet as of March 2026, but the mechanism exists).
- **Re-indexing required on model change.** If you ever switch models (small→large, or if OpenAI updates a snapshot), you must re-embed your entire corpus. Embeddings from different models or versions are not comparable.

### Accuracy vs. Query Type (Empirical Findings)

Independent RAG evaluations (Tiger Data, Apr 2025) tested retrieval accuracy across query types:

| Query Type | Accuracy Range (across models) | Notes |
|---|---|---|
| Detailed / specific questions | 88–97% | All models do well here |
| Context-dependent questions | 75–89% | `large` showed the biggest advantage (~88.8%) |
| Vague / ambiguous questions | 42–57% | Challenging for all embedding models |

The `large` model's advantage is most pronounced on questions requiring contextual understanding — exactly the kind of query where the extra dimensions capture subtler semantic relationships. For straightforward factual retrieval, `small` performs nearly as well.

---

## small vs large: Decision Framework

| Factor | Favors `small` | Favors `large` |
|---|---|---|
| Cost sensitivity | ✅ 6.5x cheaper | |
| Storage / memory | ✅ Half the vector size at default dims | |
| Latency | ✅ Smaller vectors, faster retrieval | |
| English retrieval quality | | ✅ +2.3 MTEB points |
| Multilingual retrieval quality | | ✅ +10.9 MIRACL points |
| Dimension flexibility | Both support Matryoshka truncation | ✅ More headroom to truncate and still beat `small` |

### Rules of thumb

- If your corpus is primarily **English** and your retrieval quality with `small` is acceptable, the 2.3-point MTEB gap rarely justifies 6.5x the cost at scale.
- If you have **multilingual content**, `large` has a dramatically better MIRACL score (54.9% vs 44.0%) and is worth serious consideration.
- If you want the **best of both worlds**, consider `large` truncated to 1,024 dimensions — you get most of `large`'s quality (64.1 MTEB) at 1/3 the storage cost of full 3,072 dims, though you still pay the per-token embedding cost.

---

## Sources

| Source | URL |
|---|---|
| OpenAI Embeddings Guide | https://platform.openai.com/docs/guides/embeddings |
| v3 Launch Blog Post (Jan 2024) | https://openai.com/index/new-embedding-models-and-api-updates/ |
| OpenAI Pricing | https://platform.openai.com/docs/pricing |
| Azure OpenAI Pricing | https://azure.microsoft.com/en-us/pricing/details/azure-openai/ |
| Matryoshka Representation Learning (paper) | https://arxiv.org/abs/2205.13147 |
| MTEB Leaderboard | https://huggingface.co/spaces/mteb/leaderboard |
| MIRACL Benchmark | https://project-miracl.github.io/ |
| Embeddings FAQ | https://help.openai.com/en/articles/6824809-embeddings-faq |
| OpenAI Cookbook: Embedding Long Inputs | https://cookbook.openai.com/examples/embedding_long_inputs |
| Pinecone: OpenAI Embeddings v3 | https://www.pinecone.io/learn/openai-embeddings-v3/ |
| Community: Truncation/scaling discovery | https://community.openai.com/t/it-looks-like-text-embedding-3-embeddings-are-truncated-scaled-versions-from-higher-dim-version/602276 |
| Community: Punctuation sensitivity | https://community.openai.com/t/embedding-very-sensitive-to-punctuation/546205 |
| Tiger Data: Open-Source vs OpenAI Embeddings for RAG | https://www.tigerdata.com/blog/open-source-vs-openai-embeddings-for-rag |
| Microsoft: Kernel Memory discussion (threshold recalibration) | https://github.com/microsoft/kernel-memory/discussions/542 |
