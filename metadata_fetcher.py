"""
metadata_fetcher.py (v2)
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


def _tt(imdb_url_or_id):
    m = re.search(r"tt\d{5,}", str(imdb_url_or_id or ""))
    return m.group(0) if m else None


def _strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "")


def _norm(s):
    """lowercase, letters+digits only — for title comparisons."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# ---------------- upcoming-release-movies service ----------------
_UPCOMING = {"fetched": 0.0, "by_title": {}, "by_tt": {}}
_UPCOMING_TTL = 6 * 3600  # refresh the calendar index every 6h


def _upcoming_index():
    """Cached index of the upcoming-release-movies service, keyed by
    normalized title and by tt code. Fails soft (empty index) so the
    pipeline keeps working via the other sources."""
    now = time.time()
    if _UPCOMING["by_title"] and now - _UPCOMING["fetched"] < _UPCOMING_TTL:
        return _UPCOMING
    if _SESSION is None:
        return _UPCOMING
    try:
        start = (date.today() - timedelta(days=180)).isoformat()
        end = (date.today() + timedelta(days=730)).isoformat()
        r = _SESSION.get(UPCOMING_API,
                         params={"start_date": start, "end_date": end},
                         headers=HEADERS, timeout=UPCOMING_TIMEOUT)
        r.raise_for_status()
        movies = (r.json() or {}).get("movies") or []
    except Exception as e:  # noqa: BLE001
        log.warning("upcoming-release-movies fetch failed: %s", e)
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
        meta["metacritic"] = str(m["metacritic_url"]).replace("https://", "http://")
    return meta


# ---------------- IMDb suggestion API (keyless, reliable) ----------------
# IMDb item type -> LF Program Type (for the TV BrandIngest schema)
_IMDB_QID_PROGRAM_TYPE = {
    "tvseries": "Series", "tvminiseries": "Mini-Series",
    "tvmovie": "TV Movie", "tvspecial": "Special", "tvshort": "Special",
}


def imdb_suggest_item(title, is_movie=True):
    """Best-matching item from IMDb's suggestion API (keyless), or None.
    Handles apostrophes/colons and titles that have no release yet.
    Prefers an exact title match of the right type, then the most recent year
    (these are upcoming titles, so avoid older same-named films)."""
    q = urllib.parse.quote(title.strip().lower())
    data = _get_json(IMDB_SUGGEST.format(q=q), headers=HTML_HEADERS)
    items = [it for it in (data or {}).get("d", [])
             if str(it.get("id", "")).startswith("tt")]
    if not items:
        return None
    tl = _norm(title)
    want_tv = not is_movie

    def type_ok(it):
        qid = str(it.get("qid") or "").lower()
        if not qid:
            return True
        is_tv_item = qid.startswith("tv") and qid != "tvmovie"
        return is_tv_item == want_tv

    def score(it):
        return (1 if _norm(it.get("l")) == tl else 0,
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


def _claim_values(claims, prop, prefer_us=False):
    """Values for a property. With prefer_us=True, claims qualified with
    'place of publication'/'applies to' = United States (Q30) come first."""
    vals, us_vals = [], []
    for c in claims.get(prop, []):
        snak = c.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        val = snak.get("datavalue", {}).get("value")
        if isinstance(val, dict) and "id" in val:
            val = val["id"]
        if val is None:
            continue
        is_us = False
        for qprop in ("P291", "P518", "P3005", "P1001"):
            for q in (c.get("qualifiers", {}) or {}).get(qprop, []):
                qv = (q.get("datavalue", {}) or {}).get("value")
                if isinstance(qv, dict) and qv.get("id") == US_QID:
                    is_us = True
        (us_vals if is_us else vals).append(val)
    return us_vals + vals if prefer_us else vals + us_vals


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
    gnames = [labels[q] for q in genre_qids if labels.get(q)]
    if gnames:
        meta["genre"] = "\n".join(gnames)
        meta["primary_genre"] = gnames[0]
    enwiki = entity.get("sitelinks", {}).get("enwiki")
    if enwiki and enwiki.get("title"):
        meta["wikipedia_page"] = "http://en.wikipedia.org/wiki/" + enwiki["title"].replace(" ", "_")
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
        meta = _enrich_by_tt(tt, is_movie, title or tt)
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

    meta = {}
    try:
        # the upcoming-release-movies calendar resolves the tt code by exact
        # title AND supplies distributor/genre/date/scale in one shot
        um = _upcoming_index()["by_title"].get(_norm(clean)) if is_movie else None
        sug = imdb_suggest_item(clean, is_movie) if not um else None
        tt = _tt((um or {}).get("tt_code")) or (sug or {}).get("id")
        # the IMDb item type gives the TV Program Type (Series / Mini-Series /
        # TV Movie / Special) used by the BrandIngest schema
        sug_ptype = _IMDB_QID_PROGRAM_TYPE.get(str((sug or {}).get("qid") or "").lower())
        tmdb_meta, wid = ({}, None)
        if not tt:
            tmdb_meta, wid = tmdb_lookup(clean, is_movie)
            tt = _tt(tmdb_meta.get("imdb_id")) or _tt(omdb_lookup(clean).get("imdb_id"))
        if tt:
            meta = _enrich_by_tt(tt, is_movie, clean, wikidata_id=wid)
            tmdb_meta.pop("production_company", None)
            tmdb_meta.pop("released_on_us", None)
            _fill(meta, tmdb_meta)
        else:
            tmdb_meta.pop("production_company", None)
            tmdb_meta.pop("released_on_us", None)
            _fill(meta, tmdb_meta)
            _fill(meta, wikidata_meta(clean, qid=wid, is_movie=is_movie))
            _fill(meta, omdb_lookup(clean))
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
