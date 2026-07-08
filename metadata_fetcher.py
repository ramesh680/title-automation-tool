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
import time
import urllib.parse
from datetime import date, timedelta

try:
    import requests
    _SESSION = requests.Session()
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


# ---------------- low level ----------------
def _get_json(url, params=None, headers=None):
    if _SESSION is None:
        return None
    try:
        r = _SESSION.get(url, params=params, headers=headers or HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("json request failed (%s): %s", url, e)
        return None


def _get_html(url):
    if _SESSION is None:
        return None
    try:
        r = _SESSION.get(url, headers=HTML_HEADERS, timeout=TIMEOUT)
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
        r = _SESSION.get(url, headers=HTML_HEADERS, timeout=VALIDATE_TIMEOUT,
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


def resolve_metacritic(title, is_movie=True, candidate=None, curated=False):
    """Return a Metacritic URL that is known (or safely presumed) valid, or ''.

    * curated candidate (Wikidata P1712): trusted -- dropped ONLY on a
      definitive 404/410.
    * guessed candidate (slugged title, e.g. from the BOM calendar service):
      kept ONLY when the page verifiably returns 200.
    * fallback: slug the title and try /movie/ then /tv/ (order depends on
      the title type); again only a verified 200 is accepted.
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
    sections = ("movie", "tv") if is_movie else ("tv", "movie")
    for sec in sections:
        url = "https://www.metacritic.com/%s/%s/" % (sec, slug)
        if candidate and url.rstrip("/") == str(candidate).replace(
                "http://", "https://").rstrip("/"):
            continue  # already tried above
        if _mc_alive(url):
            return "http://www.metacritic.com/%s/%s/" % (sec, slug)
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


def verify_socials(meta):
    """Drop social handles that verifiably no longer exist (deleted, renamed
    or suspended). Checks fail OPEN: a handle is only removed on a definitive
    404 -- bot walls and rate limits never strip a valid account."""
    if not VALIDATE_URLS or not meta:
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


def imdb_suggest_item(title, is_movie=True, year_hint=""):
    """Best-matching item from IMDb's suggestion API (keyless), or None.
    Handles apostrophes/colons and titles that have no release yet.
    Prefers an exact title match of the right type; when the input carried a
    '(year)' disambiguator that exact year wins, else the most recent year
    (these are upcoming titles, so avoid older same-named films)."""
    q = urllib.parse.quote(title.strip().lower())
    data = _get_json(IMDB_SUGGEST.format(q=q), headers=HTML_HEADERS)
    items = [it for it in (data or {}).get("d", [])
             if str(it.get("id", "")).startswith("tt")]
    if not items:
        return None
    tl = _norm(title)
    want_tv = not is_movie
    want_year = int(year_hint) if str(year_hint).isdigit() and len(str(year_hint)) == 4 else None

    def type_ok(it):
        qid = str(it.get("qid") or "").lower()
        if not qid:
            return True
        is_tv_item = qid.startswith("tv") and qid != "tvmovie"
        return is_tv_item == want_tv

    def score(it):
        return (1 if _norm(it.get("l")) == tl else 0,
                1 if (want_year and it.get("y") == want_year) else 0,
                1 if type_ok(it) else 0,
                it.get("y") or 0)

    best = max(items, key=score)
    # only trust it when the title actually matches
    return best if _norm(best.get("l")) == tl else None


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
    for pt in hits:
        base = _norm(re.sub(r"\s*\([^)]*\)\s*$", "", pt))
        if base != tl:
            continue
        qid = _page_qid(pt)
        if tt and qid:
            ent = _entity(qid)
            imdbs = _claim_values((ent or {}).get("claims", {}), "P345") if ent else []
            if imdbs and tt not in imdbs:
                continue  # same name, different film
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
def _tmdb_pick(results, title):
    if not results:
        return None
    tl = title.strip().lower()

    def year(r):
        d = str(r.get("release_date") or r.get("first_air_date") or "")
        return int(d[:4]) if d[:4].isdigit() else 0

    exact = [r for r in results if str(r.get("title") or r.get("name") or "").strip().lower() == tl]
    pool = exact or results
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


def tmdb_lookup(title, is_movie):
    if not TMDB_API_KEY:
        return {}, None
    kind = "movie" if is_movie else "tv"
    search = _get_json(TMDB + "/search/" + kind, {"api_key": TMDB_API_KEY, "query": title})
    if not search:
        return {}, None
    hit = _tmdb_pick(search.get("results", []), title)
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


def omdb_lookup(title):
    if not OMDB_API_KEY:
        return {}
    return _omdb_meta(_get_json(OMDB, {"t": title, "apikey": OMDB_API_KEY}))


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


def wikidata_meta(title, qid=None, is_movie=True):
    entity = _entity(qid) if qid else None
    if entity is None:
        fallback = None
        for cand in _search_candidates(title)[:3]:
            ent = _entity(cand)
            if not ent:
                continue
            fallback = fallback or ent
            if _is_film_or_tv(ent.get("claims", {})):
                entity = ent
                break
        entity = entity or fallback
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
        meta["rottentomatoes"] = "http://www.rottentomatoes.com/" + raw["rottentomatoes"]
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


def fetch_person(name):
    """Auto-discover a PERSON (talent) via Wikidata + IMDb suggestion API.
    Returns: socials, wikipedia_page, imdb_id (nm), gender line, occupation
    labels, sport labels, us_citizen flag. Fails soft ({})."""
    if not name:
        return {}
    clean = re.sub(r"\s*-\s*DAR\s*$", "", name, flags=re.IGNORECASE).strip()
    clean, _ = _split_disambiguator(clean)
    key = ("person:" + clean.lower(),)
    if key in _CACHE:
        return dict(_CACHE[key])
    meta = {}
    try:
        entity = None
        for cand in _search_candidates(clean, limit=6)[:4]:
            ent = _entity(cand)
            if not ent:
                continue
            claims = ent.get("claims", {})
            if "Q5" not in set(_claim_values(claims, "P31")):
                continue  # not a human
            lbl = (ent.get("labels", {}).get("en", {}) or {}).get("value", "")
            if _norm(lbl) == _norm(clean) or any(
                    _norm(a.get("value")) == _norm(clean)
                    for a in ent.get("aliases", {}).get("en", [])):
                entity = ent
                break
            entity = entity or ent  # weak fallback: first human hit
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
            labels = _labels(occ_qids + sport_qids)
            meta["occupations"] = [labels[q] for q in occ_qids if labels.get(q)]
            meta["sports"] = [labels[q] for q in sport_qids if labels.get(q)]
            meta["us_citizen"] = "Q30" in set(_claim_values(claims, "P27"))
        # IMDb nm id fallback via the suggestion API (people come back as nm...)
        if not meta.get("imdb_id"):
            q = urllib.parse.quote(clean.strip().lower())
            data = _get_json(IMDB_SUGGEST.format(q=q), headers=HTML_HEADERS)
            for it in (data or {}).get("d", []):
                if str(it.get("id", "")).startswith("nm") and _norm(it.get("l")) == _norm(clean):
                    meta["imdb_id"] = "https://www.imdb.com/name/" + it["id"]
                    break
    except Exception as e:  # noqa: BLE001
        log.warning("fetch_person failed for %r: %s", name, e)
    verify_socials(meta)
    _CACHE[key] = dict(meta)
    return meta


# ---------------- Video Games ----------------
_GAME_TYPES = {"Q7889", "Q116776512", "Q865493"}  # video game (+ expansions)


def fetch_game(name):
    """Auto-discover a VIDEO GAME via Wikidata: developer (P178), publisher
    (P123), platforms (P400), genres (P136), release (P577), socials,
    wikipedia, metacritic, imdb. Fails soft ({})."""
    if not name:
        return {}
    clean = re.sub(r"\s*-\s*DAR\s*$", "", name, flags=re.IGNORECASE).strip()
    clean, _ = _split_disambiguator(clean)
    key = ("game:" + clean.lower(),)
    if key in _CACHE:
        return dict(_CACHE[key])
    meta = {}
    try:
        entity = None
        fallback = None
        for cand in _search_candidates(clean, limit=8)[:6]:
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


def fetch_brand(name):
    """Auto-discover a BRAND (Beauty / Beverages / Sports / General) via
    Wikidata: social accounts, Wikipedia page, ticker symbol.

    Unlike the film/TV path, candidates are NOT filtered to FILM_TV_TYPES --
    that filter is exactly why brand lookups used to come back empty. Returns
    keys named after the BrandDef columns so create_tfx_row can map them
    straight onto the row. Fails soft ({})."""
    if not name:
        return {}
    clean = re.sub(r"\s*-\s*DAR\s*$", "", name, flags=re.IGNORECASE).strip()
    clean, _ = _split_disambiguator(clean)
    key = ("brand:" + clean.lower(),)
    if key in _CACHE:
        return dict(_CACHE[key])
    meta = {}
    try:
        skip = FILM_TV_TYPES | _GAME_TYPES | _NOT_BRAND_TYPES
        entity = brand_fb = name_fb = None
        for cand in _search_candidates(clean, limit=8)[:6]:
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
    _fill(meta, wikidata_meta(title_hint, qid=(wqid or wikidata_id), is_movie=is_movie))

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
        _fill(meta, wikidata_meta(title_hint, qid=wid, is_movie=is_movie))

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

    # metacritic: verify what we found. A Wikidata URL is curated (kept unless
    # definitively 404); the calendar service's slug guess is only kept when
    # the page really exists; otherwise a verified slug fallback is tried.
    guess = meta.pop("_metacritic_guess", None)
    curated = bool(meta.get("metacritic"))
    mc = resolve_metacritic(title_hint, is_movie,
                            candidate=meta.get("metacritic") or guess,
                            curated=curated)
    if mc:
        meta["metacritic"] = mc
    else:
        meta.pop("metacritic", None)

    # drop social accounts that verifiably no longer exist / are suspended
    verify_socials(meta)
    return meta


def fetch_metadata_by_tt(tt, is_movie=True, title=""):
    """Preferred entry point when the exact IMDb id is known (reliable)."""
    tt = _tt(tt)
    if not tt:
        return {}
    key = ("tt:" + tt, bool(is_movie))
    if key in _CACHE:
        return dict(_CACHE[key])
    meta = {}
    try:
        hint_title, _ = _split_disambiguator(
            re.sub(r"\s*-\s*DAR\s*$", "", title or "", flags=re.IGNORECASE).strip())
        meta = _enrich_by_tt(tt, is_movie, hint_title or tt)
    except Exception as e:  # noqa: BLE001
        log.warning("fetch_metadata_by_tt failed for %s: %s", tt, e)
    _CACHE[key] = dict(meta)
    return meta


def fetch_metadata(title, is_movie=True):
    """Entry point when only the title is known. Resolution order:
    IMDb suggestion API (exact, keyless) > TMDB search > OMDb search."""
    if not title:
        return {}
    clean = re.sub(r"\s*-\s*DAR\s*$", "", title, flags=re.IGNORECASE).strip()
    key = (clean.lower(), bool(is_movie))
    if key in _CACHE:
        return dict(_CACHE[key])

    # a trailing '(2026)' / '(Netflix)' disambiguator is NOT part of the real
    # name -- all lookups use the stripped title; a year hint helps pick the
    # right same-named title
    lookup, hint = _split_disambiguator(clean)
    year_hint = hint if (hint.isdigit() and len(hint) == 4) else ""

    meta = {}
    try:
        # the upcoming-release-movies calendar resolves the tt code by exact
        # title AND supplies distributor/genre/date/scale in one shot
        um = _upcoming_index()["by_title"].get(_norm(lookup)) if is_movie else None
        sug = imdb_suggest_item(lookup, is_movie, year_hint) if not um else None
        tt = _tt((um or {}).get("tt_code")) or (sug or {}).get("id")
        # the IMDb item type gives the TV Program Type (Series / Mini-Series /
        # TV Movie / Special) used by the BrandIngest schema
        sug_ptype = _IMDB_QID_PROGRAM_TYPE.get(str((sug or {}).get("qid") or "").lower())
        tmdb_meta, wid = ({}, None)
        if not tt:
            tmdb_meta, wid = tmdb_lookup(lookup, is_movie)
            tt = _tt(tmdb_meta.get("imdb_id")) or _tt(omdb_lookup(lookup).get("imdb_id"))
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
            _fill(meta, omdb_lookup(lookup))
            mc = resolve_metacritic(lookup, is_movie,
                                    candidate=meta.get("metacritic"),
                                    curated=bool(meta.get("metacritic")))
            if mc:
                meta["metacritic"] = mc
            else:
                meta.pop("metacritic", None)
            verify_socials(meta)
        if sug_ptype:
            meta["program_type"] = sug_ptype  # IMDb's own type beats TMDB's
    except Exception as e:  # noqa: BLE001
        log.warning("fetch_metadata failed for %r: %s", title, e)

    _CACHE[key] = dict(meta)
    return meta


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    t = sys.argv[1] if len(sys.argv) > 1 else "Dune"
    print(json.dumps(fetch_metadata(t, True), indent=2))
