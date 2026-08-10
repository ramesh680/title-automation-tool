"""
validator.py
------------
Workbook Sheet Validator for Title Data exports.

Upload an .xlsx/.csv, apply a rules-JSON, get back the same workbook with
failing cells highlighted plus a "Validation Summary" sheet -- mirroring the
media-tools-hub "Official Profile Finder / Data Ops Larger Project Validation"
tool, tailored to this app's 42-column schema.

The rules engine is data-driven: each rule is {sheet, column, check, ...params}.
Deterministic checks run fully offline. "lookup_*" checks that would need
external data (IMDb datasets, Metacritic, live Wikidata, YouTube API) currently
validate presence + format and are flagged as warnings; the real lookups plug
in later (they can reuse metadata_fetcher / the repo logic).
"""

import csv
import io
import re
from datetime import datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill, Font, Alignment

# ----- severities & highlight colours -------------------------------------
SEV_FAIL = "fail"     # hard failure -> red
SEV_WARN = "warn"     # needs review / pending lookup -> amber

FILL_FAIL = PatternFill("solid", fgColor="FFF4C7C3")   # soft red
FILL_WARN = PatternFill("solid", fgColor="FFFFE8A3")   # soft amber
FILL_HEAD = PatternFill("solid", fgColor="FF7C5CFF")   # brand violet

APPROVED_CATEGORIES = {"movies", "tv shows"}
# extend with the master Title Category list from the General ingest template
# (Health & Beauty, Beverages, Sports Franchise, Talent, Video Game, + 44 more)
try:
    from titleforge_ingest_ext import ALL_TITLE_CATEGORIES as _TFX_ALL
    APPROVED_CATEGORIES |= {c.lower() for c in _TFX_ALL}
except Exception:  # fail soft: keep the original two if the module is absent
    pass

# Default rules, tailored to THIS app's export schema (column names differ from
# the upstream tool: instagram_user / twitter_handle / tiktok_user / threads_page,
# metacritic, wikipedia_page). Same check semantics.
DEFAULT_RULES = {
    "rules": [
        {"sheet": "*", "column": "title", "check": "not_blank_and_not_placeholder",
         "tokens": ["#NA", "N/A"], "message": "Title cannot be blank, #NA, or N/A."},
        {"sheet": "*", "column": "title_category", "check": "approved_category",
         "message": "title_category must be present and one of the approved categories "
                    "(Movies, TV Shows, Talent, Video Game, Health & Beauty, Beverages, "
                    "Sports Franchise, or the General master list)."},
        {"sheet": "*", "column": "brand_set", "check": "brand_set_present_for_category",
         "message": "The brand set required for this title category (per the ingest "
                    "templates) is missing from brand_set."},
        {"sheet": "*", "column": "companies", "check": "dar_company_rule",
         "message": "DAR titles must have companies = 'Pristine Brand'."},
        {"sheet": "*", "column": "imdb_id", "alternate_column": "imdb_url",
         "check": "imdb_ttcode_format", "applies_to": ["Movies", "TV Shows"],
         "message": "IMDb value should be an IMDb title URL / ttNNNNNNN code."},
        {"sheet": "*", "column": "metacritic", "check": "metacritic_url_format",
         "message": "metacritic value should be a metacritic.com movie/tv URL."},
        {"sheet": "*", "column": "rottentomatoes", "check": "rottentomatoes_url_format",
         "applies_to": ["Movies", "TV Shows"],
         "message": "Rotten Tomatoes value should be a movie URL (contains /m/ or "
                    "/movie/). A /tv/ URL is not accepted as a valid Rotten "
                    "Tomatoes URL for Movies or TV Shows."},
        {"sheet": "*", "column": "wikipedia_page",
         "check": "english_wikipedia_url_matches_title", "accepted_host": "en.wikipedia.org",
         "message": "Wikipedia URLs must be en.wikipedia.org/wiki/... and match the title."},
        {"sheet": "*", "column": "facebook_page", "check": "facebook_page_hygiene",
         "message": "Facebook page must not be a /p/, /php/, /people/ or profile.php URL."},
        {"sheet": "*", "column": "released_on", "check": "release_date_valid",
         "applies_to": ["Movies", "TV Shows"],
         "message": "released_on must be a valid release date (YYYY-MM-DD) for Movies and "
                    "TV Shows."},
        {"sheet": "*", "column": "url_managers",
         "check": "contains_companies_and_platform_accounts",
         "company_column": "companies",
         "exclude_company_values": ["Unknown", "Pristine Brand"],
         "platform_columns": ["facebook_page", "youtube_channel_company",
                              "instagram_user", "twitter_handle", "tiktok_user", "threads_page"],
         "message": "url_managers must reference the row's social accounts (Unknown / Pristine Brand skipped)."},
        {"sheet": "*", "column": "network", "check": "network_boxofficemojo_checkpoint",
         "applies_to": ["Movies", "TV Shows"],
         "message": "network should be a specific Box Office Mojo distribution label "
                    "(blank is allowed when Box Office Mojo lists no distributor); "
                    "not a parent/umbrella studio."},
        {"sheet": "*", "column": "twitter_search_terms", "check": "twitter_search_terms_structure",
         "message": "twitter_search_terms lines must be '#hashtag or @handle|label|label' "
                    "(DAR rows use DAR|DAR; no duplicate terms; no runs of spaces in labels)."},
        {"sheet": "*", "column": "twitter_search_term_keywords",
         "check": "twitter_search_term_keywords_query",
         "message": "twitter_search_term_keywords must be boolean (\"title\") (...) queries; "
                    "a bare #hashtag/@handle belongs in twitter_search_terms, not here."},
    ]
}


# ----- helpers -------------------------------------------------------------
def _s(v):
    return "" if v is None else str(v).strip()


def _norm(v):
    return _s(v).lower()


def _is_dar(row):
    return " - dar" in _norm(row.get("title"))


def _row_get(row, col):
    """Case-insensitive row lookup."""
    if col in row:
        return row[col]
    low = {k.lower(): k for k in row}
    return row.get(low.get((col or "").lower(), ""), "")


# ----- individual checks ---------------------------------------------------
# each returns (severity_or_None, message)  -> None severity means "pass"
def _chk_not_blank_and_not_placeholder(val, row, rule):
    tokens = [t.upper() for t in rule.get("tokens", [])]
    if _s(val) == "" or _s(val).upper() in tokens:
        return SEV_FAIL, rule.get("message", "Value is blank or a placeholder.")
    return None, ""


def _chk_approved_category(val, row, rule):
    approved = set(a.lower() for a in rule.get("approved", APPROVED_CATEGORIES))
    if _norm(val) not in approved:
        return SEV_FAIL, rule.get("message", "Category not approved.")
    return None, ""


def _chk_dar_or_competitive_brand_set(val, row, rule):
    v = _norm(val)
    if _is_dar(row):
        if "pristine dar brands" not in v:
            return SEV_FAIL, rule.get("message")
    else:
        if "competitive view" not in v:
            return SEV_FAIL, rule.get("message")
    return None, ""


# --- brand_set required-value-by-category (Idea 4) -------------------------
# The ingest templates each carry a signature brand set. This maps a title
# category to the brand set token that MUST appear in the brand_set cell --
# split by whether the row is a DAR (pristine) row or a competitive/standard
# row. These signatures mirror what the tool's own row builders emit, so a
# workbook produced by the generator always passes.
_BRAND_SET_BY_KIND = {
    "movie":     {"dar": "Pristine DAR Brands",           "std": "Competitive View"},
    "tv":        {"dar": "LF // TV",                       "std": "Competitive View"},
    "talent":    {"dar": "LF // Talent",                   "std": "LF // Talent"},
    "publisher": {"dar": "LF // Publishing",               "std": "LF // Publishing"},
    "game":      {"dar": "LF // Video Games",              "std": "Competitive View"},
    "beauty":    {"dar": "LF // Beauty",                   "std": "Competitive View"},
    "beverages": {"dar": "LF // Beverages",                "std": "Competitive View"},
    "sports":    {"dar": "LF // Professional Sports Teams", "std": "Competitive View"},
    # "general" is intentionally absent: its brand set is carried through from
    # the source sheet, so there is no single required value to enforce.
}


def _kind_from_category(cat):
    """Map a free-text title_category to a brand-set kind key. Mirrors the
    generator's own _norm_kind so the two never disagree."""
    s = (cat or "").strip().lower()
    if "talent" in s:
        return "talent"
    if "publisher" in s:
        return "publisher"
    if "game" in s:
        return "game"
    if "beauty" in s:
        return "beauty"
    if "beverage" in s:
        return "beverages"
    if "sport" in s:
        return "sports"
    if s == "general":
        return "general"
    if "tv" in s:
        return "tv"
    if "movie" in s or "film" in s:
        return "movie"
    return ""


def _chk_brand_set_present_for_category(val, row, rule):
    """Flag ONLY when the brand set required for the row's title category is not
    present in brand_set. If it is present (or the category has no fixed
    requirement), the cell passes."""
    cat = _s(_row_get(row, "title_category"))
    spec = _BRAND_SET_BY_KIND.get(_kind_from_category(cat))
    if not spec:
        return None, ""  # unknown / General category -> nothing required
    required = spec["dar"] if _is_dar(row) else spec["std"]
    if required.lower() not in _norm(val):
        msg = rule.get("message", "Required brand set is missing.")
        return SEV_FAIL, f"{msg} Expected to contain '{required}' for category '{cat}'."
    return None, ""


def _chk_dar_company_rule(val, row, rule):
    if _is_dar(row) and _norm(val) != "pristine brand":
        return SEV_FAIL, rule.get("message", "DAR company must be 'Pristine Brand'.")
    return None, ""


_TT_RE = re.compile(r"tt\d{5,}", re.I)


def _chk_imdb_ttcode_format(val, row, rule):
    applies = [a.lower() for a in rule.get("applies_to", [])]
    if applies and _norm(_row_get(row, "title_category")) not in applies:
        return None, ""
    v = _s(val) or _s(_row_get(row, rule.get("alternate_column", "")))
    if v == "":
        return SEV_WARN, "IMDb id missing (lookup from title pending)."
    if not _TT_RE.search(v):
        return SEV_FAIL, rule.get("message", "IMDb value malformed.")
    return None, ""


# Metacritic: format only -- metacritic.com movie OR tv paths are both fine.
_MC_RE = re.compile(r"metacritic\.com/(movie|tv)/", re.I)


def _chk_metacritic_url_format(val, row, rule):
    v = _s(val)
    if v == "":
        return SEV_WARN, "Metacritic URL missing (lookup from title pending)."
    if not _MC_RE.search(v):
        return SEV_FAIL, rule.get("message", "Metacritic URL malformed.")
    return None, ""


# Business rule (Movies & TV Shows): a valid Rotten Tomatoes URL is a MOVIE URL
# -- it contains /m/ or /movie/. A /tv/ path is explicitly NOT accepted, even
# for TV-Show titles. Checked in that order so a /tv/ link always fails.
_RT_TV_RE = re.compile(r"/tv/", re.I)
_RT_VALID_RE = re.compile(r"/(?:m|movie)/", re.I)


def _chk_rottentomatoes_url_format(val, row, rule):
    # Only enforced for the categories in applies_to (Movies / TV Shows).
    applies = [a.lower() for a in rule.get("applies_to", [])]
    if applies and _norm(_row_get(row, "title_category")) not in applies:
        return None, ""
    v = _s(val)
    if v == "":
        return SEV_WARN, "Rotten Tomatoes URL missing (lookup from title pending)."
    if _RT_TV_RE.search(v):
        return SEV_FAIL, rule.get(
            "message",
            "A /tv/ URL is not accepted as a valid Rotten Tomatoes URL "
            "(use the /m/ or /movie/ URL).")
    if not _RT_VALID_RE.search(v):
        return SEV_FAIL, rule.get("message", "Rotten Tomatoes URL malformed.")
    return None, ""


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "", _norm(text))


def _chk_english_wikipedia_url_matches_title(val, row, rule):
    v = _s(val)
    host = rule.get("accepted_host", "en.wikipedia.org")
    if v == "":
        return SEV_WARN, "Wikipedia URL missing (lookup from title pending)."
    if f"{host}/wiki/" not in v.lower():
        return SEV_FAIL, rule.get("message", "Not an English Wikipedia article URL.")
    # soft title-match: compare article slug to title
    article = v.rsplit("/wiki/", 1)[-1].split("#")[0]
    art = _slug(article.replace("_", " ").split("(")[0])
    title = _slug(re.sub(r"\s*-\s*dar\s*$", "", _s(_row_get(row, "title")), flags=re.I))
    if art and title and art != title and title not in art and art not in title:
        return SEV_WARN, "Wikipedia article name does not obviously match the title."
    return None, ""


def _chk_contains_companies_and_platform_accounts(val, row, rule):
    companies = _s(_row_get(row, rule.get("company_column", "companies")))
    excluded = [e.lower() for e in rule.get("exclude_company_values", [])]
    if companies.lower() in excluded or companies == "":
        return None, ""  # skipped by rule
    present = []
    for col in rule.get("platform_columns", []):
        pv = _s(_row_get(row, col))
        if pv:
            # use the most identifying token (handle or last URL path segment)
            token = pv.split("|")[0].strip().rstrip("/").rsplit("/", 1)[-1]
            present.append((col, token))
    if not present:
        return None, ""  # nothing to reference
    um = _norm(val)
    if um == "":
        return SEV_FAIL, "url_managers is blank but social accounts exist for a real company."
    missing = [c for c, tok in present if tok and _norm(tok) not in um]
    if missing:
        return SEV_WARN, "url_managers does not reference: " + ", ".join(missing)
    return None, ""


# Facebook page hygiene (Rule 5, Aug 2026): a Facebook value that is a /p/,
# /php/ or /people/ path URL, or a profile.php URL, is not a usable page URL --
# such values must not be kept in the data, so the cell fails.
_FB_BAD_PATH_RE = re.compile(
    r"facebook\.com/(?:p|php|people)/|facebook\.com/profile\.php", re.I)


def _chk_facebook_page_valid(val, row, rule):
    v = _s(val)
    if v == "":
        return None, ""  # blank is a gap, not a bad value
    for line in v.splitlines():
        line = line.strip()
        if line and _FB_BAD_PATH_RE.search(line):
            return SEV_FAIL, rule.get(
                "message",
                "Facebook page must not be a /p/, /php/, /people/ or profile.php URL.")
    return None, ""


# Release date validation (Rule 6, Aug 2026): Movies and TV Shows must carry a
# valid release date in released_on. A missing date is a pending-lookup warning
# (consistent with the imdb/metacritic checks); a present-but-malformed or
# impossible date is a hard failure.
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%b %d, %Y",
                 "%B %d, %Y", "%d %b %Y", "%d %B %Y")


def _chk_release_date_valid(val, row, rule):
    applies = [a.lower() for a in rule.get("applies_to", [])]
    if applies and _norm(_row_get(row, "title_category")) not in applies:
        return None, ""
    v = _s(val)
    if v == "":
        return SEV_WARN, "Release date missing (lookup from title pending)."
    parsed = None
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(v, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        # accept a bare, plausible 4-digit year as a softer signal
        if re.fullmatch(r"\d{4}", v) and 1888 <= int(v) <= datetime.now().year + 10:
            return SEV_WARN, "Release date is a year only; a full YYYY-MM-DD date is preferred."
        return SEV_FAIL, rule.get("message", "released_on is not a valid date.")
    if not (1888 <= parsed.year <= datetime.now().year + 10):
        return SEV_FAIL, rule.get(
            "message", "released_on year is outside the plausible range.")
    return None, ""


# ----- Movies/Film column rules (Aug 2026) --------------------------------
# network parent-vs-child checkpoint: Box Office Mojo is the source of truth for
# a movie's distributor, but it often reports the studio UMBRELLA rather than the
# specific label. These checks are SOFT (warn) so a genuinely new label is never
# hard-blocked -- extend the lists via rule params ("parents", "approved") or here.
NETWORK_PARENTS = {
    # umbrella / holding distributor (lowercased) -> suggested child labels
    "walt disney studios motion pictures":
        ["Disney", "20th Century Studios", "Searchlight Pictures", "Pixar", "Marvel Studios", "Lucasfilm"],
    "the walt disney company":
        ["Disney", "20th Century Studios", "Searchlight Pictures", "Pixar", "Marvel Studios", "Lucasfilm"],
    "walt disney studios":
        ["Disney", "20th Century Studios", "Searchlight Pictures", "Pixar", "Marvel Studios", "Lucasfilm"],
    "walt disney pictures":
        ["Disney", "20th Century Studios", "Searchlight Pictures", "Pixar", "Marvel Studios", "Lucasfilm"],
    "sony pictures releasing":
        ["Sony / Columbia", "Columbia Pictures", "TriStar Pictures", "Screen Gems", "Sony Pictures Classics", "Affirm Films"],
    "sony pictures entertainment":
        ["Sony / Columbia", "Columbia Pictures", "TriStar Pictures", "Screen Gems", "Sony Pictures Classics", "Affirm Films"],
    "sony pictures":
        ["Sony / Columbia", "Columbia Pictures", "TriStar Pictures", "Screen Gems", "Sony Pictures Classics", "Affirm Films"],
    "sony group corporation":
        ["Sony / Columbia", "Columbia Pictures", "TriStar Pictures", "Screen Gems", "Sony Pictures Classics", "Affirm Films"],
    "nbcuniversal": ["Universal Pictures", "Focus Features"],
    "comcast": ["Universal Pictures", "Focus Features"],
    "nbcu enterprise": ["Universal Pictures", "Focus Features"],
    "warner bros. discovery": ["Warner Bros.", "New Line Cinema"],
    "warnermedia": ["Warner Bros.", "New Line Cinema"],
    "paramount global": ["Paramount Pictures"],
    "paramount skydance": ["Paramount Pictures"],
}
# Approved distribution labels (leaf/child). Seeded from the Box Office Mojo
# distributor list + the LF film feed. Off-list values only WARN.
APPROVED_NETWORKS = {n.lower() for n in [
    "Cineverse", "Warner Bros.", "New Line Cinema", "Disney", "20th Century Studios",
    "Searchlight Pictures", "Pixar", "Marvel Studios", "Lucasfilm", "Universal Pictures",
    "Focus Features", "Neon", "Sony / Columbia", "Columbia Pictures", "TriStar Pictures",
    "Screen Gems", "Sony Pictures Classics", "Affirm Films", "A24", "IFC Films",
    "IFC Midnight", "Angel Studios", "Vertical Entertainment", "Greenwich Entertainment",
    "Paramount Pictures", "Lionsgate", "Lionsgate / Summit", "Roadside Attractions",
    "GKIDS", "MUBI", "StudioCanal", "Kino Lorber", "Well Go USA Entertainment",
    "Janus Films", "Strand Releasing", "Oscilloscope", "Variance Films",
    "Ketchup Entertainment", "Brainstorm Media", "Indican Pictures", "Rialto Distribution",
    "Watermelon Pictures", "Black Bear", "Dark Sky Films", "AV Entertainment Company",
    "Trafalgar Releasing", "Fathom Events", "Iconic Events Releasing", "PBS network",
]}


def _chk_network_checkpoint(val, row, rule):
    """SOFT checkpoint for the movie `network` (distribution label).
    Blank is allowed (Box Office Mojo may list no distributor). A parent/umbrella
    distributor warns with the child labels to use; an off-list value warns."""
    applies = [a.lower() for a in rule.get("applies_to", [])]
    if applies and _norm(_row_get(row, "title_category")) not in applies:
        return None, ""
    v = _s(val)
    if v == "":
        return None, ""  # blank allowed: no distributor on Box Office Mojo
    key = v.lower()
    parents = dict(NETWORK_PARENTS)
    for k, kids in (rule.get("parents") or {}).items():
        parents[k.lower()] = kids
    if key in parents:
        kids = ", ".join(parents[key])
        return SEV_WARN, (rule.get("message", "network is a parent/umbrella distributor.")
                          + f" Use the specific label (e.g. {kids}).")
    approved = set(APPROVED_NETWORKS) | {a.lower() for a in rule.get("approved", [])}
    if approved and key not in approved:
        return SEV_WARN, (f"network '{v}' is not on the approved distributor-label list; "
                          "confirm it against Box Office Mojo (or add it to the list).")
    return None, ""


def _chk_twitter_search_terms(val, row, rule):
    """twitter_search_terms lines must be `#hashtag or @handle|label|label`.
    Blank is a gap (handled elsewhere), not a bad value. DAR rows expect DAR|DAR."""
    v = _s(val)
    if v == "":
        return None, ""
    dar = _is_dar(row)
    seen = set()
    for raw in v.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) != 3:
            return SEV_FAIL, (rule.get("message", "Lines must be term|label|label.")
                              + f" Offending line: {line}")
        term = parts[0].strip()
        if not (term.startswith("#") or term.startswith("@")):
            return SEV_FAIL, f"Term must start with # or @. Offending line: {line}"
        for lab in parts[1:]:
            if re.search(r"\s{2,}", lab):
                return SEV_FAIL, ("Label has runs of spaces (join groups with ' + '). "
                                  f"Offending line: {line}")
        if dar and (parts[1].strip() != "DAR" or parts[2].strip() != "DAR"):
            return SEV_WARN, f"DAR row labels should be 'DAR|DAR'. Offending line: {line}"
        if term.lower() in seen:
            return SEV_WARN, f"Duplicate term '{term}' (dedupe to avoid ingest conflicts)."
        seen.add(term.lower())
    return None, ""


def _chk_twitter_search_term_keywords(val, row, rule):
    """twitter_search_term_keywords lines must be boolean queries, never a bare
    #hashtag/@handle (those belong in twitter_search_terms)."""
    v = _s(val)
    if v == "":
        return None, ""
    for raw in v.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("@"):
            return SEV_FAIL, (rule.get("message", "A hashtag/handle is not a keyword.")
                              + f" Move it to twitter_search_terms. Offending line: {line}")
        if not line.startswith("("):
            return SEV_WARN, ('Line should be a (\"title\") (network-clause ...) query. '
                              f"Offending line: {line}")
    return None, ""


CHECKS = {
    "not_blank_and_not_placeholder": _chk_not_blank_and_not_placeholder,
    "approved_category": _chk_approved_category,
    "dar_or_competitive_brand_set": _chk_dar_or_competitive_brand_set,
    "brand_set_present_for_category": _chk_brand_set_present_for_category,
    "dar_company_rule": _chk_dar_company_rule,
    "imdb_ttcode_format": _chk_imdb_ttcode_format,
    "lookup_imdb_ttcode_from_title": _chk_imdb_ttcode_format,      # alias
    "metacritic_url_format": _chk_metacritic_url_format,
    "lookup_metacritic_url_from_title": _chk_metacritic_url_format,  # alias
    "rottentomatoes_url_format": _chk_rottentomatoes_url_format,
    "lookup_rottentomatoes_url_from_title": _chk_rottentomatoes_url_format,  # alias
    "english_wikipedia_url_matches_title": _chk_english_wikipedia_url_matches_title,
    "wikidata_english_wikipedia_url_matches_title": _chk_english_wikipedia_url_matches_title,
    "contains_companies_and_platform_accounts": _chk_contains_companies_and_platform_accounts,
    "facebook_page_hygiene": _chk_facebook_page_valid,
    "release_date_valid": _chk_release_date_valid,
    "lookup_release_date_from_title": _chk_release_date_valid,  # alias
    "network_boxofficemojo_checkpoint": _chk_network_checkpoint,
    "network_checkpoint": _chk_network_checkpoint,  # alias
    "twitter_search_terms_structure": _chk_twitter_search_terms,
    "twitter_search_term_keywords_query": _chk_twitter_search_term_keywords,
}


# ----- workbook loading ----------------------------------------------------
def _load_workbook(file_storage):
    """Return an openpyxl Workbook from an uploaded .xlsx or .csv."""
    name = (getattr(file_storage, "filename", "") or "").lower()
    data = file_storage.read()
    if name.endswith(".csv"):
        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        text = data.decode("utf-8-sig", errors="replace")
        for r in csv.reader(io.StringIO(text)):
            ws.append(r)
        return wb
    return load_workbook(io.BytesIO(data))


def _sheet_matches(rule_sheet, sheet_name):
    return rule_sheet in ("*", None) or rule_sheet == sheet_name


# ----- main entry ----------------------------------------------------------
def validate_workbook(file_storage, rules=None):
    """Validate a workbook. Returns (xlsx_bytes, summary_dict).

    summary_dict = {total_rows, checked_cells, fail, warn, failures:[...]}
    """
    rules = (rules or DEFAULT_RULES).get("rules", [])
    wb = _load_workbook(file_storage)

    failures = []
    total_rows = 0

    for ws in wb.worksheets:
        if ws.max_row < 2:
            continue
        headers = {}
        for c in range(1, ws.max_column + 1):
            h = _s(ws.cell(1, c).value)
            if h:
                headers[h.lower()] = c
        for r in range(2, ws.max_row + 1):
            total_rows += 1
            row = {}
            for hlow, c in headers.items():
                row[hlow] = ws.cell(r, c).value
            for rule in rules:
                if not _sheet_matches(rule.get("sheet"), ws.title):
                    continue
                col = (rule.get("column") or "").lower()
                fn = CHECKS.get(rule.get("check"))
                if not fn or col not in headers:
                    continue
                cell_val = ws.cell(r, headers[col]).value
                sev, msg = fn(cell_val, row, rule)
                if sev:
                    cell = ws.cell(r, headers[col])
                    cell.fill = FILL_FAIL if sev == SEV_FAIL else FILL_WARN
                    failures.append({
                        "sheet": ws.title, "row": r, "column": rule.get("column"),
                        "value": _s(cell_val), "severity": sev,
                        "rule": rule.get("check"), "message": msg,
                    })

    _append_summary(wb, failures, total_rows)

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    n_fail = sum(1 for f in failures if f["severity"] == SEV_FAIL)
    n_warn = sum(1 for f in failures if f["severity"] == SEV_WARN)
    summary = {
        "total_rows": total_rows,
        "issues": len(failures),
        "fail": n_fail,
        "warn": n_warn,
        "failures": failures[:500],
    }
    return out.getvalue(), summary


def _append_summary(wb, failures, total_rows):
    if "Validation Summary" in wb.sheetnames:
        del wb["Validation Summary"]
    ws = wb.create_sheet("Validation Summary", 0)
    n_fail = sum(1 for f in failures if f["severity"] == SEV_FAIL)
    n_warn = sum(1 for f in failures if f["severity"] == SEV_WARN)
    ws.append(["Validation Summary"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append(["Rows checked", total_rows])
    ws.append(["Failures (red)", n_fail])
    ws.append(["Warnings (amber)", n_warn])
    ws.append([])
    head = ["Sheet", "Row", "Column", "Value", "Severity", "Rule", "Message"]
    ws.append(head)
    hr = ws.max_row
    for c in range(1, len(head) + 1):
        cell = ws.cell(hr, c)
        cell.fill = FILL_HEAD
        cell.font = Font(bold=True, color="FFFFFFFF")
    for f in failures:
        ws.append([f["sheet"], f["row"], f["column"], f["value"][:200],
                   f["severity"], f["rule"], f["message"]])
        ws.cell(ws.max_row, 5).fill = FILL_FAIL if f["severity"] == SEV_FAIL else FILL_WARN
    widths = [16, 6, 22, 40, 10, 30, 50]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i)].width = w
    for c in range(1, len(head) + 1):
        ws.cell(hr, c).alignment = Alignment(vertical="center")
    return ws
