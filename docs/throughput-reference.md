# Azure AI Throughput Estimation Reference

> These are example values from the author's Azure OpenAI deployment. Your throughput will vary based on your provider, tier, and rate limits.

## Variant 1: LLM Processing (e.g., cleaning/summarizing with a chat model)

### Inputs

| Variable | Description | Example Source |
|---|---|---|
| `TOTAL_JOBS` | Number of items to process | Count of records in your dataset |
| `AVG_INPUT_TOKENS` | Average input tokens per request (system prompt + user content) | Measure from a sample batch |
| `AVG_MAX_TOKENS` | max_tokens you'll set per request (tip: set to expected output size, not model max) | Estimate from sample outputs |
| `AVG_COMPLETION_TOKENS` | Average actual completion tokens per request | Measure from a sample batch |
| `AVG_LATENCY_SEC` | Average wall-clock time per request in seconds | Measure from a sample batch |
| `DEPLOYMENT_TPM` | Tokens per minute limit from deployment config | `rateLimits[key=token].count` where `renewalPeriod=60` |
| `DEPLOYMENT_RPM` | Requests per minute limit from deployment config | `rateLimits[key=request].count`, normalized to per-minute |
| `CAPACITY_FACTOR` | Fraction of limits to target (headroom for retries) | e.g., 0.90 |

### Math

```
# What Azure "charges" against your TPM budget per request (at request time)
estimated_tpm_per_request = AVG_INPUT_TOKENS + AVG_MAX_TOKENS

# Effective limits (with headroom)
effective_tpm = DEPLOYMENT_TPM × CAPACITY_FACTOR
effective_rpm = DEPLOYMENT_RPM × CAPACITY_FACTOR

# How many jobs per minute each ceiling allows
jobs_per_min_tpm = effective_tpm ÷ estimated_tpm_per_request
jobs_per_min_rpm = effective_rpm
jobs_per_min = min(jobs_per_min_tpm, jobs_per_min_rpm)

# How many concurrent workers needed to sustain that throughput
workers_needed = ceil(jobs_per_min × AVG_LATENCY_SEC ÷ 60)

# Wall-clock time
estimated_minutes = TOTAL_JOBS ÷ jobs_per_min
```

### Outputs

| Output | Formula |
|---|---|
| **Required workers** | `workers_needed` |
| **Estimated completion** | `estimated_minutes` |
| **Binding constraint** | Whichever of TPM or RPM produced the lower `jobs_per_min` |

### Notes

- Azure estimates token consumption at **request time** using `input_tokens + max_tokens`, not actual output. Setting `max_tokens` too high wastes your TPM budget. Set it to expected output size.
- **Reasoning models** (gpt-5-*): `max_completion_tokens` must cover both visible output *and* reasoning tokens. For gpt-5-nano at `reasoning_effort=low`, p99 reasoning overhead is ~1,200 tokens. The strip phase uses `max(len/4, 256) + 1200` — factor that full value into `AVG_MAX_TOKENS` for throughput estimates.
- If `workers_needed` exceeds ~500, consider async (asyncio + httpx) instead of thread pools.
- If TPM is the bottleneck and you need more speed, deploy across multiple regions. Quota is per-region, per-subscription. Total throughput scales linearly with region count.

---

## Variant 2: Embedding (e.g., vectorizing text with an embedding model)

### Inputs

| Variable | Description | Example Source |
|---|---|---|
| `TOTAL_JOBS` | Number of items to embed | Count of records in your dataset |
| `AVG_TOKENS_PER_JOB` | Average tokens per text to embed | Measure from dataset |
| `DEPLOYMENT_TPM` | Tokens per minute limit | `rateLimits[key=token].count` where `renewalPeriod=60` |
| `DEPLOYMENT_RPM` | Requests per minute limit | `rateLimits[key=request].count`, normalized to per-minute |
| `MAX_BATCH_SIZE` | Max texts per API call | 2,048 for OpenAI; 96 for Cohere |
| `CAPACITY_FACTOR` | Fraction of limits to target | e.g., 0.90 |

### Math

```
# Effective limits
effective_tpm = DEPLOYMENT_TPM × CAPACITY_FACTOR
effective_rpm = DEPLOYMENT_RPM × CAPACITY_FACTOR

# Batch sizing: target ~25% of TPM per batch (allows ~4 batches/min with retry room)
target_tokens_per_batch = effective_tpm × 0.25
jobs_per_batch = min(floor(target_tokens_per_batch ÷ AVG_TOKENS_PER_JOB), MAX_BATCH_SIZE)
actual_tokens_per_batch = jobs_per_batch × AVG_TOKENS_PER_JOB

# How many batches per minute each ceiling allows
batches_per_min_tpm = effective_tpm ÷ actual_tokens_per_batch
batches_per_min_rpm = effective_rpm
batches_per_min = min(batches_per_min_tpm, batches_per_min_rpm)

# Jobs per minute
jobs_per_min = batches_per_min × jobs_per_batch

# Workers: latency is low (~200-500ms), so few workers needed
workers_needed = ceil(batches_per_min ÷ 60 × AVG_LATENCY_SEC)  # usually 3-4

# Wall-clock time
total_batches = ceil(TOTAL_JOBS ÷ jobs_per_batch)
estimated_minutes = total_batches ÷ batches_per_min
```

### Outputs

| Output | Formula |
|---|---|
| **Batch size** | `jobs_per_batch` |
| **Required workers** | `workers_needed` (typically 3-4) |
| **Estimated completion** | `estimated_minutes` |
| **Binding constraint** | Almost always TPM for embedding workloads |

### Notes

- Embeddings are input-only — no completion tokens. TPM = total input tokens across all texts in the batch.
- One batch of N texts = 1 request (good for RPM) but N × tokens (counts against TPM).
- Latency is low enough that TPM is nearly always the sole bottleneck. Workers just need to keep the pipe full.
- Multi-region trick works here too. Time scales as `estimated_minutes ÷ NUM_REGIONS`.

---

## Deployment Reference Values

### gpt-5-nano (GlobalStandard)

```
DEPLOYMENT_TPM = 5,000,000
DEPLOYMENT_RPM = 5,000
INPUT_COST     = $0.05 / 1M tokens
OUTPUT_COST    = $0.40 / 1M tokens
NUM_REGIONS    = 23
```

### text-embedding-3-small (GlobalStandard)

```
DEPLOYMENT_TPM = 1,000,000
DEPLOYMENT_RPM = 6,000  (1,000 per 10s)
MAX_BATCH_SIZE = 2,048
INPUT_COST     = $0.02 / 1M tokens
NUM_REGIONS    = 23
```

### text-embedding-3-large (GlobalStandard)

```
DEPLOYMENT_TPM = 1,000,000
DEPLOYMENT_RPM = 6,000  (1,000 per 10s)
MAX_BATCH_SIZE = 2,048
INPUT_COST     = $0.13 / 1M tokens
NUM_REGIONS    = 23
```

---

## Multi-Region Scaling (applies to both variants)

```
estimated_minutes_multi = estimated_minutes ÷ NUM_REGIONS
```

Each region has independent quota. Partition your job list into NUM_REGIONS chunks and run them in parallel against separate regional endpoints. No coordination needed between regions.
