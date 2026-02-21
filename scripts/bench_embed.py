"""Benchmark Azure OpenAI embedding API with various batch sizes.

Per-trial timing + rate limit header capture to identify throttling.
"""

import time
import statistics
import httpx
from jobbuddy.settings import get_settings
from jobbuddy.store import JobStore
from jobbuddy.embeddings import _get_client, embed_texts, serialize_f32, DEPLOYMENT_NAME
from jobbuddy.models import Job

BATCH_SIZES = [50, 100]
TRIALS = 3


def main():
    settings = get_settings()
    store = JobStore(settings.db_path)
    client = _get_client()

    # Grab real rows with stripped descriptions
    rows = store.list_jobs_needing_embeddings(limit=250)
    if len(rows) < 250:
        with store._connect() as conn:
            cur = conn.execute("""
                SELECT j.job_id, j.title, j.location, j.department,
                       j.description_stripped, c.slug as company_slug
                FROM jobs j JOIN companies c ON j.company_id = c.id
                WHERE j.description_stripped IS NOT NULL LIMIT 250
            """)
            rows = [dict(r) for r in cur.fetchall()]

    # Pre-build texts
    all_texts = []
    for r in rows:
        job = Job(id=r["job_id"], title=r["title"],
                  location=r["location"] or "", url="", apply_url="",
                  department=r.get("department"))
        text = job.embed_text(r["company_slug"],
                              description_stripped=r["description_stripped"])
        if text:
            all_texts.append(text)

    print(f"Loaded {len(all_texts)} texts")
    avg_chars = statistics.mean(len(t) for t in all_texts[:50])
    print(f"Avg chars (first 50): {avg_chars:.0f} (~{avg_chars/4:.0f} tokens est)\n")

    for size in BATCH_SIZES:
        if size > len(all_texts):
            print(f"\n=== Batch {size}: not enough texts ===")
            continue

        batch = all_texts[:size]
        print(f"\n=== Batch {size} ({size * 1438} est tokens) ===")

        for trial in range(TRIALS):
            # --- Raw API with httpx to capture headers ---
            t0 = time.perf_counter()
            resp = client.embeddings.with_raw_response.create(
                input=batch, model=DEPLOYMENT_NAME
            )
            t_api = time.perf_counter() - t0

            # Parse the response
            t1 = time.perf_counter()
            parsed = resp.parse()
            t_parse = time.perf_counter() - t1

            tokens = parsed.usage.total_tokens if parsed.usage else 0
            headers = resp.headers

            # Rate limit headers
            rl_remaining = headers.get("x-ratelimit-remaining-requests", "?")
            rl_remaining_tok = headers.get("x-ratelimit-remaining-tokens", "?")
            rl_reset = headers.get("x-ratelimit-reset-requests", "?")
            rl_reset_tok = headers.get("x-ratelimit-reset-tokens", "?")
            retry_after = headers.get("retry-after", None)

            # --- Wrapper overhead (text build + embed_texts + serialize) ---
            batch_rows = rows[:size]
            t2 = time.perf_counter()
            texts = []
            for r in batch_rows:
                job = Job(id=r["job_id"], title=r["title"],
                          location=r["location"] or "", url="", apply_url="",
                          department=r.get("department"))
                text = job.embed_text(r["company_slug"],
                                      description_stripped=r["description_stripped"])
                if text:
                    texts.append(text)
            t_build = time.perf_counter() - t2

            t3 = time.perf_counter()
            vectors, _ = embed_texts(texts)
            t_embed_wrapper = time.perf_counter() - t3

            t4 = time.perf_counter()
            for vec in vectors:
                serialize_f32(vec)
            t_serialize = time.perf_counter() - t4

            print(f"  Trial {trial+1}:")
            print(f"    API call:     {t_api:>7.3f}s  ({tokens:,} tokens)")
            print(f"    Parse resp:   {t_parse:>7.3f}s")
            print(f"    Build texts:  {t_build:>7.3f}s")
            print(f"    embed_texts:  {t_embed_wrapper:>7.3f}s  (wrapper)")
            print(f"    Serialize:    {t_serialize:>7.3f}s")
            print(f"    Rate limits:  reqs_left={rl_remaining}  toks_left={rl_remaining_tok}")
            print(f"                  req_reset={rl_reset}  tok_reset={rl_reset_tok}")
            if retry_after:
                print(f"    RETRY-AFTER:  {retry_after}")

            time.sleep(0.5)  # breathing room between trials


if __name__ == "__main__":
    main()
