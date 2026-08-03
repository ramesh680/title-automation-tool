"""
metadata_fetcher.py (v3)
------------------------
Auto-discover title metadata by layering several sources.

RESOLUTION (finding the right title):
  * IMDb suggestion API (keyless) -> exact tt id; handles apostrophes, colons
    and UNRELEASED titles far better than TMDB text search
  * TMDB search / OMDb title search as fallbacks

ENRICHMENT priority (per field, first non-blank wins):
  network (US distributor):
      Box Office Mojo "Domestic Distributor"  (released titles)
    > Wikipedia infobox "Distributed by"      (works for upcoming titles)
    > Wikidata P750 distributor (US-qualified preferred)
    > IMDb page production company            (last resort only)
    > TMDB production company                 (last resort only)
  wikipedia_page / rottentomatoes / metacritic / socials / own YouTube:
      verified Wikipedia article (IMDb-id cross-checked) + its Wikidata item
  genre / released_on:
      TMDB US *theatrical* release date > OMDb > IMDb datePublished (often a
      festival date) > Wikidata

The film's OWN YouTube channel (if any) is returned as `youtube_own_channel`;
the app builds youtube_channel_username / youtube_channel_company from the
network's channel + the title (matching the manual Ops format).

API keys are read from ENVIRONMENT VARIABLES (never hard-coded):
  TMDB_API_KEY, OMDB_API_KEY, YOUTUBE_API_KEY, WIKIMEDIA_CONTACT,
  REQUEST_TIMEOUT_SECONDS
Every network call is defensive: on failure it returns what it has, never raises.
"""

import json
import logging
import os
import re
import threading
import time
import urllib.parse
from datetime import date, timedelta

try:
    import requests
    _SESSION = requests.Session()
    try:
        from requests.adapters import HTTPAdapter
        # parallel auto-discovery runs many lookups at once; grow the
        # connection pool so worker threads don't queue on free sockets
        _ADAPTER = HTTPAdapter(pool_connections=32, pool_maxsize=64,
                               max_retries=1)
        _SESSION.mount("https://", _ADAPTER)
        _SESSION.mount("http://", _ADAPTER)
    except Exception:
        pass
except Exception:
    requests = None
    _SESSION = None

log = logging.getLogger(__name__)

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
OMDB_API_KEY = os.getenv("OMDB_API_KEY", "")
YOUTUBE_API_KEY = (os.getenv("YOUTUBE_API_KEY", "") or "").strip('"')
WIKIMEDIA_CONTACT = os.getenv("WIKIMEDIA_CONTACT", "contact@listenfirstmedia.com")
try:
    TIMEOUT = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "10"))
except ValueError:
    TIMEOUT = 10

TMDB = "https://api.themoviedb.org/3"
OMDB = "https://www.omdbapi.com/"
YT_SEARCH = "https://www.googleapis.com/youtube/v3/search"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
ENTITYDATA = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
IMDB_TITLE = "https://www.imdb.com/title/{tt}/"
IMDB_SUGGEST = "https://v3.sg.media-imdb.com/suggestion/x/{q}.json"
BOM_TITLE = "https://www.boxofficemojo.com/title/{tt}/"
# Our own upcoming-release-movies service (BOM calendar data): authoritative
# for tt code, US distributor, genres, release date and Wide/Limited scale.
UPCOMING_API = os.getenv(
    "UPCOMING_MOVIES_API",
    "https://upcoming-release-movies.onrender.com/api/upcoming-release-movies")
try:
    UPCOMING_TIMEOUT = int(os.getenv("UPCOMING_TIMEOUT_SECONDS", "45"))
except ValueError:
    UPCOMING_TIMEOUT = 45

HEADERS = {"User-Agent": "ListenFirstTitleTool/1.0 (" + WIKIMEDIA_CONTACT + ")"}
HTML_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

FILM_TV_TYPES = {
    "Q11424", "Q202866", "Q24856", "Q93204", "Q506240", "Q5398426",
    "Q1259759", "Q15416", "Q21191270", "Q3464665", "Q580850", "Q1054574",
    "Q7725310", "Q1261214", "Q1366112",
}
PROPERTY_MAP = {
    "P345": "imdb", "P1258": "rottentomatoes", "P1712": "metacritic",
    "P2002": "twitter", "P2003": "instagram", "P2013": "facebook",
    "P2397": "youtube_id", "P11245": "youtube_handle", "P7085": "tiktok",
    "P4264": "linkedin_co", "P3836": "pinterest", "P11892": "threads",
}
# distributor first for films; original broadcaster first for TV
NETWORK_PROPS_MOVIE = ["P750", "P272"]
NETWORK_PROPS_TV = ["P449", "P750", "P272"]
US_QID = "Q30"
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
_MONTHS_FULL = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
                "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}


# ---------------- polite parallel fetching ----------------
# With parallel generation many lookups hit the same APIs at once; without
# throttling, Wikidata/Wikipedia answer 429 and discovery silently comes
# back empty (rows generate but socials are blank). Cap concurrent requests
# per host and retry rate-limited calls with backoff.
try:
    MAX_PER_HOST = max(1, int(os.getenv("MAX_CONCURRENT_PER_HOST", "4")))
except ValueError:
    MAX_PER_HOST = 4
_RETRY_STATUS = (429, 502, 503, 504)
_MAX_RETRIES = 4
_HOST_SEMS = {}
_HOST_SEMS_LOCK = threading.Lock()


def _host_sem(url):
    host = urllib.parse.urlsplit(url).netloc.lower()
    with _HOST_SEMS_LOCK:
        s = _HOST_SEMS.get(host)
        if s is None:
            s = _HOST_SEMS[host] = threading.BoundedSemaphore(MAX_PER_HOST)
        return s


def _polite_get(url, **kw):
    """Session.get with a per-host concurrency cap and retry/backoff on
    rate-limit / transient statuses (honours Retry-After). Raises like
    Session.get on final failure."""
    sem = _host_sem(url)
    delay = 0.5
    for attempt in range(_MAX_RETRIES + 1):
        with sem:
            try:
                r = _SESSION.get(url, **kw)
            except requests.exceptions.RequestException:
                if attempt >= _MAX_RETRIES:
                    raise
                time.sleep(delay)
                delay *= 2
                continue
        if r.status_code in _RETRY_STATUS and attempt < _MAX_RETRIES:
            ra = r.headers.get("Retry-After")
            try:
                wait = min(30.0, float(ra)) if ra else delay
            except ValueError:
                wait = delay
            r.close()
            log.info("HTTP %s from %s -- retrying in %.1fs",
                     r.status_code, url.split('?')[0], wait)
            time.sleep(wait)
            delay *= 2
            continue
        return r
    return r


# ---------------- low level ----------------
def _get_json(url, params=None, headers=None):
    if _SESSION is None:
        return None
    try:
        r = _polite_get(url, params=params, headers=headers or HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("json request failed (%s): %s", url, e)
        return None


def _get_html(url):
    if _SESSION is None:
        return None
    try:
        r = _polite_get(url, headers=HTML_HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception as e:  # noqa: BLE001
        log.warning("html request failed (%s): %s", url, e)
        return None


# Set VALIDATE_URLS=0 to skip the live URL/account checks (faster, less strict)
VALIDATE_URLS = os.getenv("VALIDATE_URLS", "1").strip().lower() not in ("0", "false", "no")
try:
    VALIDATE_TIMEOUT = int(os.getenv("VALIDATE_TIMEOUT_SECONDS", "6"))
except ValueError:
    VALIDATE_TIMEOUT = 6

_URL_STATUS_CACHE = {}


def _url_status(url):
    """Final HTTP status code for a URL (browser headers, redirects followed).
    None on network failure/timeout. Cached for the process lifetime."""
    if not url or _SESSION is None:
        return None
    if url in _URL_STATUS_CACHE:
        return _URL_STATUS_CACHE[url]
    status = None
    try:
        r = _polite_get(url, headers=HTML_HEADERS, timeout=VALIDATE_TIMEOUT,
                        allow_redirects=True, stream=True)
        status = r.status_code
        r.close()
    except Exception as e:  # noqa: BLE001
        log.warning("url status check failed (%s): %s", url, e)
    _URL_STATUS_CACHE[url] = status
    return status


def _tt(imdb_url_or_id):
    m = re.search(r"tt\d{5,}", str(imdb_url_or_id or ""))
    return m.group(0) if m else None


def _strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "")


def _norm(s):
    """lowercase, letters+digits only — for title comparisons."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _split_disambiguator(title):
    """Titles may carry a trailing '(...)' disambiguator that is NOT part of
    the real name — a year or a network/studio, e.g. 'Buddy (2026)' or
    'Steps (Netflix)'. Returns (lookup_title, hint): the bracket value is
    ignored for all lookups, but a 4-digit year is kept as a hint to pick
    the right same-named title."""
    m = re.search(r"\s*\(([^)]*)\)\s*$", title or "")
    if not m:
        return (title or "").strip(), ""
    return title[:m.start()].strip(), m.group(1).strip()


IMDB_YEAR_TOLERANCE = 1  # festival vs wide / regional gaps: allow +/-1 year


def _year_from(value):
    """First 4-digit release year in a date string ('2026-08-05'), a bare year
    ('2026'), or any text containing one. Returns int or None."""
    m = re.search(r"(?:19|20)\d{2}", str(value or ""))
    return int(m.group(0)) if m else None


# ---------------- Metacritic URL validation ----------------
def _mc_slug(title):
    """Slug the way Metacritic builds movie/tv paths (best-effort guess)."""
    import unicodedata
    s = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode("ascii")
    s = s.replace("&", " and ").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _mc_alive(url):
    """True (page exists), False (definitive 404/410) or None (can't tell:
    blocked, throttled, network error)."""
    status = _url_status(url.replace("http://", "https://"))
    if status is None:
        return None
    if status == 200:
        return True
    if status in (404, 410):
        return False
    return None  # 403/429/5xx: fail open, we can't tell


def resolve_metacritic(title, is_movie=True, candidate=None, curated=False, year=None):
    """Return a Metacritic URL that is known (or safely presumed) valid, or ''.

    Release-date first (same rule as IMDb): when a release year is known, the
    year-suffixed slug -- '.../movie/superman-2025/' -- is tried before the bare
    slug (which is usually the oldest same-named title), so a colliding title
    resolves to the right year's page.

    * curated candidate (Wikidata P1712): trusted -- dropped ONLY on a
      definitive 404/410.
    * guessed candidate (slugged title, e.g. from the BOM calendar service):
      kept ONLY when the page verifiably returns 200.
    * fallback: slug the title (year-suffixed first) and try /movie/ then /tv/
      per the title type; again only a verified 200 is accepted.
    """
    if not VALIDATE_URLS:
        return candidate or ""
    if candidate:
        alive = _mc_alive(candidate)
        if alive or (curated and alive is None):
            return candidate
    slug = _mc_slug(title)
    if not slug:
        return ""
    slugs = [f"{slug}-{year}", slug] if year else [slug]
    sections = ("movie", "tv") if is_movie else ("tv", "movie")
    for sec in sections:
        for sl in slugs:
            url = "https://www.metacritic.com/%s/%s/" % (sec, sl)
            if candidate and url.rstrip("/") == str(candidate).replace(
                    "http://", "https://").rstrip("/"):
                continue  # already tried above
            if _mc_alive(url):
                return "http://www.metacritic.com/%s/%s/" % (sec, sl)
    return ""


def is_tv_rottentomatoes(url):
    """Business rule (Movies & TV Shows): only a MOVIE Rotten Tomatoes URL --
    one containing /m/ -- is accepted. A /tv/ path is never valid, so such a
    value is dropped rather than shipped to Ops."""
    return "/tv/" in str(url or "").lower()


def clean_rottentomatoes(url):
    """Return the RT URL if it satisfies the movie-only (/m/) rule, else ''."""
    u = str(url or "").strip()
    if not u or is_tv_rottentomatoes(u):
        return ""
    return u


def _rt_slug(title):
    """Slug the way Rotten Tomatoes builds /m/ movie paths (underscores)."""
    import unicodedata
    s = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode("ascii")
    s = s.replace("&", " and ").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _rt_alive(url):
    """True (200), False (definitive 404/410) or None (blocked/throttled)."""
    status = _url_status(url.replace("http://", "https://"))
    if status is None:
        return None
    if status == 200:
        return True
    if status in (404, 410):
        return False
    return None  # 403/429/5xx: fail open, we can't tell


def resolve_rottentomatoes(title, is_movie=True, candidate=None, curated=False, year=None):
    """Return a movie-only (/m/) Rotten Tomatoes URL, or ''.

    Same rule as IMDb/Metacritic: with a known release year the year-suffixed
    slug -- '/m/superman_2025' -- is tried before the bare slug, so a colliding
    title resolves to the right year's page.

    * curated candidate (Wikidata P1258): trusted -- dropped ONLY on a
      definitive 404/410 (and only if it satisfies the /m/ movie-only rule).
    * generated slug: kept ONLY when the page verifiably returns 200.
    Business rule: a /tv/ path is never valid, so TV Shows yield ''."""
    cand = clean_rottentomatoes(candidate)
    if not VALIDATE_URLS:
        return cand
    if cand:
        alive = _rt_alive(cand)
        if alive or (curated and alive is None):
            return cand
    if not is_movie:
        return ""  # only movie /m/ RT URLs are shipped
    slug = _rt_slug(title)
    if not slug:
        return ""
    slugs = [f"{slug}_{year}", slug] if year else [slug]
    for sl in slugs:
        url = "https://www.rottentomatoes.com/m/%s" % sl
        if cand and url.rstrip("/") == str(cand).replace(
                "http://", "https://").rstrip("/"):
            continue
        if _rt_alive(url):
            return "http://www.rottentomatoes.com/m/%s" % sl
    return ""


# ---------------- social account liveness ----------------
def _twitter_alive(handle):
    """Keyless check via Twitter's oEmbed endpoint: a 404 means the account
    does not exist or is suspended. Anything ambiguous fails open (True)."""
    h = str(handle or "").strip().lstrip("@")
    if not h:
        return False
    status = _url_status("https://publish.twitter.com/oembed?url="
                         + urllib.parse.quote("https://twitter.com/" + h, safe=""))
    return status != 404


def _instagram_alive(user):
    """404 on instagram.com/<user>/ means the account is gone. Login walls /
    throttling (200/302/429...) fail open (True)."""
    u = str(user or "").strip().lstrip("@")
    if not u:
        return False
    return _url_status("https://www.instagram.com/" + u + "/") not in (404, 410)


def _facebook_alive(page):
    """`page` is a facebook.com URL or a bare page name. 404/410 means the
    page is gone; login redirects fail open (True)."""
    p = str(page or "").strip()
    if not p:
        return False
    if "facebook.com" not in p:
        p = "https://www.facebook.com/" + p.lstrip("/")
    return _url_status(p.replace("http://", "https://")) not in (404, 410)


def verify_socials(meta, title=None, reject_foreign=False):
    """Drop social handles that are wrong or dead.

    With reject_foreign set (movies/TV), a handle whose slug signals a different
    owner -- '...band', 'VEVO', 'Topic' -- is dropped first, so a same-named
    band's account never rides along on a movie/show (the 'Crawlers' case).
    Then the live check runs; it fails OPEN, removing a handle only on a
    definitive 404 -- bot walls and rate limits never strip a valid account."""
    if not meta:
        return meta
    if reject_foreign and title:
        for k in ("twitter_handle", "instagram_user", "facebook_page"):
            v = meta.get(k)
            if v and _handle_foreign_to_title(v, title):
                log.info("dropping %s %r - looks like a band/artist, not %r", k, v, title)
                meta.pop(k, None)
    if not VALIDATE_URLS:
        return meta
    if meta.get("twitter_handle") and not _twitter_alive(meta["twitter_handle"]):
        log.info("dropping dead twitter handle %r", meta["twitter_handle"])
        meta.pop("twitter_handle")
    if meta.get("instagram_user") and not _instagram_alive(meta["instagram_user"]):
        log.info("dropping dead instagram user %r", meta["instagram_user"])
        meta.pop("instagram_user")
    if meta.get("facebook_page") and not _facebook_alive(meta["facebook_page"]):
        log.info("dropping dead facebook page %r", meta["facebook_page"])
        meta.pop("facebook_page")
    return meta


# ---------------- upcoming-release-movies service ----------------
_UPCOMING = {"fetched": 0.0, "by_title": {}, "by_tt": {}}
_UPCOMING_TTL = 6 * 3600  # refresh the calendar index every 6h


def _upcoming_index():
    """Cached index of the upcoming-release-movies service, keyed by
    normalized title and by tt code. The service runs on a free Render
    instance that sleeps when idle, so a failed fetch is retried once after
    a short pause (waking service). Fails soft (empty index) so the
    pipeline keeps working via the other sources."""
    now = time.time()
    if _UPCOMING["by_title"] and now - _UPCOMING["fetched"] < _UPCOMING_TTL:
        return _UPCOMING
    if _SESSION is None:
        return _UPCOMING
    start = (date.today() - timedelta(days=180)).isoformat()
    end = (date.today() + timedelta(days=730)).isoformat()
    movies = []
    for attempt in (1, 2):
        try:
            r = _SESSION.get(UPCOMING_API,
                             params={"start_date": start, "end_date": end},
                             headers=HEADERS, timeout=UPCOMING_TIMEOUT)
            r.raise_for_status()
            movies = (r.json() or {}).get("movies") or []
            break
        except Exception as e:  # noqa: BLE001
            log.warning("upcoming-release-movies fetch failed (try %d): %s", attempt, e)
            if attempt == 1:
                time.sleep(10)  # give the free instance time to wake
    if not movies:
        return _UPCOMING
    by_title, by_tt = {}, {}
    for m in movies:
        k = _norm(m.get("title"))
        tt = m.get("tt_code")
        # prefer the entry that actually has a distributor if duplicated
        if k and (k not in by_title or
                  (by_title[k].get("distributor_network") or "-") == "-"):
            by_title[k] = m
        if tt and (tt not in by_tt or
                   (by_tt[tt].get("distributor_network") or "-") == "-"):
            by_tt[tt] = m
    if by_title:
        _UPCOMING.update(fetched=now, by_title=by_title, by_tt=by_tt)
    return _UPCOMING


def warm_upcoming():
    """Fire-and-forget warm-up of the calendar index (called when the tool's
    page loads, so the sleeping service is awake before Generate is hit)."""
    import threading
    threading.Thread(target=_upcoming_index, daemon=True).start()


def _upcoming_meta(m):
    """Map an upcoming-release-movies record to our columns."""
    if not m:
        return {}
    meta = {}
    dist = str(m.get("distributor_network") or "").strip()
    if dist and dist != "-":
        meta["network"] = dist
    if m.get("release_date"):
        meta["released_on"] = str(m["release_date"])[:10]
    gs = m.get("genres") or [g.strip() for g in str(m.get("genre") or "").split(",") if g.strip()]
    if gs:
        meta["genre"] = "\n".join(gs)
        meta["primary_genre"] = gs[0]
    if str(m.get("release_scale") or "").strip().title() in ("Wide", "Limited"):
        meta["release_scale"] = str(m["release_scale"]).strip().title()
    if m.get("tt_code"):
        meta["imdb_id"] = "http://www.imdb.com/title/" + str(m["tt_code"])
    if m.get("metacritic_url"):
        # the calendar service GUESSES this URL by slugging the title --
        # hold it as a candidate; _enrich_by_tt only keeps it if it verifies
        meta["_metacritic_guess"] = str(m["metacritic_url"]).replace("https://", "http://")
    return meta


# ---------------- IMDb suggestion API (keyless, reliable) ----------------
# IMDb item type -> LF Program Type (for the TV BrandIngest schema)
_IMDB_QID_PROGRAM_TYPE = {
    "tvseries": "Series", "tvminiseries": "Mini-Series",
    "tvmovie": "TV Movie", "tvspecial": "Special", "tvshort": "Special",
}


def imdb_suggest_item(title, is_movie=True, year_hint="", require_year=False):
    """Best-matching item from IMDb's suggestion API (keyless), or None.
    Handles apostrophes/colons and titles that have no release yet.

    Prefers an exact title match of the right type. When a release year is
    known (year_hint), a candidate whose year matches it (within
    IMDB_YEAR_TOLERANCE) is preferred over a merely more-recent one -- this is
    how a same-named title from another year is avoided.

    With require_year=True the year is authoritative: if the best title match
    has a year that disagrees with year_hint by more than the tolerance, None is
    returned rather than attaching the wrong title. A candidate with no year (an
    upcoming title) is never rejected on year."""
    q = urllib.parse.quote(title.strip().lower())
    data = _get_json(IMDB_SUGGEST.format(q=q), headers=HTML_HEADERS)
    items = [it for it in (data or {}).get("d", [])
             if str(it.get("id", "")).startswith("tt")]
    if not items:
        return None
    tl = _norm(title)
    want_tv = not is_movie
    want_year = _year_from(year_hint)

    def type_ok(it):
        qid = str(it.get("qid") or "").lower()
        if not qid:
            return True
        is_tv_item = qid.startswith("tv") and qid != "tvmovie"
        return is_tv_item == want_tv

    def year_rank(it):
        y = it.get("y")
        if not (want_year and y):
            return 0
        diff = abs(int(y) - want_year)
        return 2 if diff == 0 else (1 if diff <= IMDB_YEAR_TOLERANCE else -1)

    def score(it):
        # correct media type outranks year: a movie lookup must never return a
        # TV series of the same name, even one from the wanted year
        return (1 if _norm(it.get("l")) == tl else 0,
                1 if type_ok(it) else 0,
                year_rank(it),
                it.get("y") or 0)

    best = max(items, key=score)
    # only trust it when the title actually matches
    if _norm(best.get("l")) != tl:
        return None
    # date is authoritative: reject a same-named title from the wrong year
    if require_year and want_year and best.get("y") \
            and abs(int(best["y"]) - want_year) > IMDB_YEAR_TOLERANCE:
        log.info("imdb_suggest_item: %r best %s is year %s, want ~%s - rejected",
                 title, best.get("id"), best.get("y"), want_year)
        return None
    return best


def imdb_suggest(title, is_movie=True):
    """Resolve just the IMDb tt id via the suggestion API."""
    item = imdb_suggest_item(title, is_movie)
    return item["id"] if item else None


# ---------------- Wikipedia (keyless) ----------------
def _page_qid(page_title):
    d = _get_json(WIKIPEDIA_API, {"action": "query", "prop": "pageprops",
                                  "ppprop": "wikibase_item", "redirects": 1,
                                  "titles": page_title, "format": "json"})
    pages = ((d or {}).get("query", {}) or {}).get("pages", {})
    for p in pages.values():
        qid = (p.get("pageprops") or {}).get("wikibase_item")
        if qid:
            return qid
    return None


def wiki_lookup(title, is_movie=True, tt=None):
    """Find the enwiki article for this exact film/show.
    Returns (url, page_title, wikidata_qid) or (None, None, None).
    A candidate must match the title (ignoring a trailing '(film)'/'(TV...)')
    and, when we know the IMDb id, its Wikidata P345 must agree."""
    kind = "film" if is_movie else "TV series"
    hits = []
    for q in (f'{title} {kind}', title):
        data = _get_json(WIKIPEDIA_API, {"action": "query", "list": "search",
                                         "srsearch": q, "srlimit": 6, "format": "json"})
        for h in ((data or {}).get("query", {}) or {}).get("search", []):
            if h["title"] not in hits:
                hits.append(h["title"])
    tl = _norm(title)
    kind_paren = re.compile(
        r"\([^)]*\b(film|movie|miniseries|mini-series|tv series|television series|"
        r"tv film|television film|series)\)\s*$", re.IGNORECASE)
    for pt in hits:
        base = _norm(re.sub(r"\s*\([^)]*\)\s*$", "", pt))
        if base != tl:
            continue
        qid = _page_qid(pt)
        ent = _entity(qid) if qid else None
        if ent is not None:
            imdbs = _claim_values(ent.get("claims", {}) or {}, "P345")
            if tt and imdbs and tt not in imdbs:
                continue  # same name, different film
            # the article must be the film/TV work, not a same-named band/person
            if not (entity_is_wanted_work(ent, is_movie, tt) or kind_paren.search(pt)):
                log.info("wiki_lookup: skipped %r (%s) - not a %s", pt, qid, kind)
                continue
        url = "http://en.wikipedia.org/wiki/" + pt.replace(" ", "_")
        return url, pt, qid
    return None, None, None


def wiki_infobox_network(page_title, is_movie=True):
    """US distributor (films) / network (TV) from the article's infobox.
    Works for upcoming titles, where BOM has no page yet."""
    d = _get_json(WIKIPEDIA_API, {"action": "parse", "page": page_title,
                                  "prop": "wikitext", "section": "0",
                                  "redirects": 1, "format": "json"})
    txt = (((d or {}).get("parse") or {}).get("wikitext") or {}).get("*", "")
    if not txt:
        return None
    keys = ("distributor", "distributors") if is_movie else ("network", "channel", "distributor")
    for k in keys:
        m = re.search(r"\|\s*" + k + r"\s*=((?:[^\n]|\n(?!\s*[|}]))*)", txt, re.I)
        if not m:
            continue
        seg = m.group(1)
        links = re.findall(r"\[\[(?:[^\]|]*\|)?([^\]|]+)\]\]", seg)  # display text
        if links:
            return links[0].strip()
        plain = _strip_tags(re.sub(r"\{\{[^{}]*\}\}", " ", seg)).strip()
        plain = plain.replace("*", " ").strip()
        first = next((ln.strip() for ln in plain.splitlines() if ln.strip()), "")
        if first:
            return first
    return None


# ---------------- IMDb scrape ----------------
def imdb_scrape(tt):
    """genre, primary_genre, datePublished (often a festival date -> lowest
    priority for released_on), production company as LAST-RESORT network."""
    if not tt:
        return {}
    html = _get_html(IMDB_TITLE.format(tt=tt))
    if not html:
        return {}
    meta = {}
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    if m:
        try:
            j = json.loads(m.group(1))
            g = j.get("genre")
            if isinstance(g, str):
                g = [g]
            if g:
                meta["genre"] = "\n".join(g)
                meta["primary_genre"] = g[0]
            if j.get("datePublished"):
                meta["released_on"] = j["datePublished"]
        except Exception:  # noqa: BLE001
            pass
    block = re.search(r'data-testid="title-details-companies"(.*?)</ul>', html, re.S)
    if block:
        names = re.findall(r'href="/company/[^"]*"[^>]*>([^<]+)</a>', block.group(1))
        if names:
            meta["production_company"] = names[0].strip()
    return meta


# ---------------- Box Office Mojo scrape ----------------
def _bom_value(html, label):
    m = re.search(re.escape(label) + r"</span>\s*<span[^>]*>(.*?)</span>", html, re.S)
    if not m:
        return None
    return _strip_tags(m.group(1)).strip()


def bom_scrape(tt):
    """US Domestic Distributor (network) + domestic opening box office."""
    if not tt:
        return {}
    html = _get_html(BOM_TITLE.format(tt=tt))
    if not html:
        return {}
    meta = {}
    dist = _bom_value(html, "Domestic Distributor")
    if dist:
        dist = re.sub(r"See full company information.*$", "", dist).strip()
        if dist:
            meta["network"] = dist
    opening = _bom_value(html, "Domestic Opening")
    if opening:
        opening = opening.split("(")[0].strip()
        if opening.startswith("$"):
            meta["domestic_opening_weekend_box_office"] = opening
    return meta


# ---------------- TMDB ----------------
def _tmdb_pick(results, title, want_year=None):
    if not results:
        return None
    tl = title.strip().lower()

    def year(r):
        d = str(r.get("release_date") or r.get("first_air_date") or "")
        return int(d[:4]) if d[:4].isdigit() else 0

    exact = [r for r in results if str(r.get("title") or r.get("name") or "").strip().lower() == tl]
    pool = exact or results
    if want_year:
        dated = [r for r in pool if year(r) and abs(year(r) - want_year) <= IMDB_YEAR_TOLERANCE]
        if dated:  # closest year to the target wins
            return min(dated, key=lambda r: abs(year(r) - want_year))
    return max(pool, key=year)


def _us_theatrical_date(details):
    """US theatrical (type 3) > limited (2) > premiere (1) release date."""
    results = ((details.get("release_dates") or {}).get("results")) or []
    us = next((r for r in results if r.get("iso_3166_1") == "US"), None)
    if not us:
        return None
    dates = us.get("release_dates") or []
    for wanted in (3, 2, 4, 6, 1):
        for d in dates:
            if d.get("type") == wanted and d.get("release_date"):
                return str(d["release_date"])[:10]
    return None


def _tmdb_details_meta(details, kind):
    meta = {}
    genres = [g.get("name") for g in details.get("genres", []) if g.get("name")]
    if genres:
        meta["genre"] = "\n".join(genres)
        meta["primary_genre"] = genres[0]
    if kind == "movie":
        us = _us_theatrical_date(details)
        if us:
            meta["released_on_us"] = us
    rel = details.get("release_date") or details.get("first_air_date")
    if rel:
        meta["released_on"] = rel
    if details.get("original_language"):
        meta["original_language"] = details["original_language"]
    if kind == "tv":
        nets = [n.get("name") for n in details.get("networks", []) if n.get("name")]
        if nets:
            meta["network"] = nets[0]
        # TMDB show type -> LF Program Type (fallback when IMDb didn't say)
        ttype = str(details.get("type") or "").strip().lower()
        if ttype == "miniseries":
            meta["program_type"] = "Mini-Series"
        elif ttype in ("scripted", "reality", "documentary", "talk show", "news", "soap"):
            meta["program_type"] = "Series"
    pcs = [c.get("name") for c in details.get("production_companies", []) if c.get("name")]
    if pcs:
        meta["production_company"] = pcs[0]  # NOT the network; last resort only
    ext = details.get("external_ids", {}) or {}
    if ext.get("imdb_id"):
        meta["imdb_id"] = "http://www.imdb.com/title/" + ext["imdb_id"]
    if ext.get("facebook_id"):
        meta["facebook_page"] = "http://www.facebook.com/" + ext["facebook_id"]
    if ext.get("instagram_id"):
        meta["instagram_user"] = ext["instagram_id"]
    if ext.get("twitter_id"):
        meta["twitter_handle"] = ext["twitter_id"]
    return meta, ext.get("wikidata_id")


_TMDB_APPEND = "external_ids,release_dates"


def tmdb_lookup(title, is_movie, want_year=None):
    if not TMDB_API_KEY:
        return {}, None
    kind = "movie" if is_movie else "tv"
    params = {"api_key": TMDB_API_KEY, "query": title}
    if want_year:
        params["primary_release_year" if is_movie else "first_air_date_year"] = want_year
    search = _get_json(TMDB + "/search/" + kind, params)
    if not search:
        return {}, None
    hit = _tmdb_pick(search.get("results", []), title, want_year)
    if not hit:
        return {}, None
    details = _get_json(TMDB + "/" + kind + "/" + str(hit["id"]),
                        {"api_key": TMDB_API_KEY, "append_to_response": _TMDB_APPEND})
    if not details:
        return {}, None
    return _tmdb_details_meta(details, kind)


def tmdb_find_by_imdb(tt):
    if not TMDB_API_KEY or not tt:
        return {}, None
    data = _get_json(TMDB + "/find/" + tt, {"api_key": TMDB_API_KEY, "external_source": "imdb_id"})
    if not data:
        return {}, None
    for key, kind in (("movie_results", "movie"), ("tv_results", "tv")):
        res = data.get(key) or []
        if res:
            details = _get_json(TMDB + "/" + kind + "/" + str(res[0]["id"]),
                                {"api_key": TMDB_API_KEY, "append_to_response": _TMDB_APPEND})
            if details:
                return _tmdb_details_meta(details, kind)
    return {}, None


# ---------------- OMDb ----------------
def _omdb_date(s):
    m = re.match(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", s or "")
    if not m:
        return None
    d, mon, y = m.groups()
    mon = mon[:3].title()
    return "%s-%02d-%02d" % (y, _MONTHS[mon], int(d)) if mon in _MONTHS else None


def _omdb_meta(data):
    if not data or data.get("Response") == "False":
        return {}
    meta = {}
    if data.get("imdbID"):
        meta["imdb_id"] = "http://www.imdb.com/title/" + data["imdbID"]
    if data.get("Genre") and data["Genre"] != "N/A":
        gs = [g.strip() for g in data["Genre"].split(",") if g.strip()]
        if gs:
            meta["genre"] = "\n".join(gs)
            meta["primary_genre"] = gs[0]
    d = _omdb_date(data.get("Released"))
    if d:
        meta["released_on"] = d
    return meta


def omdb_by_id(tt):
    if not OMDB_API_KEY or not tt:
        return {}
    return _omdb_meta(_get_json(OMDB, {"i": tt, "apikey": OMDB_API_KEY}))


def omdb_lookup(title, want_year=None):
    if not OMDB_API_KEY:
        return {}
    params = {"t": title, "apikey": OMDB_API_KEY}
    if want_year:
        params["y"] = want_year
    return _omdb_meta(_get_json(OMDB, params))


# ---------------- Wikidata ----------------
def _search_candidates(title, limit=6):
    data = _get_json(WIKIDATA_API, {"action": "wbsearchentities", "search": title,
                                    "language": "en", "format": "json", "type": "item", "limit": limit})
    return [c["id"] for c in data.get("search", [])] if data else []


def _entity(qid):
    data = _get_json(ENTITYDATA.format(qid=qid))
    return data.get("entities", {}).get(qid) if data else None


def _labels(qids):
    qids = [q for q in qids if q]
    if not qids:
        return {}
    data = _get_json(WIKIDATA_API, {"action": "wbgetentities", "ids": "|".join(qids[:50]),
                                    "props": "labels", "languages": "en", "format": "json"})
    out = {}
    for qid, ent in (data.get("entities", {}) if data else {}).items():
        lbl = ent.get("labels", {}).get("en", {}).get("value")
        if lbl:
            out[qid] = lbl
    return out


# ---- reverse lookup: social handle -> Wikidata entity ---------------------
# Handle-first Generator mode. Given ONE account we find its Wikidata item,
# so the full profile (real name + every other social as proper URLs +
# Wikipedia + IMDb) can be pulled with the SAME extraction the name-based
# fetchers use. Keyless: CirrusSearch 'haswbstatement' matches an exact
# property value.
_HANDLE_PROPS = {
    "instagram": ["P2003"],
    "twitter":   ["P2002"],
    "facebook":  ["P2013"],
    "youtube":   ["P11245", "P2397"],   # @handle, then channel id
    "tiktok":    ["P7085"],
    "tumblr":    ["P3943"],
    "linkedin":  ["P4264", "P6634"],    # company id, then personal profile id
}


def _bare_handle(platform, handle):
    """Reduce a URL / @name to the raw value Wikidata stores for the property."""
    s = str(handle or "").strip()
    if not s:
        return ""
    s = re.sub(r"^https?://", "", s, flags=re.I)
    s = re.sub(r"^www\.", "", s, flags=re.I)
    for dom in ("instagram.com/", "twitter.com/", "x.com/", "facebook.com/",
                "youtube.com/", "youtu.be/", "tiktok.com/", "tumblr.com/",
                "linkedin.com/"):
        i = s.lower().find(dom)
        if i != -1:
            s = s[i + len(dom):]
            break
    s = s.split("?")[0].split("#")[0]
    if platform == "linkedin":
        s = re.sub(r"^(company|in)/", "", s, flags=re.I)
    if platform == "youtube":
        s = re.sub(r"^(channel|c|user)/", "", s, flags=re.I)
    s = s.strip("/").split("/")[0]
    return s.lstrip("@")


def resolve_qid_by_handle(platform, handle):
    """First Wikidata QID whose social property exactly matches this handle,
    or None. Instagram/TikTok store lowercase, so both cases are tried."""
    bare = _bare_handle(platform, handle)
    if not bare:
        return None
    variants = []
    for v in (bare, bare.lower()):
        if v and v not in variants:
            variants.append(v)
    for prop in _HANDLE_PROPS.get(platform, []):
        for v in variants:
            srch = 'haswbstatement:%s=%s' % (prop, v)
            data = _get_json(WIKIDATA_API, {"action": "query", "list": "search",
                                            "srsearch": srch, "srlimit": 3,
                                            "srnamespace": 0, "format": "json"})
            for hit in (((data or {}).get("query", {}) or {}).get("search", []) or []):
                qid = str(hit.get("title", ""))
                if qid.startswith("Q"):
                    return qid
    return None


def entity_label(qid):
    """English label (canonical real name) for a Wikidata QID, or ''."""
    if not qid:
        return ""
    return _labels([qid]).get(qid, "")


# social-account properties where an 'end time' qualifier means the account
# is closed / renamed / suspended -- such claims must never be used
_SOCIAL_PROPS = {"P2002", "P2003", "P2013", "P2397", "P11245",
                 "P7085", "P4264", "P3836", "P11892"}


def _claim_values(claims, prop, prefer_us=False):
    """Values for a property. With prefer_us=True, claims qualified with
    'place of publication'/'applies to' = United States (Q30) come first.
    Deprecated-rank claims are skipped; social claims carrying an 'end time'
    (P582) qualifier (defunct/suspended accounts) are skipped; within each
    group, preferred-rank claims come first."""
    groups = {(u, p): [] for u in (0, 1) for p in (0, 1)}  # (is_us, is_pref)
    for c in claims.get(prop, []):
        if c.get("rank") == "deprecated":
            continue
        quals = c.get("qualifiers", {}) or {}
        if prop in _SOCIAL_PROPS and "P582" in quals:
            continue  # account no longer active
        snak = c.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        val = snak.get("datavalue", {}).get("value")
        if isinstance(val, dict) and "id" in val:
            val = val["id"]
        if val is None:
            continue
        is_us = 0
        for qprop in ("P291", "P518", "P3005", "P1001"):
            for q in quals.get(qprop, []):
                qv = (q.get("datavalue", {}) or {}).get("value")
                if isinstance(qv, dict) and qv.get("id") == US_QID:
                    is_us = 1
        groups[(is_us, 1 if c.get("rank") == "preferred" else 0)].append(val)
    us = groups[(1, 1)] + groups[(1, 0)]
    other = groups[(0, 1)] + groups[(0, 0)]
    return us + other if prefer_us else other + us


def _parse_time(val):
    if not isinstance(val, dict):
        return None
    m = re.match(r"[+-]?(\d{4})-(\d{2})-(\d{2})", val.get("time", ""))
    if not m:
        return None
    y, mo, d = m.groups()
    return "%s-%s-%s" % (y, "01" if mo == "00" else mo, "01" if d == "00" else d)


def _is_film_or_tv(claims):
    return bool(set(_claim_values(claims, "P31")) & FILM_TV_TYPES)


def _imdb_title_ids(claims):
    """IMDb *title* ids (tt...) on a Wikidata item. A person carries nm..., so
    this also separates a work from a same-named person."""
    return [v for v in _claim_values(claims, "P345") if str(v).startswith("tt")]


def entity_is_wanted_work(ent, is_movie=True, tt=None):
    """True only when this Wikidata item really is the film/TV title being
    ingested -- so its social handles belong to the movie/show, not a same-named
    band, album, person or business.

    Positive evidence only: P31 (instance of) is a film/TV type, or the item
    carries an IMDb *title* id. A same-named work whose IMDb id contradicts the
    resolved one (tt) is rejected. This is what keeps a band's Facebook /
    Instagram / Twitter off a movie row (the 'Crawlers' case)."""
    if not ent:
        return False
    claims = ent.get("claims", {}) or {}
    ids = _imdb_title_ids(claims)
    if tt and ids and tt not in ids:
        return False
    return bool(_is_film_or_tv(claims) or ids)


# Slug tokens that signal a NON film/TV owner (a band or artist channel). A
# movie/TV handle should not contain these unless the title itself does.
_FOREIGN_HANDLE_MARKERS = ("band", "vevo", "topic")  # "band" already covers *bandofficial


def _handle_foreign_to_title(handle, title):
    """True when a social handle looks like a band/artist account rather than
    the title -- e.g. 'crawlersband' for the movie 'Crawlers'. A marker that
    also appears in the title (a film literally called 'The Band') is allowed."""
    h = re.sub(r"[^a-z0-9]", "", str(handle or "").lower())
    t = re.sub(r"[^a-z0-9]", "", str(title or "").lower())
    if not h:
        return False
    return any(m in h and m not in t for m in _FOREIGN_HANDLE_MARKERS)


def wikidata_meta(title, qid=None, is_movie=True, tt=None, verify=True):
    """Socials / RT / metacritic / distributor / genres off the Wikidata item.

    With verify=True (default) the item must be confirmed to be the film/TV
    title -- see entity_is_wanted_work -- before ANY field, above all its social
    handles, is trusted. This is the guard that keeps a same-named band, album
    or person from supplying a movie's Facebook / Instagram / Twitter."""
    entity = _entity(qid) if qid else None
    if entity is not None and verify and not entity_is_wanted_work(entity, is_movie, tt):
        log.info("wikidata_meta: rejected %s for %r - not the %s being ingested (P31=%s)",
                 qid, title, "film" if is_movie else "TV title",
                 ",".join(_claim_values(entity.get("claims", {}) or {}, "P31")) or "none")
        entity = None
    if entity is None:
        for cand in _search_candidates(title)[:5]:
            ent = _entity(cand)
            if ent and entity_is_wanted_work(ent, is_movie, tt):
                entity = ent
                break
        # No untyped fallback: taking the first search hit regardless of type is
        # exactly how a band's item used to reach a movie row.
    if entity is None:
        return {}
    claims = entity.get("claims", {})
    raw = {}
    for prop, key in PROPERTY_MAP.items():
        v = _claim_values(claims, prop)
        if v:
            raw[key] = v[0]
    meta = {}
    if "rottentomatoes" in raw:
        # movie-only rule: a /tv/ RT path is not accepted, so drop it rather
        # than emit an invalid URL (leaves a blank cell Ops can fill).
        _rt = clean_rottentomatoes(
            "http://www.rottentomatoes.com/" + raw["rottentomatoes"])
        if _rt:
            meta["rottentomatoes"] = _rt
    if "metacritic" in raw:
        meta["metacritic"] = "http://www.metacritic.com/" + raw["metacritic"].strip("/") + "/"
    if "imdb" in raw:
        meta["imdb_id"] = "http://www.imdb.com/title/" + raw["imdb"]
    # the title's OWN channel; the app decides how to combine it with the
    # network's channel when building youtube_channel_username
    yt = None
    if raw.get("youtube_handle"):
        yt = "http://www.youtube.com/@" + raw["youtube_handle"].lstrip("@")
    elif raw.get("youtube_id"):
        yt = "http://www.youtube.com/channel/" + raw["youtube_id"]
    if yt:
        meta["youtube_own_channel"] = yt
    if "twitter" in raw:
        meta["twitter_handle"] = raw["twitter"]
    if "instagram" in raw:
        meta["instagram_user"] = raw["instagram"]
    if "tiktok" in raw:
        meta["tiktok_user"] = raw["tiktok"].lstrip("@")
    if "pinterest" in raw:
        meta["pinterest_user_username"] = raw["pinterest"]
    if "facebook" in raw:
        meta["facebook_page"] = "http://www.facebook.com/" + raw["facebook"]
    for p in ("P577", "P580"):
        got = False
        for v in _claim_values(claims, p, prefer_us=True):
            d = _parse_time(v)
            if d:
                meta["released_on"] = d
                got = True
                break
        if got:
            break
    net_qid = None
    for p in (NETWORK_PROPS_MOVIE if is_movie else NETWORK_PROPS_TV):
        v = _claim_values(claims, p, prefer_us=True)
        if v:
            net_qid = v[0]
            break
    genre_qids = _claim_values(claims, "P136")
    labels = _labels(([net_qid] if net_qid else []) + genre_qids)
    if net_qid and labels.get(net_qid):
        meta["network"] = labels[net_qid]
    # Wikidata genre labels are lowercase and suffixed ('science fiction
    # film') -- clean them into Title Case tokens the LF taxonomy expects
    gnames = []
    for q in genre_qids:
        lbl = labels.get(q)
        if not lbl:
            continue
        lbl = re.sub(r"\s+(film|television series|tv series|series)$", "",
                     lbl.strip(), flags=re.IGNORECASE).strip()
        lbl = lbl.title().replace("Science Fiction", "Sci Fi")
        if lbl and lbl not in gnames:
            gnames.append(lbl)
    if gnames:
        meta["genre"] = "\n".join(gnames)
        meta["primary_genre"] = gnames[0]
    enwiki = entity.get("sitelinks", {}).get("enwiki")
    if enwiki and enwiki.get("title"):
        meta["wikipedia_page"] = "http://en.wikipedia.org/wiki/" + enwiki["title"].replace(" ", "_")
    return meta


# ---------------- Talent (people) ----------------
_GENDER_QIDS = {"Q6581097": "Gender - Man", "Q6581072": "Gender - Woman"}


# ---- profession hint ("Talent professional details" from Ops) -------------
# Ops type these freehand: "Football Player", "NBA basketball", "soccer
# player", "musician". The first implementation matched them with
#     hint = _norm(profession); hint_match = hint in _norm(description)
# but _norm() strips spaces as well as punctuation, so "NBA basketball"
# became "nbabasketball" and was tested against "americanbasketballplayer" --
# never a substring. Every multi-word hint silently failed and only bare
# single words that appear verbatim in a Wikidata description ("musician")
# ever worked. We tokenise the hint instead and expand league/colloquial
# shorthand into the vocabulary Wikidata actually uses in its labels.
_HINT_STOPWORDS = {
    "a", "an", "and", "at", "for", "in", "of", "on", "or", "the",
    "player", "players", "playing", "plays", "professional", "pro",
    "former", "ex", "retired", "current", "star", "famous", "celebrity",
    "personality", "person", "people", "talent", "career", "detail",
    "details", "league", "team", "club", "sport", "sports",
}

# shorthand -> terms that appear in Wikidata P106/P641 labels & descriptions
_HINT_ALIASES = {
    "nba": ["basketball"],
    "wnba": ["basketball"],
    "nfl": ["american football"],
    "mlb": ["baseball"],
    "nhl": ["ice hockey", "hockey"],
    "mls": ["association football", "soccer"],
    "fifa": ["association football", "soccer"],
    "epl": ["association football", "soccer"],
    "premier": ["association football", "soccer"],
    "soccer": ["association football", "soccer"],
    "footballer": ["football", "association football"],
    "gridiron": ["american football"],
    "f1": ["formula one", "racing driver", "motorsport"],
    "nascar": ["racing driver", "motorsport"],
    "ufc": ["mixed martial arts"],
    "mma": ["mixed martial arts"],
    "wwe": ["professional wrestling"],
    "wrestler": ["professional wrestling", "wrestling"],
    "boxer": ["boxer", "boxing"],
    "singer": ["singer", "musician"],
    "rapper": ["rapper", "musician"],
    "musician": ["musician", "singer", "songwriter", "composer"],
    "dj": ["disc jockey", "record producer"],
    "actress": ["actor"],
    "filmmaker": ["film director", "director", "film producer"],
    "youtuber": ["youtuber", "internet celebrity"],
    "influencer": ["influencer", "internet celebrity"],
    "streamer": ["streamer", "internet celebrity"],
    "presenter": ["television presenter", "presenter"],
    "host": ["television presenter", "radio host", "presenter"],
    "anchor": ["journalist", "news presenter"],
    "author": ["author", "writer"],
    "businessman": ["businessperson", "entrepreneur"],
    "businesswoman": ["businessperson", "entrepreneur"],
    "ceo": ["businessperson", "entrepreneur"],
    "athlete": ["athlete", "sportsperson"],
}


# What to do when a profession hint matches nobody:
#   * several people share the name -> ALWAYS blank + flag. We cannot tell which
#     one Ops meant, and a wrong IMDb id is expensive to unpick downstream.
#   * exactly one person has the name -> keep them, flag for review. There is no
#     rival to confuse them with, and freehand hints ("bassist", "Beatle") often
#     just do not echo Wikidata's wording. Set this True to blank those too.
STRICT_UNIQUE_HINT = os.getenv("STRICT_UNIQUE_HINT", "0") == "1"


def _clean_person_name(name):
    """Strip stray leading/trailing punctuation that comes in with pasted
    lists -- "Bill Murray:" must look up (and export) as "Bill Murray".
    Interior punctuation and a trailing period are preserved so "Martin
    Luther King Jr." and "Anna-Maria O'Brien" survive intact."""
    s = (name or "").strip()
    s = re.sub(r"^[\s\-–—:;,.|/\\*#>\"']+", "", s)
    s = re.sub(r"[\s\-–—:;,|/\\*#\"']+$", "", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _fold(s):
    """Lowercase and ASCII-fold, keeping word boundaries. Deleting non-ASCII
    outright turned "Fútbol" into "tbol" and guaranteed a false rejection."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode(
        "ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9\s]", " ", s.lower())


def _hint_terms(hint):
    """Expand a freehand profession hint into lowercase match terms.
    Multi-word terms ("american football") are kept whole so they can be
    matched as phrases. Returns [] for a blank or content-free hint."""
    raw = _fold(hint)
    words = [w for w in raw.split() if w]
    terms = []   # ORDER MATTERS: callers walk these in priority order, so
                 # _HINT_ALIASES' own ordering must survive (for "host",
                 # "television presenter" has to come before "radio host").

    def _add(t):
        if t and t not in terms:
            terms.append(t)
    for w in words:
        if w in _HINT_ALIASES:
            for a in _HINT_ALIASES[w]:
                _add(a)
            _add(w)
        elif w not in _HINT_STOPWORDS and len(w) > 2:
            _add(w)
    kept = [w for w in words if w not in _HINT_STOPWORDS and len(w) > 2]
    if len(kept) > 1:
        _add(" ".join(kept))
    return terms


def _hint_haystack(ent, extra_labels=()):
    """Lowercase text a hint is scored against: the English description plus
    the resolved occupation (P106) and sport (P641) labels. The description
    alone is far too thin -- it is terse for some people and absent for
    others, which is why hint matching had nothing to bite on."""
    parts = []
    if ent:
        d = (ent.get("descriptions", {}).get("en", {}) or {}).get("value", "")
        if d:
            parts.append(str(d))
    parts.extend(str(x) for x in extra_labels if x)
    return _fold(" ".join(parts))


def _hint_score(terms, haystack):
    """How strongly a candidate supports the profession hint: whole-word hits,
    multi-word phrases weighted double. 0 == the hint is unsupported, which is
    treated as "not this person" rather than "close enough"."""
    if not terms or not haystack:
        return 0
    score = 0
    for t in terms:
        if re.search(r"\b" + re.escape(t) + r"\b", haystack):
            score += 2 if " " in t else 1
    return score


def _person_base_name(entity, meta, provided):
    """Base name for the IMDb nm-code lookup. Rule: use the person's Wikipedia
    article title when they have one (the canonical spelling -- it corrects a
    mistyped/variant input and pins the search to the SAME person Wikipedia
    identifies); with no Wikipedia page, fall back to the provided name."""
    title = ""
    if entity:
        sl = (entity.get("sitelinks", {}) or {}).get("enwiki") or {}
        title = sl.get("title") or ""
    if not title and meta.get("wikipedia_page"):
        title = str(meta["wikipedia_page"]).rsplit("/", 1)[-1].replace("_", " ")
    title = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()  # drop "(actress)" etc.
    return title or provided


def fetch_person(name, qid=None, profession=""):
    """Auto-discover a PERSON (talent) via Wikidata + IMDb suggestion API.
    Returns: socials, wikipedia_page, imdb_id (nm), gender line, occupation
    labels, sport labels, us_citizen flag. Fails soft ({}).

    When qid is given the candidate search is skipped and that entity is used
    directly (handle-first resolution).

    `profession` is an optional professional-details hint from Ops (e.g.
    "Actor", "NBA basketball", "soccer player"). It is the PRIMARY
    discriminator between people who share a name: the hint is expanded into
    match terms and scored against each candidate's description, occupations
    (P106) and sports (P641). The hint is also fed into the Wikidata search so
    the intended person actually enters the candidate pool. Ignored when qid is
    given (an explicit entity needs no disambiguation).

    When a hint is supplied and NO candidate supports it, the lookup returns
    `needs_review` with no discovered ids instead of falling back to the most
    prominent same-name person -- a wrong IMDb id costs far more to unpick
    downstream than a blank cell costs to fill in.
    """
    if not name and not qid:
        return {}
    clean = re.sub(r"\s*-\s*DAR\s*$", "", name or "", flags=re.IGNORECASE).strip()
    clean = _clean_person_name(clean)
    clean, _ = _split_disambiguator(clean)
    clean = _clean_person_name(clean)
    hint_raw = "" if qid else (profession or "").strip()
    hint_terms = _hint_terms(hint_raw)
    # Tuple key, not concatenation: a name containing '|' would otherwise
    # collide with a name+hint key.
    # Include the RAW hint: "soccer" and "soccer player" expand to the same
    # terms, but each must keep its own review wording and its own search.
    key = (("person:qid", qid) if qid
           else ("person", clean.lower(), hint_raw.lower(), tuple(hint_terms)))
    if key in _CACHE:
        return dict(_CACHE[key])
    meta = {}
    hint_matched = False
    hint_unconfirmed = False  # entity kept, but the hint did not corroborate it
    had_candidates = False    # any SAME-NAME human found at all
    n_named = 0               # how many same-name humans were seen
    label_map = {}
    try:
        # An explicit qid wins outright (handle-first resolution). If that fetch
        # fails we must NOT fall back to a name search -- adopting whoever
        # shares the name is worse than returning nothing.
        entity = _entity(qid) if qid else None
        if not qid:
            # Hint-qualified queries go FIRST. The intended person is often
            # outside the top hits for the bare name (for "David Lee" the other
            # David Lees crowd it out), and the candidate caps below would trim
            # the hint results away if the bare-name hits were queued ahead.
            searches = []
            if hint_terms:      # a hint of only stopwords must not steer the
                                # search, or it biases without being scorable
                searches.append(clean + " " + hint_raw)
                searches.extend(clean + " " + t for t in hint_terms if " " in t)
            seen_q, queries = set(), []
            for s in searches[:2]:             # Wikidata search is case-
                k = " ".join(s.lower().split())  # insensitive: dedupe on that
                if k and k not in seen_q:
                    seen_q.add(k)
                    queries.append(s)
            # The bare name is ALWAYS searched, never truncated away: it is the
            # only query guaranteed to surface the same-name people, and
            # had_candidates / the hard-fail decision depend on seeing them.
            if " ".join(clean.lower().split()) not in seen_q:
                queries.append(clean)
            # Round-robin the per-query results so a hint query returning many
            # hits cannot starve the bare-name query (or vice versa) once the
            # candidate cap bites.
            per_query = [_search_candidates(s, limit=8) for s in queries]
            cand_qids = []
            for i in range(max((len(p) for p in per_query), default=0)):
                for p in per_query:
                    if i < len(p) and p[i] not in cand_qids:
                        cand_qids.append(p[i])
            humans = []  # (entity, name_match, occ_qids, sport_qids)
            for c in cand_qids[:12]:
                ent = _entity(c)
                if not ent:
                    continue
                claims = ent.get("claims", {})
                if "Q5" not in set(_claim_values(claims, "P31")):
                    continue  # not a human
                lbl = (ent.get("labels", {}).get("en", {}) or {}).get("value", "")
                name_match = _norm(lbl) == _norm(clean) or any(
                    _norm(a.get("value")) == _norm(clean)
                    for a in ent.get("aliases", {}).get("en", []))
                humans.append((ent, name_match,
                               _claim_values(claims, "P106")[:12],
                               _claim_values(claims, "P641")[:4]))
                if not hint_terms and name_match:
                    break        # no hint: first name match wins, as before
                if len(humans) >= 6:
                    break
            had_candidates = any(h[1] for h in humans)
            # Resolve every candidate's occupation/sport labels in ONE batched
            # call so hint scoring can see them without a request per person.
            if hint_terms and humans:
                want = []
                for _ent, _nm, occ, spo in humans:
                    for q in occ + spo:
                        if q not in want:
                            want.append(q)
                label_map = _labels(want[:50])
            scored = []  # (hint_score, name_match, entity)
            for ent, name_match, occ, spo in humans:
                hay = _hint_haystack(ent, [label_map.get(q) for q in occ + spo])
                scored.append((_hint_score(hint_terms, hay), name_match, ent))
            if scored:
                if hint_terms:
                    # The hint decides BETWEEN same-name people; it must never
                    # promote a differently-named person, or searching
                    # "Mark Wahlberg soccer player" can return a footballer
                    # called Mark Walberg. Name match is a hard filter here.
                    named = [c for c in scored if c[1]]
                    named.sort(key=lambda c: c[0], reverse=True)
                    n_named = len(named)
                    if named and named[0][0] > 0:
                        hint_matched = True
                        entity = named[0][2]
                    elif n_named == 1 and not STRICT_UNIQUE_HINT:
                        # Only ONE person has this name, so there is no rival to
                        # confuse them with and nothing for the hint to
                        # disambiguate. Ops hints are freehand ("bassist",
                        # "Beatle") and often do not echo Wikidata's wording --
                        # discarding a unique, unambiguous person over that
                        # would make the hint actively harmful. Keep them, but
                        # flag it and do not classify from the unmatched hint.
                        entity = named[0][2]
                        hint_unconfirmed = True
                    else:
                        entity = None
                else:
                    scored.sort(key=lambda c: c[1], reverse=True)
                    entity = scored[0][2]
        if entity is None and hint_terms and had_candidates:
            # Several people share this name and none support the professional
            # details, so picking the most prominent is a coin flip on the wrong
            # person. Return nothing and flag it rather than export a wrong id.
            meta["needs_review"] = True
            meta["review_reason"] = (
                "Found %d people named '%s' but none match the professional "
                "details '%s' - check the spelling or the profession."
                % (n_named, clean, hint_raw))
            meta["profession"] = hint_raw
            meta["hint_matched"] = False
            # The hint was actively contradicted by every candidate, so it must
            # not be used to classify either -- otherwise a rejected "soccer
            # player" still stamps the row "Athlete - Soccer".
            meta["hint_rejected"] = True
            _CACHE[key] = dict(meta)
            return dict(meta)
        if entity is None and hint_terms:
            # Nobody of this name in Wikidata at all: there is no rival
            # candidate for the hint to contradict, so fall through to the
            # normal no-hint path (IMDb suggestion + hint-seeded occupation).
            # Supplying professional details must never return LESS than
            # omitting them. Still flagged, so Ops verify before ingestion.
            meta["needs_review"] = True
            meta["review_reason"] = (
                "No Wikidata entry for '%s' - details below come from an "
                "exact IMDb name match only and are unverified." % clean)
        elif hint_unconfirmed:
            # Unique same-name person kept despite an unmatched hint.
            meta["needs_review"] = True
            meta["review_reason"] = (
                "Only one person is named '%s', so their details were used, but "
                "nothing about them matches the professional details '%s' - "
                "confirm this is the right person."
                % (clean, hint_raw))
            # Classification must come from Wikidata, not the unmatched hint.
            meta["hint_rejected"] = True
        if entity is not None:
            claims = entity.get("claims", {})
            raw = {}
            for prop, k in PROPERTY_MAP.items():
                v = _claim_values(claims, prop)
                if v:
                    raw[k] = v[0]
            if raw.get("imdb", "").startswith("nm"):
                meta["imdb_id"] = "https://www.imdb.com/name/" + raw["imdb"]
            if "twitter" in raw:
                meta["twitter_handle"] = raw["twitter"]
            if "instagram" in raw:
                meta["instagram_user"] = str(raw["instagram"]).lower()
            if "facebook" in raw:
                meta["facebook_page"] = "https://www.facebook.com/" + raw["facebook"]
            if "tiktok" in raw:
                meta["tiktok_user"] = str(raw["tiktok"]).lstrip("@")
            yt = None
            if raw.get("youtube_handle"):
                yt = "https://www.youtube.com/@" + raw["youtube_handle"].lstrip("@")
            elif raw.get("youtube_id"):
                yt = "https://www.youtube.com/channel/" + raw["youtube_id"]
            if yt:
                meta["youtube_channel_username"] = yt
            enwiki = entity.get("sitelinks", {}).get("enwiki")
            if enwiki and enwiki.get("title"):
                meta["wikipedia_page"] = ("https://en.wikipedia.org/wiki/"
                                          + enwiki["title"].replace(" ", "_"))
            for g in _claim_values(claims, "P21"):
                if g in _GENDER_QIDS:
                    meta["gender"] = _GENDER_QIDS[g]
                    break
            occ_qids = _claim_values(claims, "P106")[:12]
            sport_qids = _claim_values(claims, "P641")[:4]
            # Reuse the batched candidate-scoring labels; only ask for the ones
            # we have not already resolved.
            labels = dict(label_map)
            missing = [q for q in occ_qids + sport_qids if q not in labels]
            if missing:
                labels.update(_labels(missing))
            meta["occupations"] = [labels[q] for q in occ_qids if labels.get(q)]
            meta["sports"] = [labels[q] for q in sport_qids if labels.get(q)]
            meta["us_citizen"] = "Q30" in set(_claim_values(claims, "P27"))
        # IMDb nm id fallback via the suggestion API (people come back as nm...).
        # Base the search on the VERIFIED Wikipedia article name when the person
        # has one -- the canonical spelling corrects a mistyped input (e.g.
        # 'Kara Young') and pins it to the same person; else the provided name.
        if not meta.get("imdb_id"):
            base = _person_base_name(entity, meta, clean)
            if base:
                q = urllib.parse.quote(base.strip().lower())
                data = _get_json(IMDB_SUGGEST.format(q=q), headers=HTML_HEADERS)
                for it in (data or {}).get("d", []):
                    if str(it.get("id", "")).startswith("nm") and _norm(it.get("l")) == _norm(base):
                        meta["imdb_id"] = "https://www.imdb.com/name/" + it["id"]
                        break
    except Exception as e:  # noqa: BLE001
        log.warning("fetch_person failed for %r: %s", name, e)
    # Keep the Ops hint on the payload for classification, but never overwrite
    # occupations discovered from Wikidata -- classification decides which of
    # the two wins (hint first, then P106 order), not this function.
    if hint_raw:
        meta["profession"] = hint_raw
        meta["hint_matched"] = bool(hint_matched)
        if not meta.get("occupations") and not meta.get("hint_rejected"):
            meta["occupations"] = [hint_raw]
    verify_socials(meta)
    _CACHE[key] = dict(meta)
    return meta


# ---------------- Video Games ----------------
_GAME_TYPES = {"Q7889", "Q116776512", "Q865493"}  # video game (+ expansions)


def fetch_game(name, qid=None):
    """Auto-discover a VIDEO GAME via Wikidata: developer (P178), publisher
    (P123), platforms (P400), genres (P136), release (P577), socials,
    wikipedia, metacritic, imdb. Fails soft ({}). qid skips the search."""
    if not name and not qid:
        return {}
    clean = re.sub(r"\s*-\s*DAR\s*$", "", name or "", flags=re.IGNORECASE).strip()
    clean, _ = _split_disambiguator(clean)
    key = ("game:qid:" + qid,) if qid else ("game:" + clean.lower(),)
    if key in _CACHE:
        return dict(_CACHE[key])
    meta = {}
    try:
        entity = _entity(qid) if qid else None
        fallback = None
        for cand in ([] if qid else _search_candidates(clean, limit=8)[:6]):
            ent = _entity(cand)
            if not ent:
                continue
            claims = ent.get("claims", {})
            is_game = bool(set(_claim_values(claims, "P31")) & _GAME_TYPES)
            has_gamey = bool(_claim_values(claims, "P178") or _claim_values(claims, "P400"))
            lbl = (ent.get("labels", {}).get("en", {}) or {}).get("value", "")
            if (is_game or has_gamey) and _norm(lbl) == _norm(clean):
                entity = ent
                break
            if is_game and fallback is None:
                fallback = ent
        entity = entity or fallback
        if entity is not None:
            claims = entity.get("claims", {})
            raw = {}
            for prop, k in PROPERTY_MAP.items():
                v = _claim_values(claims, prop)
                if v:
                    raw[k] = v[0]
            dev_qids = _claim_values(claims, "P178")[:2]
            pub_qids = _claim_values(claims, "P123", prefer_us=True)[:2]
            plat_qids = _claim_values(claims, "P400")[:6]
            genre_qids = _claim_values(claims, "P136")[:4]
            labels = _labels(dev_qids + pub_qids + plat_qids + genre_qids)
            if dev_qids and labels.get(dev_qids[0]):
                meta["developer"] = labels[dev_qids[0]]
            if pub_qids and labels.get(pub_qids[0]):
                meta["network"] = labels[pub_qids[0]]  # publisher
            plats = [labels[q] for q in plat_qids if labels.get(q)]
            if plats:
                meta["platforms"] = plats
            gnames = [labels[q] for q in genre_qids if labels.get(q)]
            if gnames:
                g0 = re.sub(r"\s+(video )?game$", "", gnames[0].strip(),
                            flags=re.IGNORECASE).strip().title()
                meta["genre"] = g0
            for v in _claim_values(claims, "P577", prefer_us=True):
                d = _parse_time(v)
                if d:
                    meta["released_on"] = d
                    break
            if raw.get("imdb"):
                meta["imdb_id"] = "https://www.imdb.com/title/" + raw["imdb"]
            if raw.get("metacritic"):
                meta["metacritic"] = ("https://www.metacritic.com/"
                                      + raw["metacritic"].strip("/") + "/")
            if "twitter" in raw:
                meta["twitter_handle"] = raw["twitter"]
            if "instagram" in raw:
                meta["instagram_user"] = str(raw["instagram"]).lower()
            if "facebook" in raw:
                meta["facebook_page"] = "https://www.facebook.com/" + raw["facebook"]
            if "tiktok" in raw:
                meta["tiktok_user"] = str(raw["tiktok"]).lstrip("@")
            enwiki = entity.get("sitelinks", {}).get("enwiki")
            if enwiki and enwiki.get("title"):
                meta["wikipedia_page"] = ("https://en.wikipedia.org/wiki/"
                                          + enwiki["title"].replace(" ", "_"))
    except Exception as e:  # noqa: BLE001
        log.warning("fetch_game failed for %r: %s", name, e)
    if meta.get("metacritic"):
        alive = _mc_alive(meta["metacritic"]) if VALIDATE_URLS else True
        if alive is False:
            meta.pop("metacritic")
    verify_socials(meta)
    _CACHE[key] = dict(meta)
    return meta


# ---------------- Brands (Beauty / Beverages / Sports Teams / General) ---------------
# P31 values that identify a brand / company / organization / sports team.
_BRAND_TYPES = {
    "Q431289",    # brand
    "Q4830453",   # business
    "Q783794",    # company
    "Q891723",    # public company
    "Q167037",    # corporation
    "Q6881511",   # enterprise
    "Q43229",     # organization
    "Q4438121",   # sports organization
    "Q12973014",  # sports team
    "Q476028",    # association football club
}
# never accept these as a brand match
_NOT_BRAND_TYPES = {"Q5", "Q4167410"}  # human, disambiguation page


def fetch_brand(name, qid=None):
    """Auto-discover a BRAND (Beauty / Beverages / Sports / General) via
    Wikidata: social accounts, Wikipedia page, ticker symbol.

    Unlike the film/TV path, candidates are NOT filtered to FILM_TV_TYPES --
    that filter is exactly why brand lookups used to come back empty. Returns
    keys named after the BrandDef columns so create_tfx_row can map them
    straight onto the row. Fails soft ({}). qid skips the search."""
    if not name and not qid:
        return {}
    clean = re.sub(r"\s*-\s*DAR\s*$", "", name or "", flags=re.IGNORECASE).strip()
    clean, _ = _split_disambiguator(clean)
    key = ("brand:qid:" + qid,) if qid else ("brand:" + clean.lower(),)
    if key in _CACHE:
        return dict(_CACHE[key])
    meta = {}
    try:
        skip = FILM_TV_TYPES | _GAME_TYPES | _NOT_BRAND_TYPES
        entity = brand_fb = name_fb = None
        if qid:
            entity = _entity(qid)
        for cand in ([] if qid else _search_candidates(clean, limit=8)[:6]):
            ent = _entity(cand)
            if not ent:
                continue
            claims = ent.get("claims", {})
            p31 = set(_claim_values(claims, "P31"))
            if p31 & skip:
                continue  # a film/show/game/person, not a brand
            has_social = any(p in claims for p in _SOCIAL_PROPS)
            is_brandish = bool(p31 & _BRAND_TYPES)
            lbl = (ent.get("labels", {}).get("en", {}) or {}).get("value", "")
            name_match = _norm(lbl) == _norm(clean) or any(
                _norm(a.get("value")) == _norm(clean)
                for a in ent.get("aliases", {}).get("en", []))
            if name_match and (is_brandish or has_social):
                entity = ent  # exact name + clearly a brand: take it
                break
            if brand_fb is None and is_brandish and has_social:
                brand_fb = ent  # right shape, name didn't match exactly
            if name_fb is None and name_match:
                name_fb = ent   # weakest: name-only match
        entity = entity or brand_fb or name_fb
        if entity is not None:
            claims = entity.get("claims", {})
            raw = {}
            for prop, k in PROPERTY_MAP.items():
                v = _claim_values(claims, prop)
                if v:
                    raw[k] = v[0]
            if "facebook" in raw:
                meta["facebook_page"] = ("http://www.facebook.com/"
                                         + str(raw["facebook"]).strip("/"))
            if "twitter" in raw:
                meta["twitter_handle"] = str(raw["twitter"]).lstrip("@")
            if "instagram" in raw:
                meta["instagram_user"] = str(raw["instagram"]).lower().lstrip("@")
            if "tiktok" in raw:
                meta["tiktok_user"] = str(raw["tiktok"]).lstrip("@")
            if "pinterest" in raw:
                meta["pinterest_user_username"] = str(raw["pinterest"]).strip("/")
            if "linkedin_co" in raw:
                meta["linkedin_page"] = ("http://www.linkedin.com/company/"
                                         + str(raw["linkedin_co"]).strip("/"))
            yt = None
            if raw.get("youtube_handle"):
                yt = "http://www.youtube.com/@" + str(raw["youtube_handle"]).lstrip("@")
            elif raw.get("youtube_id"):
                yt = "http://www.youtube.com/channel/" + raw["youtube_id"]
            if yt:
                meta["youtube_channel_username"] = yt
            tumblr = _claim_values(claims, "P3943")  # Tumblr username
            if tumblr:
                meta["tumblr_page"] = str(tumblr[0]).strip("/")
            ticker = _claim_values(claims, "P249")   # ticker symbol
            if ticker:
                meta["ticker_symbol"] = str(ticker[0])
            enwiki = entity.get("sitelinks", {}).get("enwiki")
            if enwiki and enwiki.get("title"):
                meta["wikipedia_page"] = ("http://en.wikipedia.org/wiki/"
                                          + enwiki["title"].replace(" ", "_"))
    except Exception as e:  # noqa: BLE001
        log.warning("fetch_brand failed for %r: %s", name, e)
    verify_socials(meta)
    _CACHE[key] = dict(meta)
    return meta


def youtube_channel(title):
    """The title's own channel via the YouTube Data API (optional key)."""
    if not YOUTUBE_API_KEY:
        return {}
    data = _get_json(YT_SEARCH, {"part": "snippet", "type": "channel", "maxResults": 1,
                                 "q": title, "key": YOUTUBE_API_KEY})
    items = (data or {}).get("items", [])
    if not items:
        return {}
    snip = items[0].get("snippet", {})
    cid = snip.get("channelId") or items[0].get("id", {}).get("channelId")
    # only trust the hit when the channel is literally named like the title
    if not cid or _norm(snip.get("title")) != _norm(title):
        return {}
    return {"youtube_own_channel": "http://www.youtube.com/channel/" + cid}


# ---------------- merge / entry ----------------
def _fill(dst, src):
    for k, v in (src or {}).items():
        if v not in (None, "") and dst.get(k) in (None, ""):
            dst[k] = v


_CACHE = {}


def _enrich_by_tt(tt, is_movie, title_hint, wikidata_id=None):
    """Merge all sources keyed off an exact IMDb tt (see module docstring
    for the field-priority rationale)."""
    meta = {}

    # 0) upcoming-release-movies service (BOM calendar): distributor, genres,
    #    release date + Wide/Limited scale -- authoritative when present
    if is_movie:
        _fill(meta, _upcoming_meta(_upcoming_index()["by_tt"].get(tt)))

    # 1) Box Office Mojo -- US Domestic Distributor (released titles only)
    _fill(meta, bom_scrape(tt))

    # 2) Wikipedia article, verified against the IMDb id
    wurl, wtitle, wqid = wiki_lookup(title_hint, is_movie, tt=tt)
    if wurl:
        meta.setdefault("wikipedia_page", wurl)
    if wtitle and not meta.get("network"):
        dist = wiki_infobox_network(wtitle, is_movie)
        if dist:
            meta["network"] = dist

    # 3) Wikidata item -> RT / metacritic / socials / own-YouTube / distributor
    _fill(meta, wikidata_meta(title_hint, qid=(wqid or wikidata_id), is_movie=is_movie, tt=tt))

    # 4) OMDb by exact id -> genre / release fallback (reliable API)
    _fill(meta, omdb_by_id(tt))

    # 5) TMDB by exact id -> socials, genres, US theatrical date, wikidata id
    tmeta, wid = tmdb_find_by_imdb(tt)
    us_rel = tmeta.pop("released_on_us", None)
    if us_rel:
        meta["released_on"] = us_rel  # US theatrical beats festival/first dates
    prod_co = tmeta.pop("production_company", None)
    _fill(meta, tmeta)
    if not (wqid or wikidata_id) and wid:
        _fill(meta, wikidata_meta(title_hint, qid=wid, is_movie=is_movie, tt=tt))

    # 6) IMDb page scrape -- genre + datePublished as last resort
    imeta = imdb_scrape(tt)
    imdb_prod_co = imeta.pop("production_company", None)
    _fill(meta, imeta)

    # production company is a LAST RESORT for network (it caused wrong
    # distributor attributions before -- e.g. prod-co instead of Neon)
    if not meta.get("network"):
        meta["network"] = imdb_prod_co or prod_co or ""
        if not meta["network"]:
            meta.pop("network")

    if not meta.get("youtube_own_channel"):
        _fill(meta, youtube_channel(title_hint))
    meta.setdefault("imdb_id", "http://www.imdb.com/title/" + tt)

    # release-date first for MC & RT (same rule as IMDb): the known year picks
    # the correct same-named title's page.
    _yr = _year_from(meta.get("released_on"))

    # metacritic: verify what we found. A Wikidata URL is curated (kept unless
    # definitively 404); the calendar service's slug guess is only kept when the
    # page really exists; otherwise a year-suffixed slug fallback is tried.
    guess = meta.pop("_metacritic_guess", None)
    curated = bool(meta.get("metacritic"))
    mc = resolve_metacritic(title_hint, is_movie,
                            candidate=meta.get("metacritic") or guess,
                            curated=curated, year=_yr)
    if mc:
        meta["metacritic"] = mc
    else:
        meta.pop("metacritic", None)

    # rotten tomatoes: same treatment -- trust a curated Wikidata URL, else try
    # the year-suffixed /m/ slug; keep a generated URL only if it verifies.
    rt = resolve_rottentomatoes(title_hint, is_movie,
                                candidate=meta.get("rottentomatoes"),
                                curated=bool(meta.get("rottentomatoes")), year=_yr)
    if rt:
        meta["rottentomatoes"] = rt
    else:
        meta.pop("rottentomatoes", None)

    # drop wrong-owner (band/artist) handles first, then dead ones
    verify_socials(meta, title_hint, reject_foreign=True)
    return meta


def fetch_metadata_by_tt(tt, is_movie=True, title="", year_hint=""):
    """Preferred entry point when the exact IMDb id is known (reliable).

    When a release year is known, the tt's own year is checked against it and a
    disagreement is surfaced via '_imdb_year_note' -- the id is kept (it was
    supplied explicitly) but the reviewer is told it looks wrong for the date."""
    tt = _tt(tt)
    if not tt:
        return {}
    want_year = _year_from(year_hint)
    key = ("tt:" + tt, bool(is_movie), want_year or 0)
    if key in _CACHE:
        return dict(_CACHE[key])
    meta = {}
    try:
        hint_title, _ = _split_disambiguator(
            re.sub(r"\s*-\s*DAR\s*$", "", title or "", flags=re.IGNORECASE).strip())
        meta = _enrich_by_tt(tt, is_movie, hint_title or tt)
        if want_year:
            yr = _year_from(meta.get("released_on"))
            if yr and abs(yr - want_year) > IMDB_YEAR_TOLERANCE:
                meta["_imdb_year_note"] = (
                    "IMDb %s is dated %s but the release year is %s -- verify it is "
                    "the right title." % (tt, yr, want_year))
    except Exception as e:  # noqa: BLE001
        log.warning("fetch_metadata_by_tt failed for %s: %s", tt, e)
    _CACHE[key] = dict(meta)
    return meta


def fetch_metadata(title, is_movie=True, year_hint=""):
    """Entry point when only the title is known. Resolution order:
    IMDb suggestion API (exact, keyless) > TMDB search > OMDb search.

    When a release date/year is known (year_hint -- e.g. from the ingest sheet
    a reviewer is checking), it is resolved FIRST and used to pick the IMDb entry
    for that year, so a same-named title from another year is not matched. If no
    candidate fits the year, no IMDb id is attached and '_imdb_year_note' is set
    so the caller can flag it for review."""
    if not title:
        return {}
    clean = re.sub(r"\s*-\s*DAR\s*$", "", title, flags=re.IGNORECASE).strip()
    want_year = _year_from(year_hint)  # known release year makes lookup year-specific
    key = (clean.lower(), bool(is_movie), want_year or 0)
    if key in _CACHE:
        return dict(_CACHE[key])

    # a trailing '(2026)' / '(Netflix)' disambiguator is NOT part of the real
    # name -- all lookups use the stripped title. An explicit release year wins
    # over a parenthetical one as the same-named-title tie-breaker.
    lookup, hint = _split_disambiguator(clean)
    if not want_year and hint.isdigit() and len(hint) == 4:
        want_year = int(hint)
    year_hint = str(want_year) if want_year else ""

    meta = {}
    try:
        # the upcoming-release-movies calendar resolves the tt code by exact
        # title AND supplies distributor/genre/date/scale -- but only trust it
        # when its year agrees with the known release year
        um = _upcoming_index()["by_title"].get(_norm(lookup)) if is_movie else None
        if um and want_year:
            uy = _year_from(um.get("release_date"))
            if uy and abs(uy - want_year) > IMDB_YEAR_TOLERANCE:
                um = None
        sug = imdb_suggest_item(lookup, is_movie, year_hint,
                                require_year=bool(want_year)) if not um else None
        tt = _tt((um or {}).get("tt_code")) or (sug or {}).get("id")
        # the IMDb item type gives the TV Program Type (Series / Mini-Series /
        # TV Movie / Special) used by the BrandIngest schema
        sug_ptype = _IMDB_QID_PROGRAM_TYPE.get(str((sug or {}).get("qid") or "").lower())
        tmdb_meta, wid = ({}, None)
        if not tt:
            tmdb_meta, wid = tmdb_lookup(lookup, is_movie, want_year)
            tt = _tt(tmdb_meta.get("imdb_id")) or _tt(omdb_lookup(lookup, want_year).get("imdb_id"))
        if tt:
            meta = _enrich_by_tt(tt, is_movie, lookup, wikidata_id=wid)
            tmdb_meta.pop("production_company", None)
            tmdb_meta.pop("released_on_us", None)
            _fill(meta, tmdb_meta)
        else:
            tmdb_meta.pop("production_company", None)
            tmdb_meta.pop("released_on_us", None)
            _fill(meta, tmdb_meta)
            _fill(meta, wikidata_meta(lookup, qid=wid, is_movie=is_movie))
            _fill(meta, omdb_lookup(lookup, want_year))
            _yr = want_year or _year_from(meta.get("released_on"))
            mc = resolve_metacritic(lookup, is_movie,
                                    candidate=meta.get("metacritic"),
                                    curated=bool(meta.get("metacritic")), year=_yr)
            if mc:
                meta["metacritic"] = mc
            else:
                meta.pop("metacritic", None)
            rt = resolve_rottentomatoes(lookup, is_movie,
                                        candidate=meta.get("rottentomatoes"),
                                        curated=bool(meta.get("rottentomatoes")), year=_yr)
            if rt:
                meta["rottentomatoes"] = rt
            else:
                meta.pop("rottentomatoes", None)
            verify_socials(meta, lookup, reject_foreign=True)
        if sug_ptype:
            meta["program_type"] = sug_ptype  # IMDb's own type beats TMDB's
        # date is authoritative: if we still hold an IMDb id whose year disagrees
        # with the known release year, drop it; if none resolved for that year,
        # leave IMDb blank and flag for a human reviewer (never guess).
        if want_year and _tt(meta.get("imdb_id")):
            yr = _year_from(meta.get("released_on")) \
                or _year_from(omdb_by_id(_tt(meta["imdb_id"])).get("released_on"))
            if yr and abs(yr - want_year) > IMDB_YEAR_TOLERANCE:
                log.info("fetch_metadata: dropping IMDb %s (year %s) for %r, want ~%s",
                         meta.get("imdb_id"), yr, title, want_year)
                meta.pop("imdb_id", None)
        if want_year and not _tt(meta.get("imdb_id")):
            meta["_imdb_year_note"] = (
                "No IMDb title found for release year %s; left blank for review."
                % want_year)
    except Exception as e:  # noqa: BLE001
        log.warning("fetch_metadata failed for %r: %s", title, e)

    _CACHE[key] = dict(meta)
    return meta


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    t = sys.argv[1] if len(sys.argv) > 1 else "Dune"
    print(json.dumps(fetch_metadata(t, True), indent=2))
