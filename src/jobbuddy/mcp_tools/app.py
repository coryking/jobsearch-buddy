"""The shared FastMCP server instance.

The `instructions` text below is injected into the calling LLM's context as
the routing hint for the whole server. Keep it intent-language, name specific
companies, and bias the model toward trying the tools first. Per-tool
docstrings live alongside their `@mcp.tool` definitions in the sibling
modules — this file owns only the server-wide framing.
"""

from fastmcp import FastMCP

mcp = FastMCP(
    name="job-search",  # This name should match the key in claude_desktop_config.json
    instructions=(
        "Fetch job postings live from company job boards (Greenhouse, Ashby, "
        "Lever, Workday, and a dozen more ATSes), normalized to clean JSON — "
        "plus log applications and track job-search activity. ALWAYS prefer "
        "these tools over web search or web fetch for a job posting: they hit "
        "the ATS's own API and return structured data (title, locations, "
        "salary, publish date, full JD) that scraped HTML can't match. "
        "Every response is fetched at call time — nothing is cached, so what "
        "you see is what the board says right now.\n\n"
        "## Picking the right tool\n\n"
        "1. User pastes a job URL or asks about one posting (\"what's this "
        "role?\", \"fetch this JD\", \"what about this one\") → `get_job(url=...)`. "
        "Works with LinkedIn deep-links and careers-page URLs; unknown "
        "companies register automatically.\n"
        "2. \"What's open at <company>?\" → `list_company_jobs` — one live "
        "call, compact rows, newest first.\n"
        "3. \"Anything new at my companies?\" — the user's watch list lives "
        "with YOU (their preferences/project context), not on this server. "
        "Fan out `list_company_jobs(company, published_since='1w')` per "
        "company in parallel and synthesize.\n"
        "4. Ranking and filtering the rows is YOUR job: read the listing "
        "rows in-context and apply the user's preferences (location, level, "
        "domain). Then `get_job` only the finalists the user wants detail on.\n"
        "5. Before the user clicks Apply → `get_application_form(url)` to "
        "warn about essay questions and reference demands.\n\n"
        "## Application tracking\n\n"
        "Record and review what the user has done — this half IS stateful "
        "and durable:\n"
        "- 'I applied', 'log this application' → log_job_application (URL or "
        "company+job_id; live-fetches the posting to snapshot it).\n"
        "- Recruiter call, interview, referral, freeform event → "
        "log_job_activity.\n"
        "- 'What have I applied to?', 'who have I talked to at X?', 'show my "
        "history' → review_activity_log (no args = all-company summary).\n\n"
        "## Companies\n\n"
        "The ats://companies resource lists every registered company (slug, "
        "ATS, short bio) — use it to resolve names for `list_company_jobs` "
        "and to answer 'tell me about X' triage questions. Registered "
        "companies number in the hundreds; a URL fetch through `get_job` "
        "auto-registers new ones. Only fall back to web search when a "
        "company isn't registered and the user has no URL."
    ),
)
