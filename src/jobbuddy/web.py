"""Flask web UI for interactive semantic job search."""

from flask import Flask, render_template, request


def create_app() -> Flask:
    """Create and configure the Flask app."""
    import os

    app = Flask(__name__, template_folder="templates")

    @app.route("/")
    def index():
        query = request.args.get("q", "").strip()
        results = []
        error = None

        if query:
            try:
                from jobbuddy import embeddings
                from jobbuddy.cache import cache_exists, get_connection, semantic_search

                if not cache_exists():
                    error = "No cached data — run 'ats sync' to populate the database."
                    return render_template("search.html", query=query, results=results, error=error)

                vec = embeddings.embed_query(query)
                serialized = embeddings.serialize_f32(vec)
                conn = get_connection()
                try:
                    rows = semantic_search(conn, serialized, limit=25)
                finally:
                    conn.close()

                for row in rows:
                    similarity = 1 - row["distance"] / 2
                    results.append({
                        "score": round(similarity * 100, 1),
                        "title": row["title"],
                        "company": row["company_slug"],
                        "location": row.get("location") or "",
                        "department": row.get("department") or row.get("team") or "",
                        "url": row.get("url") or "",
                    })
            except Exception as e:
                error = str(e)

        return render_template("search.html", query=query, results=results, error=error)

    return app
