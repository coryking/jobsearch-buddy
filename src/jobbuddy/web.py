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
        results = []
        error = None

        if query:
            try:
                raw = vs.search(query=query)
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
            error=error,
        )

    return app
