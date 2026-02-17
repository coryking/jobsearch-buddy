"""Flask web UI for interactive semantic job search."""

from flask import Flask, render_template, request


def create_app() -> Flask:
    """Create and configure the Flask app."""
    from jobbuddy.search import VectorSearch

    app = Flask(__name__, template_folder="templates")
    vs = VectorSearch()

    @app.teardown_appcontext
    def close_search(exc):
        vs.close()

    @app.route("/")
    def index():
        query = request.args.get("q", "").strip()
        model = request.args.get("model", "bge_small")
        compare = request.args.get("compare", "") == "1"
        results = []
        compare_results = {}
        error = None
        models = vs.available_models

        if query:
            try:
                if compare:
                    raw = vs.search_all_models(query)
                    # Merge into unified rows: one per unique job, scores per model
                    jobs_seen = {}  # job_id → row dict
                    for model_key, model_results in raw.items():
                        for r in model_results:
                            jid = r.job["id"]
                            if jid not in jobs_seen:
                                jobs_seen[jid] = {
                                    "title": r.job.get("title", ""),
                                    "company": r.job.get("company_slug", ""),
                                    "location": r.job.get("location") or "",
                                    "department": r.job.get("department") or r.job.get("team") or "",
                                    "url": r.job.get("url") or "",
                                    "scores": {},
                                    "max_score": 0,
                                }
                            score = round(r.score * 100, 1)
                            jobs_seen[jid]["scores"][model_key] = score
                            if score > jobs_seen[jid]["max_score"]:
                                jobs_seen[jid]["max_score"] = score

                    # Sort by max score across models
                    compare_results = sorted(
                        jobs_seen.values(),
                        key=lambda x: x["max_score"],
                        reverse=True,
                    )
                else:
                    raw = vs.search(query, model_key=model)
                    for r in raw:
                        results.append({
                            "score": round(r.score * 100, 1),
                            "title": r.job.get("title", ""),
                            "company": r.job.get("company_slug", ""),
                            "location": r.job.get("location") or "",
                            "department": r.job.get("department") or r.job.get("team") or "",
                            "url": r.job.get("url") or "",
                        })
            except Exception as e:
                error = str(e)

        return render_template(
            "search.html",
            query=query,
            results=results,
            compare_results=compare_results,
            compare=compare,
            model=model,
            models=models,
            error=error,
        )

    return app
