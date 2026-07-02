"""
metadata_fetcher.py
-------------------
Auto-discover title metadata by layering several sources.

AUTHORITATIVE for network / genre / release date (per Ops requirement):
  * Box Office Mojo  -> US "Domestic Distributor" (network) + domestic opening
  * IMDb (JSON-LD)   -> genre, primary_genre, US release date (datePublished);
                        IMDb "Production companies" as a network fallback
FALLBACK / other fields:
  * TMDB  -> imdb id resolution, facebook/instagram/twitter, wikidata_id,
             plus genre/release/network if IMDb+BOM are unavailable
  * Wikidata (by the wikidata_id TMDB returns) -> wikipedia, rottentomatoes,
             metacritic, tiktok, pinterest, youtube
  * OMDb  -> imdb-id resolution / genre / release fallback
  * YouTube Data API -> youtube channel if still missing

API keys are read from ENVIRONMENT VARIABLES (never hard-coded):
  TMDB_API_KEY, OMDB_API_KEY, YOUTUBE_API_KEY, WIKIMEDIA_CONTACT,
  REQUEST_TIMEOUT_SECONDS
Every network call is defensive: on failure it returns what it has, never raises.

NOTE: IMDb / Box Office Mojo are scraped HTML. This can be rate-limited or
blocked from datacenter IPs and may break if their markup changes; all scrape
functions fail soft so the pipeline keeps working via the API fallbacks.
"""

import json
import logging
import os
import re

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
ENTITYDATA = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
IMDB_TITLE = "https://www.imdb.com/title/{tt}/"
BOM_TITLE = "https://www.boxofficemojo.com/title/{tt}/"

HEADERS = {"User-Agent": "ListenFirstTitleTool/1.0 (" + WIKIMEDIA_CONTACT + ")"}
# Realistic browser headers reduce (do not eliminate) IMDb/BOM bot-blocking.
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
NETWORK_PROPS = ["P449", "P750", "P272"]
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
_MONTHS_FULL = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
                "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}


# ---------------- low level ----------------
def _get_json(url, params=None):
    if _SESSION is None:
        return None
    try:
        r = _SESSION.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
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


# ---------------- IMDb scrape ----------------
def imdb_scrape(tt):
    """genre, primary_genre, released_on (US datePublished), network (prod-co)."""
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
    # production companies block -> first company is a network fallback
    block = re.search(r'data-testid="title-details-companies"(.*?)</ul>', html, re.S)
    if block:
        names = re.findall(r'href="/company/[^"]*"[^>]*>([^<]+)</a>', block.group(1))
        if names:
            meta["network"] = names[0].strip()
    return meta


# ---------------- Box Office Mojo scrape ----------------
def _bom_value(html, label):
    m = re.search(re.escape(label) + r"</span>\s*<span[^>]*>(.*?)</span>", html, re.S)
    if not m:
        return None
    return _strip_tags(m.group(1)).strip()


def _parse_long_date(s):
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", s or "")
    if not m:
        return None
    mon, d, y = m.groups()
    mi = _MONTHS_FULL.get(mon.lower())
    return "%s-%02d-%02d" % (y, mi, int(d)) if mi else None


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
    """Pick the best TMDB search hit. Prefer an exact title match, and among
    candidates choose the MOST RECENT release year -- these titles are upcoming,
    so this avoids matching an older same-named film (e.g. a 1979 "Over the Edge")."""
    if not results:
        return None
    tl = title.strip().lower()

    def year(r):
        d = str(r.get("release_date") or r.get("first_air_date") or "")
        return int(d[:4]) if d[:4].isdigit() else 0

    exact = [r for r in results if str(r.get("title") or r.get("name") or "").strip().lower() == tl]
    pool = exact or results
    return max(pool, key=year)


def _tmdb_details_meta(details, kind):
    """Map a TMDB details payload (with external_ids) to our columns + wikidata_id."""
    meta = {}
    genres = [g.get("name") for g in details.get("genres", []) if g.get("name")]
    if genres:
        meta["genre"] = "\n".join(genres)
        meta["primary_genre"] = genres[0]
    rel = details.get("release_date") or details.get("first_air_date")
    if rel:
        meta["released_on"] = rel
    if kind == "tv":
        nets = [n.get("name") for n in details.get("networks", []) if n.get("name")]
        if nets:
            meta["network"] = nets[0]
    if "network" not in meta:
        pcs = [c.get("name") for c in details.get("production_companies", []) if c.get("name")]
        if pcs:
            meta["network"] = pcs[0]
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


def tmdb_lookup(title, is_movie):
    """Resolve by title search (best-effort; unreliable for obscure titles)."""
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
                        {"api_key": TMDB_API_KEY, "append_to_response": "external_ids"})
    if not details:
        return {}, None
    return _tmdb_details_meta(details, kind)


def tmdb_find_by_imdb(tt):
    """Resolve by EXACT imdb id via /find (reliable). Returns (meta, wikidata_id)."""
    if not TMDB_API_KEY or not tt:
        return {}, None
    data = _get_json(TMDB + "/find/" + tt, {"api_key": TMDB_API_KEY, "external_source": "imdb_id"})
    if not data:
        return {}, None
    for key, kind in (("movie_results", "movie"), ("tv_results", "tv")):
        res = data.get(key) or []
        if res:
            details = _get_json(TMDB + "/" + kind + "/" + str(res[0]["id"]),
                                {"api_key": TMDB_API_KEY, "append_to_response": "external_ids"})
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


def omdb_by_id(tt):
    """OMDb keyed by exact imdb id (API -> not IP-blocked)."""
    if not OMDB_API_KEY or not tt:
        return {}
    data = _get_json(OMDB, {"i": tt, "apikey": OMDB_API_KEY})
    return _omdb_meta(data)


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


def _claim_values(claims, prop):
    out = []
    for c in claims.get(prop, []):
        snak = c.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        val = snak.get("datavalue", {}).get("value")
        if isinstance(val, dict) and "id" in val:
            out.append(val["id"])
        elif val is not None:
            out.append(val)
    return out


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


def wikidata_meta(title, qid=None):
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
    yt = None
    if raw.get("youtube_handle"):
        yt = "http://www.youtube.com/@" + raw["youtube_handle"].lstrip("@")
    elif raw.get("youtube_id"):
        yt = "http://www.youtube.com/channel/" + raw["youtube_id"]
    if yt:
        meta["youtube_channel_company"] = yt
        meta["youtube_channel_username"] = yt + "|" + title.lower()
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
        for v in _claim_values(claims, p):
            d = _parse_time(v)
            if d:
                meta["released_on"] = d
                got = True
                break
        if got:
            break
    net_qid = None
    for p in NETWORK_PROPS:
        v = _claim_values(claims, p)
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
    if not YOUTUBE_API_KEY:
        return {}
    data = _get_json(YT_SEARCH, {"part": "snippet", "type": "channel", "maxResults": 1,
                                 "q": title, "key": YOUTUBE_API_KEY})
    items = (data or {}).get("items", [])
    if not items:
        return {}
    snip = items[0].get("snippet", {})
    cid = snip.get("channelId") or items[0].get("id", {}).get("channelId")
    if not cid:
        return {}
    url = "http://www.youtube.com/channel/" + cid
    return {"youtube_channel_company": url, "youtube_channel_username": url + "|" + title.lower()}


# ---------------- merge / entry ----------------
def _fill(dst, src):
    for k, v in (src or {}).items():
        if v not in (None, "") and dst.get(k) in (None, ""):
            dst[k] = v


_CACHE = {}


def _enrich_by_tt(tt, is_movie, title_hint, wikidata_id=None):
    """Merge all sources keyed off an exact IMDb tt. Priority:
    IMDb/BOM (authoritative for network/genre/release) > OMDb-by-id > TMDB-find > Wikidata."""
    meta = {}
    # IMDb + BOM (authoritative) -- may be blocked from datacenter IPs; fails soft
    _fill(meta, bom_scrape(tt))        # US Domestic Distributor -> network (+opening)
    _fill(meta, imdb_scrape(tt))       # genre + US release date (+ prod-co network)
    # OMDb by id (reliable API) -> genre / release / imdb fallback
    _fill(meta, omdb_by_id(tt))
    # TMDB find by exact id -> socials, network fallback, wikidata_id
    tmeta, wid = tmdb_find_by_imdb(tt)
    wikidata_id = wikidata_id or wid
    _fill(meta, tmeta)
    # Wikidata -> wikipedia / rottentomatoes / metacritic / tiktok / youtube / pinterest
    _fill(meta, wikidata_meta(title_hint, qid=wikidata_id))
    if not meta.get("youtube_channel_company"):
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
    """Entry point when only the title is known. Resolves an IMDb id first
    (via TMDB search / OMDb) then enriches by that id. For obscure/pre-release
    titles the id may not resolve -- pass the imdb_id via fetch_metadata_by_tt
    for reliable results."""
    if not title:
        return {}
    clean = re.sub(r"\s*-\s*DAR\s*$", "", title, flags=re.IGNORECASE).strip()
    key = (clean.lower(), bool(is_movie))
    if key in _CACHE:
        return dict(_CACHE[key])

    meta = {}
    try:
        tmdb_meta, wid = tmdb_lookup(clean, is_movie)
        tt = _tt(tmdb_meta.get("imdb_id"))
        if not tt:
            tt = _tt(omdb_lookup(clean).get("imdb_id"))
        if tt:
            meta = _enrich_by_tt(tt, is_movie, clean, wikidata_id=wid)
            _fill(meta, tmdb_meta)  # socials etc. from the title match
        else:
            # could not resolve an id -> best effort from title-based sources
            _fill(meta, tmdb_meta)
            _fill(meta, wikidata_meta(clean, qid=wid))
            _fill(meta, omdb_lookup(clean))
    except Exception as e:  # noqa: BLE001
        log.warning("fetch_metadata failed for %r: %s", title, e)

    _CACHE[key] = dict(meta)
    return meta


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    t = sys.argv[1] if len(sys.argv) > 1 else "Dune"
    print(json.dumps(fetch_metadata(t, True), indent=2))
