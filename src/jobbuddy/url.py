"""URL parser: job URL -> (ats_type, board_slug, job_id) or None."""

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# Each pattern returns (ats_type, board_slug, job_id)

_PATTERNS: list[tuple[str, re.Pattern, tuple[int, int, int] | None]] = []


def _add(ats: str, pattern: str, groups: tuple[int, int, int] | None = None):
    """Register a URL pattern. groups = (ats_type_group, board_group, job_id_group) or None for custom."""
    _PATTERNS.append((ats, re.compile(pattern, re.IGNORECASE), groups))


@dataclass
class ParsedURL:
    ats: str
    board: str
    job_id: str


# ---------------------------------------------------------------------------
# Greenhouse
# ---------------------------------------------------------------------------
# https://job-boards.greenhouse.io/<board>/jobs/<id>
# https://boards.greenhouse.io/embed/job_app?token=<id>  (board unknown, use "embed")
# https://<board>.greenhouse.io/jobs/<id>  (custom subdomain)
# https://boards-api.greenhouse.io/v1/boards/<board>/jobs/<id>  (API URL)

_GH_PATTERNS = [
    # job-boards.greenhouse.io/<board>/jobs/<id>
    r"(?:job-boards|boards)\.greenhouse\.io/([^/]+)/jobs/(\d+)",
    # boards.greenhouse.io/embed/job_app?token=<id>
    r"boards\.greenhouse\.io/embed/job_app\?.*?token=(\d+)",
    # <company>.greenhouse.io/jobs/<id>  (but not boards/job-boards subdomains)
    r"([a-z0-9-]+)\.greenhouse\.io/jobs/(\d+)",
    # Greenhouse via company career pages (e.g., stripe.com/jobs/listing/*/6567643)
    # These need the company to be in the registry already — handled by fallback
]

# Also handle Greenhouse URLs embedded in company sites:
# https://www.databricks.com/company/careers/product/sr-product-manager-...-8200284002
# These extract the trailing ID as a Greenhouse job ID (Databricks uses Greenhouse)


def _parse_greenhouse(url: str) -> ParsedURL | None:
    parsed = urlparse(url)
    host = parsed.hostname or ""

    # job-boards.greenhouse.io/<board>/jobs/<id> or boards.greenhouse.io/<board>/jobs/<id>
    m = re.search(r"(?:job-boards|boards)\.greenhouse\.io/([^/]+)/jobs/(\d+)", url)
    if m:
        return ParsedURL(ats="greenhouse", board=m.group(1), job_id=m.group(2))

    # boards.greenhouse.io/embed/job_app?token=<id>
    m = re.search(r"boards\.greenhouse\.io/embed/job_app\?.*?token=(\d+)", url)
    if m:
        return ParsedURL(ats="greenhouse", board="embed", job_id=m.group(1))

    # <board>.greenhouse.io/jobs/<id>  (custom subdomain, not boards/job-boards)
    if host.endswith(".greenhouse.io"):
        subdomain = host.rsplit(".greenhouse.io", 1)[0]
        if subdomain not in ("boards", "job-boards", "boards-api"):
            m = re.search(r"/jobs/(\d+)", parsed.path)
            if m:
                return ParsedURL(ats="greenhouse", board=subdomain, job_id=m.group(1))

    return None


# ---------------------------------------------------------------------------
# Ashby
# ---------------------------------------------------------------------------
# https://jobs.ashbyhq.com/<board>/<uuid>

def _parse_ashby(url: str) -> ParsedURL | None:
    m = re.search(
        r"jobs\.ashbyhq\.com/([^/]+)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        url,
        re.IGNORECASE,
    )
    if m:
        return ParsedURL(ats="ashby", board=m.group(1), job_id=m.group(2))
    return None


# ---------------------------------------------------------------------------
# Workday
# ---------------------------------------------------------------------------
# https://<company>.wd<N>.myworkdayjobs.com/<locale>/<board>/job/<path>/<id>
# https://<company>.wd<N>.myworkdayjobs.com/en-US/<board>/job/<slug>_<id>

def _parse_workday(url: str) -> ParsedURL | None:
    m = re.search(
        r"([a-z0-9-]+)\.wd(\d+)\.myworkdayjobs\.com",
        url,
        re.IGNORECASE,
    )
    if not m:
        return None

    wd_company = m.group(1)
    wd_instance = int(m.group(2))

    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]

    # Find the board slug — it's typically after en-US or similar locale
    # Pattern: /<locale>/<board>/job/... or /<board>/job/...
    board = None
    for i, part in enumerate(path_parts):
        if part == "job" and i > 0:
            board = path_parts[i - 1]
            break

    # If no /job/ in path, the board is likely the first non-locale segment
    if not board and path_parts:
        # Skip locale-like segments (en-US, etc.)
        for part in path_parts:
            if not re.match(r"^[a-z]{2}(-[A-Z]{2})?$", part):
                board = part
                break

    if not board:
        return None

    # Extract job ID: last path segment, or after underscore in slug
    # e.g., /job/Senior-Product-Manager_JR2001233
    job_id = None
    for part in reversed(path_parts):
        # Workday IDs often look like JR2001233 or R-99750-2 or 3154476
        id_match = re.search(r"[_-]([A-Z]*R?-?\d[\d-]+)$", part)
        if id_match:
            job_id = id_match.group(1)
            break

    if not job_id:
        # Fallback: last path segment
        job_id = path_parts[-1] if path_parts else None

    if not job_id:
        return None

    return ParsedURL(
        ats="workday",
        board=board,
        job_id=job_id,
    )


# ---------------------------------------------------------------------------
# Lever
# ---------------------------------------------------------------------------
# https://jobs.lever.co/<company>/<uuid>

def _parse_lever(url: str) -> ParsedURL | None:
    m = re.search(
        r"jobs\.lever\.co/([^/]+)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        url,
        re.IGNORECASE,
    )
    if m:
        return ParsedURL(ats="lever", board=m.group(1), job_id=m.group(2))
    return None


# ---------------------------------------------------------------------------
# Rippling
# ---------------------------------------------------------------------------
# https://ats.rippling.com/<company>/jobs/<uuid>

def _parse_rippling(url: str) -> ParsedURL | None:
    m = re.search(
        r"ats\.rippling\.com/([^/]+)/jobs/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        url,
        re.IGNORECASE,
    )
    if m:
        return ParsedURL(ats="rippling", board=m.group(1), job_id=m.group(2))
    return None


# ---------------------------------------------------------------------------
# Eightfold (Microsoft, Starbucks, etc.)
# ---------------------------------------------------------------------------
# https://apply.careers.microsoft.com/careers/job/<id>
# https://apply.starbucks.com/careers/job/<id>
# Pattern: any registered Eightfold base_url + /careers/job/<numeric_id>

def _parse_eightfold(url: str) -> ParsedURL | None:
    # Match /careers/job/<numeric_id> in path
    m = re.search(r"/careers/job/(\d+)", url)
    if not m:
        return None

    job_id = m.group(1)
    parsed = urlparse(url)
    host = parsed.hostname or ""

    # Reverse-lookup: find the eightfold company whose base_url matches this host
    from jobbuddy.registry import load_registry

    for slug, company in load_registry().items():
        if company.ats != "eightfold":
            continue
        extras = company.model_extra or {}
        base_url = extras.get("base_url", "")
        if not base_url:
            continue
        base_host = urlparse(base_url).hostname or ""
        if base_host == host:
            return ParsedURL(ats="eightfold", board=slug, job_id=job_id)

    return None


# ---------------------------------------------------------------------------
# Oracle HCM Cloud
# ---------------------------------------------------------------------------
# https://{tenant}.fa.{region}.oraclecloud.com/hcmUI/CandidateExperience/en/sites/{site}/job/{id}

def _parse_oracle_hcm(url: str) -> ParsedURL | None:
    m = re.search(
        r"([a-z0-9-]+)\.fa\.([a-z0-9]+)\.oraclecloud\.com/hcmUI/CandidateExperience/[^/]+/sites/([^/]+)/job/(\d+)",
        url,
        re.IGNORECASE,
    )
    if m:
        return ParsedURL(ats="oracle_hcm", board=m.group(1), job_id=m.group(4))
    return None


# ---------------------------------------------------------------------------
# Paylocity
# ---------------------------------------------------------------------------
# https://recruiting.paylocity.com/Recruiting/Jobs/All/{uuid}/{slug}  (listing)
# https://recruiting.paylocity.com/Recruiting/Jobs/Details/{id}        (detail)
# https://{N}recruiting.paylocity.com/...  (tenant prefix variant)

def _parse_paylocity(url: str) -> ParsedURL | None:
    parsed = urlparse(url)
    host = parsed.hostname or ""

    # Match recruiting.paylocity.com or {N}recruiting.paylocity.com
    if not re.search(r"(\d*)recruiting\.paylocity\.com$", host, re.IGNORECASE):
        return None

    path = parsed.path

    # Listing page: /Recruiting/Jobs/All/{uuid}/{slug}
    m = re.search(
        r"/Recruiting/Jobs/All/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        path,
        re.IGNORECASE,
    )
    if m:
        return ParsedURL(ats="paylocity", board=m.group(1), job_id="")

    # Detail page: /Recruiting/Jobs/Details/{numeric_id}
    m = re.search(r"/Recruiting/Jobs/Details/(\d+)", path, re.IGNORECASE)
    if m:
        return ParsedURL(ats="paylocity", board="", job_id=m.group(1))

    return None


# ---------------------------------------------------------------------------
# Workable
# ---------------------------------------------------------------------------
# https://apply.workable.com/<board>/#jobs  (board listing)
# https://apply.workable.com/<board>/j/<shortcode>/  (job detail)
# https://apply.workable.com/j/<shortcode>  (shortlink, board unknown)

def _parse_workable(url: str) -> ParsedURL | None:
    # Job detail: apply.workable.com/<board>/j/<shortcode>
    m = re.search(
        r"apply\.workable\.com/([^/]+)/j/([A-Z0-9]+)",
        url,
        re.IGNORECASE,
    )
    if m:
        board = m.group(1)
        if board.lower() != "j":  # avoid matching shortlinks as board
            return ParsedURL(ats="workable", board=board, job_id=m.group(2))

    # Shortlink: apply.workable.com/j/<shortcode>
    m = re.search(
        r"apply\.workable\.com/j/([A-Z0-9]+)",
        url,
        re.IGNORECASE,
    )
    if m:
        return ParsedURL(ats="workable", board="", job_id=m.group(1))

    # Board listing: apply.workable.com/<board>/ (no job ID)
    m = re.search(
        r"apply\.workable\.com/([^/]+)/?(?:#.*)?$",
        url,
        re.IGNORECASE,
    )
    if m and m.group(1).lower() not in ("j", "api"):
        return ParsedURL(ats="workable", board=m.group(1), job_id="")

    return None


# ---------------------------------------------------------------------------
# Avature
# ---------------------------------------------------------------------------
# https://{company}.avature.net[/{locale}]/{section}/JobDetail/{slug}/{id}

def _parse_avature(url: str) -> ParsedURL | None:
    m = re.search(
        r"([a-z0-9-]+)\.avature\.net(?:/[a-z]{2}_[A-Z]{2})?/([^/]+)/JobDetail/[^/]+/(\d+)",
        url,
        re.IGNORECASE,
    )
    if m:
        return ParsedURL(ats="avature", board=m.group(1), job_id=m.group(3))
    return None


# ---------------------------------------------------------------------------
# Phenom People (BAE Systems, RTX, GE Aerospace, etc.)
# ---------------------------------------------------------------------------
# https://{careers_domain}/{country}/{locale}/job/{jobId}
# https://{careers_domain}/{country}/{locale}/job/{jobId}/{slug}

def _parse_phenom(url: str) -> ParsedURL | None:
    parsed = urlparse(url)
    path = parsed.path
    host = parsed.hostname or ""

    # Match /{country_code}/{locale}/job/{jobId} in path
    m = re.search(r"/([a-z]{2,})/([a-z]{2})/job/([^/]+)", path, re.IGNORECASE)
    if not m:
        return None

    job_id = m.group(3)

    # Reverse-lookup: find the phenom company whose careers_url matches this host
    from jobbuddy.registry import load_registry

    for slug, company in load_registry().items():
        if company.ats != "phenom":
            continue
        extras = company.model_extra or {}
        careers_url = extras.get("careers_url", "")
        if not careers_url:
            continue
        careers_host = urlparse(careers_url).hostname or ""
        if careers_host == host:
            return ParsedURL(ats="phenom", board=slug, job_id=job_id)

    return None


# ---------------------------------------------------------------------------
# TalentBrew (Radancy)
# ---------------------------------------------------------------------------
# https://jobs.intuit.com/job/{location}/{title}/{tenant_id}/{job_id}

def _parse_talentbrew(url: str) -> ParsedURL | None:
    parsed = urlparse(url)
    path = parsed.path

    # Job detail: /job/{location}/{title}/{tenant_id}/{numeric_id}
    m = re.search(r"/job/[^/]+/[^/]+/(\d+)/(\d+)", path)
    if m:
        return ParsedURL(ats="talentbrew", board="", job_id=m.group(2))

    return None


# ---------------------------------------------------------------------------
# SuccessFactors (SAP)
# ---------------------------------------------------------------------------
# https://jobs.paccar.com/job/{slug}/{numericId}/
# https://careers.gulfstream.com/job/{slug}/{numericId}/

def _parse_successfactors(url: str) -> ParsedURL | None:
    parsed = urlparse(url)
    host = parsed.hostname or ""

    m = re.search(r"/job/[^/]+/(\d+)", parsed.path)
    if not m:
        return None

    job_id = m.group(1)

    from jobbuddy.registry import load_registry

    for slug, company in load_registry().items():
        if company.ats != "successfactors":
            continue
        extras = company.model_extra or {}
        careers_url = extras.get("careers_url", "")
        if not careers_url:
            continue
        careers_host = urlparse(careers_url).hostname or ""
        if careers_host == host:
            return ParsedURL(ats="successfactors", board=slug, job_id=job_id)

    return None


# ---------------------------------------------------------------------------
# Jibe (iCIMS Attract/CRM)
# ---------------------------------------------------------------------------
# https://careers.spiritaero.com/jobs/16625
# https://careers.spiritaero.com/jobs/16625?lang=en-us

def _parse_jibe(url: str) -> ParsedURL | None:
    parsed = urlparse(url)
    host = parsed.hostname or ""

    m = re.search(r"/jobs/(\d+)", parsed.path)
    if not m:
        return None

    job_id = m.group(1)

    from jobbuddy.registry import load_registry

    for slug, company in load_registry().items():
        if company.ats != "jibe":
            continue
        extras = company.model_extra or {}
        careers_url = extras.get("careers_url", "")
        if not careers_url:
            continue
        careers_host = urlparse(careers_url).hostname or ""
        if careers_host == host:
            return ParsedURL(ats="jibe", board=slug, job_id=job_id)

    return None


# ---------------------------------------------------------------------------
# JobSync (DirectEmployers/Solr)
# ---------------------------------------------------------------------------
# https://careers.alaskaair.com/jobs/passenger-service-agent/53BED5549DD84A0DACA2C0C1EB75E125/

def _parse_jobsync(url: str) -> ParsedURL | None:
    parsed = urlparse(url)
    host = parsed.hostname or ""

    # Match /jobs/{slug}/{32-char-hex-guid} with optional trailing slash
    m = re.search(r"/jobs/[^/]+/([A-Fa-f0-9]{32})/?$", parsed.path)
    if not m:
        return None

    guid = m.group(1).upper()

    from jobbuddy.registry import load_registry

    for slug, company in load_registry().items():
        if company.ats != "jobsync":
            continue
        extras = company.model_extra or {}
        origin_host = extras.get("origin_host", "")
        if origin_host == host:
            return ParsedURL(ats="jobsync", board=slug, job_id=guid)

    return None


# ---------------------------------------------------------------------------
# Apple
# ---------------------------------------------------------------------------
# https://jobs.apple.com/en-us/details/{positionId}/{slug}
# https://jobs.apple.com/{locale}/details/{positionId}/{slug}

def _parse_apple(url: str) -> ParsedURL | None:
    m = re.search(
        r"jobs\.apple\.com/[^/]+/details/([^/]+)",
        url,
        re.IGNORECASE,
    )
    if m:
        return ParsedURL(ats="apple", board="apple", job_id=m.group(1))
    return None


# ---------------------------------------------------------------------------
# Tesla
# ---------------------------------------------------------------------------
# https://www.tesla.com/careers/search/job/{slug}-{id}
# https://tesla.com/careers/search/job/{slug}-{id}


def _parse_tesla(url: str) -> ParsedURL | None:
    m = re.search(
        r"tesla\.com/careers/search/job/.*?(\d+)(?=[/?#]|$)",
        url,
        re.IGNORECASE,
    )
    if m:
        return ParsedURL(ats="tesla", board="tesla", job_id=m.group(1))
    return None


# ---------------------------------------------------------------------------
# SmartRecruiters
# ---------------------------------------------------------------------------
# https://jobs.smartrecruiters.com/{companyIdentifier}/{postingId}-{slug}

def _parse_smartrecruiters(url: str) -> ParsedURL | None:
    m = re.search(
        r"jobs\.smartrecruiters\.com/([^/]+)/(\d+)",
        url,
        re.IGNORECASE,
    )
    if m:
        return ParsedURL(ats="smartrecruiters", board=m.group(1), job_id=m.group(2))
    return None


# ---------------------------------------------------------------------------
# Amazon
# ---------------------------------------------------------------------------
# https://www.amazon.jobs/en/jobs/{icimsJobId}/{slug}
# https://amazon.jobs/en/jobs/{icimsJobId}/{slug}

def _parse_amazon(url: str) -> ParsedURL | None:
    m = re.search(
        r"amazon\.jobs/[^/]+/jobs/(\d+)",
        url,
        re.IGNORECASE,
    )
    if m:
        return ParsedURL(ats="amazon", board="amazon", job_id=m.group(1))
    return None


# ---------------------------------------------------------------------------
# Google Careers
# ---------------------------------------------------------------------------
# https://www.google.com/about/careers/applications/jobs/results/{job_id}

def _parse_google(url: str) -> ParsedURL | None:
    m = re.search(
        r"google\.com/about/careers/applications/jobs/results/(\d+)",
        url,
    )
    if m:
        return ParsedURL(ats="google", board="google", job_id=m.group(1))
    return None


# ---------------------------------------------------------------------------
# Taleo
# ---------------------------------------------------------------------------
# https://{tenant}.taleo.net/careersection/{section}/jobdetail.ftl?job={id}&lang=en

def _parse_taleo(url: str) -> ParsedURL | None:
    m = re.search(
        r"([a-z0-9-]+)\.taleo\.net/careersection/([^/]+)/jobdetail\.ftl",
        url,
        re.IGNORECASE,
    )
    if not m:
        return None
    parsed = urlparse(url)
    params = parsed.query
    job_m = re.search(r"(?:^|&)job=([^&]+)", params)
    if not job_m:
        return None
    return ParsedURL(ats="taleo", board=m.group(1), job_id=job_m.group(1))


# ---------------------------------------------------------------------------
# Uber
# ---------------------------------------------------------------------------
# https://www.uber.com/careers/apply/form/{id}

def _parse_uber(url: str) -> ParsedURL | None:
    m = re.search(
        r"uber\.com/careers/apply/form/(\d+)",
        url,
        re.IGNORECASE,
    )
    if m:
        return ParsedURL(ats="uber", board="uber", job_id=m.group(1))
    return None


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_PARSERS = [
    _parse_greenhouse,
    _parse_ashby,
    _parse_workable,
    _parse_workday,
    _parse_lever,
    _parse_rippling,
    _parse_eightfold,
    _parse_oracle_hcm,
    _parse_paylocity,
    _parse_avature,
    _parse_phenom,
    _parse_talentbrew,
    _parse_successfactors,
    _parse_jibe,
    _parse_jobsync,
    _parse_tesla,
    _parse_smartrecruiters,
    _parse_amazon,
    _parse_apple,
    _parse_google,
    _parse_uber,
    _parse_taleo,
]


def parse_url(url: str) -> ParsedURL | None:
    """Parse a job listing URL into (ats, board, job_id). Returns None if unrecognized."""
    for parser in _PARSERS:
        result = parser(url)
        if result:
            return result
    return None
