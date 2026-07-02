"""
metadata_fetcher.py
-------------------
Auto-discover social-media handles and external IDs (IMDb, Rotten Tomatoes,
Metacritic, Wikipedia) for a movie / TV title using free, public Wikidata APIs.

No API key required. Every network call is defensive: on any failure the
functions return empty results so the main app never breaks.

Design:
  1. wbsearchentities  -> candidate Wikidata items for the title string
  2. Special:EntityData/<QID>.json -> the item's claims + sitelinks
  3. Pick the best candidate whose "instance of" (P31) is a film / TV type
  4. Map Wikidata properties -> the export's columns, formatting URLs to match
     the style seen in the Test_Run export.

The property -> column mapping and URL formats are collected in one place
(see PROPERTY_MAP / build_metadata) so they are easy to adjust.
"""

import logging
import re

try:
    import requests
    _SESSION = requests.Session()
except Exception:  # requests should be installed, but never hard-fail on import
    requests = None
    _SESSION = None

log = logging.getLogger(__name__)

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
ENTITYDATA = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
HEADERS = {
    "User-Agent": "ListenFirstTitleTool/1.0 (https://listenfirstmedia.com; contact@listenfirstmedia.com)"
}
TIMEOUT = 12

# Wikidata QIDs that count as a film / TV title (used to disambiguate search hits)
FILM_TV_TYPES = {
    "Q11424",    # film
    "Q202866",   # animated film
    "Q24856",    # film series
    "Q93204",    # documentary film
    "Q506240",   # television film
    "Q5398426",  # television series
    "Q1259759",  # miniseries
    "Q15416",    # television program
    "Q21191270", # television series season
    "Q3464665",  # television series episode
    "Q580850",   # anthology series
    "Q1054574",  # limited series
    "Q1667921",  # novel series (occasionally used) -- harmless
    "Q7725310",  # series of creative works
}

# Wikidata property -> internal key
PROPERTY_MAP = {
    "P345": "imdb",            # IMDb ID (tt/nm...)
    "P1258": "rottentomatoes", # Rotten Tomatoes ID (m/...)
    "P1712": "metacritic",     # Metacritic ID (movie/...)
    "P2002": "twitter",        # X/Twitter username
    "P2003": "instagram",      # Instagram username
    "P2013": "facebook",       # Facebook ID
    "P2397": "youtube_id",     # YouTube channel ID (UC...)
    "P11245": "youtube_handle",# YouTube handle (@...)
    "P7085": "tiktok",         # TikTok username
    "P4264": "linkedin_co",    # LinkedIn company ID
    "P3836": "pinterest",      # Pinterest username
    "P11892": "threads",       # Threads username
}


def _get_json(url, params=None):
    if _SESSION is None:
        return None
    try:
        r = _SESSION.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001 - never propagate network errors
        log.warning("Wikidata request failed (%s): %s", url, e)
        return None


def _search_candidates(title, limit=6):
    data = _get_json(WIKIDATA_API, {
        "action": "wbsearchentities",
        "search": title,
        "language": "en",
        "format": "json",
        "type": "item",
        "limit": limit,
    })
    if not data:
        return []
    return [c["id"] for c in data.get("search", []) if "id" in c]


def _entity(qid):
    data = _get_json(ENTITYDATA.format(qid=qid))
    if not data:
        return None
    return data.get("entities", {}).get(qid)


def _claim_values(claims, prop):
    """Return the list of simple values for a property's claims."""
    out = []
    for c in claims.get(prop, []):
        snak = c.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        val = snak.get("datavalue", {}).get("value")
        if isinstance(val, dict) and "id" in val:      # entity reference (e.g. P31)
            out.append(val["id"])
        elif val is not None:
            out.append(val)
    return out


def _is_film_or_tv(claims):
    types = set(_claim_values(claims, "P31"))
    return bool(types & FILM_TV_TYPES)


def resolve_entity(title):
    """Return (qid, entity_dict) for the best film/TV match, or (None, None)."""
    candidates = _search_candidates(title)
    fallback = None
    for qid in candidates:
        ent = _entity(qid)
        if not ent:
            continue
        if fallback is None:
            fallback = (qid, ent)
        if _is_film_or_tv(ent.get("claims", {})):
            return qid, ent
    return fallback if fallback else (None, None)


def build_metadata(title, entity):
    """Map a Wikidata entity to export columns. Returns a dict of column->value.

    URL formats mirror the style seen in the Test_Run export:
      imdb_id        -> http://www.imdb.com/title/<ttID>
      rottentomatoes -> http://www.rottentomatoes.com/<id>
      metacritic     -> http://www.metacritic.com/<id>/
      youtube_*      -> http://www.youtube.com/@<handle>  or  /channel/<UC...>
    Handle-style fields (twitter/instagram/tiktok/pinterest) store the raw
    username; *_page fields store full URLs.
    """
    claims = entity.get("claims", {})
    raw = {}
    for prop, key in PROPERTY_MAP.items():
        vals = _claim_values(claims, prop)
        if vals:
            raw[key] = vals[0]

    meta = {}

    # External review / DB IDs
    if "imdb" in raw:
        meta["imdb_id"] = f"http://www.imdb.com/title/{raw['imdb']}"
    if "rottentomatoes" in raw:
        meta["rottentomatoes"] = f"http://www.rottentomatoes.com/{raw['rottentomatoes']}"
    if "metacritic" in raw:
        mc = raw["metacritic"].strip("/")
        meta["metacritic"] = f"http://www.metacritic.com/{mc}/"

    # YouTube: prefer handle, else channel ID
    yt_url = None
    if raw.get("youtube_handle"):
        h = raw["youtube_handle"].lstrip("@")
        yt_url = f"http://www.youtube.com/@{h}"
    elif raw.get("youtube_id"):
        yt_url = f"http://www.youtube.com/channel/{raw['youtube_id']}"
    if yt_url:
        meta["youtube_channel_company"] = yt_url
        # export pairs the channel URL with the title in the username column
        meta["youtube_channel_username"] = f"{yt_url}|{title.lower()}"

    # Social handles / pages
    if "twitter" in raw:
        meta["twitter_handle"] = raw["twitter"]
    if "instagram" in raw:
        meta["instagram_user"] = raw["instagram"]
    if "tiktok" in raw:
        meta["tiktok_user"] = raw["tiktok"].lstrip("@")
    if "pinterest" in raw:
        meta["pinterest_user_username"] = raw["pinterest"]
    if "threads" in raw:
        meta["threads_page"] = f"http://www.threads.net/@{raw['threads'].lstrip('@')}"
    if "facebook" in raw:
        meta["facebook_page"] = f"http://www.facebook.com/{raw['facebook']}"
    if "linkedin_co" in raw:
        meta["linkedin_page"] = f"http://www.linkedin.com/company/{raw['linkedin_co']}"

    # Wikipedia page from sitelinks
    sitelinks = entity.get("sitelinks", {})
    enwiki = sitelinks.get("enwiki")
    if enwiki and enwiki.get("title"):
        slug = enwiki["title"].replace(" ", "_")
        meta["wikipedia_page"] = f"http://en.wikipedia.org/wiki/{slug}"

    return meta


# simple in-process cache to avoid re-querying the same title
_CACHE = {}


def fetch_metadata(title):
    """Public entry point: title string -> dict of discovered column values.

    Always returns a dict (possibly empty). Never raises.
    """
    if not title:
        return {}
    key = title.strip().lower()
    if key in _CACHE:
        return dict(_CACHE[key])
    result = {}
    try:
        # strip a trailing " - DAR" so DAR variants resolve to the real title
        clean = re.sub(r"\s*-\s*DAR\s*$", "", title, flags=re.IGNORECASE).strip()
        qid, entity = resolve_entity(clean)
        if entity:
            result = build_metadata(clean, entity)
            result["_wikidata_qid"] = qid  # informational; harmless extra key
    except Exception as e:  # noqa: BLE001
        log.warning("fetch_metadata failed for %r: %s", title, e)
        result = {}
    _CACHE[key] = dict(result)
    return result


if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO)
    t = sys.argv[1] if len(sys.argv) > 1 else "Stranger Things"
    print(json.dumps(fetch_metadata(t), indent=2))
