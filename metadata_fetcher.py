"""
metadata_fetcher.py
-------------------
Auto-discover metadata for a movie / TV title using free, public Wikidata APIs
(no API key). Everything is defensive: on any failure the functions return
empty results so the main app never breaks.

Discovers: network (distributor / broadcaster / production company),
release date, genre + primary genre, IMDb / Rotten Tomatoes / Metacritic IDs,
Wikipedia page, and official Twitter / Instagram / Facebook / YouTube / TikTok /
Pinterest / Threads / LinkedIn accounts.
"""

import logging
import re

try:
    import requests
    _SESSION = requests.Session()
except Exception:
    requests = None
    _SESSION = None

log = logging.getLogger(__name__)

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
ENTITYDATA = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
HEADERS = {
    "User-Agent": "ListenFirstTitleTool/1.0 (https://listenfirstmedia.com; contact@listenfirstmedia.com)"
}
TIMEOUT = 12

FILM_TV_TYPES = {
    "Q11424", "Q202866", "Q24856", "Q93204", "Q506240", "Q5398426",
    "Q1259759", "Q15416", "Q21191270", "Q3464665", "Q580850", "Q1054574",
    "Q7725310", "Q1261214", "Q1366112",
}

# Wikidata property -> internal key (external IDs + socials)
PROPERTY_MAP = {
    "P345": "imdb",
    "P1258": "rottentomatoes",
    "P1712": "metacritic",
    "P2002": "twitter",
    "P2003": "instagram",
    "P2013": "facebook",
    "P2397": "youtube_id",
    "P11245": "youtube_handle",
    "P7085": "tiktok",
    "P4264": "linkedin_co",
    "P3836": "pinterest",
    "P11892": "threads",
}

# Properties whose first available value (an item QID) is used as "network"
NETWORK_PROPS = ["P449", "P750", "P272"]  # broadcaster, distributor, production company


def _get_json(url, params=None):
    if _SESSION is None:
        return None
    try:
        r = _SESSION.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("Wikidata request failed (%s): %s", url, e)
        return None


def _search_candidates(title, limit=6):
    data = _get_json(WIKIDATA_API, {
        "action": "wbsearchentities", "search": title, "language": "en",
        "format": "json", "type": "item", "limit": limit,
    })
    if not data:
        return []
    return [c["id"] for c in data.get("search", []) if "id" in c]


def _entity(qid):
    data = _get_json(ENTITYDATA.format(qid=qid))
    if not data:
        return None
    return data.get("entities", {}).get(qid)


def _labels(qids):
    """Resolve a list of item QIDs to their English labels in one request."""
    qids = [q for q in qids if q]
    if not qids:
        return {}
    data = _get_json(WIKIDATA_API, {
        "action": "wbgetentities", "ids": "|".join(qids[:50]),
        "props": "labels", "languages": "en", "format": "json",
    })
    out = {}
    if not data:
        return out
    for qid, ent in data.get("entities", {}).items():
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
    """Wikidata time value dict -> 'YYYY-MM-DD' (best effort)."""
    if not isinstance(val, dict):
        return None
    m = re.match(r"[+-]?(\d{4})-(\d{2})-(\d{2})", val.get("time", ""))
    if not m:
        return None
    y, mo, d = m.groups()
    mo = "01" if mo == "00" else mo
    d = "01" if d == "00" else d
    return f"{y}-{mo}-{d}"


def _is_film_or_tv(claims):
    return bool(set(_claim_values(claims, "P31")) & FILM_TV_TYPES)


def resolve_entity(title):
    """Return (qid, entity) for the best film/TV match, else (None, None)."""
    fallback = None
    for qid in _search_candidates(title):
        ent = _entity(qid)
        if not ent:
            continue
        if fallback is None:
            fallback = (qid, ent)
        if _is_film_or_tv(ent.get("claims", {})):
            return qid, ent
    return fallback if fallback else (None, None)


def build_metadata(title, entity):
    """Map a Wikidata entity to export columns."""
    claims = entity.get("claims", {})
    raw = {}
    for prop, key in PROPERTY_MAP.items():
        vals = _claim_values(claims, prop)
        if vals:
            raw[key] = vals[0]

    meta = {}

    # External DB IDs (formatted like the Test_Run export)
    if "imdb" in raw:
        meta["imdb_id"] = f"http://www.imdb.com/title/{raw['imdb']}"
    if "rottentomatoes" in raw:
        meta["rottentomatoes"] = f"http://www.rottentomatoes.com/{raw['rottentomatoes']}"
    if "metacritic" in raw:
        meta["metacritic"] = f"http://www.metacritic.com/{raw['metacritic'].strip('/')}/"

    # YouTube: prefer handle, else channel ID
    yt_url = None
    if raw.get("youtube_handle"):
        yt_url = f"http://www.youtube.com/@{raw['youtube_handle'].lstrip('@')}"
    elif raw.get("youtube_id"):
        yt_url = f"http://www.youtube.com/channel/{raw['youtube_id']}"
    if yt_url:
        meta["youtube_channel_company"] = yt_url
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

    # Release date: publication date (P577), else start time (P580)
    for p in ("P577", "P580"):
        for v in _claim_values(claims, p):
            d = _parse_time(v)
            if d:
                meta["released_on"] = d
                break
        if "released_on" in meta:
            break

    # --- Item-reference fields that need label lookups (network + genre) ---
    to_label = []

    network_qid = None
    for p in NETWORK_PROPS:
        vals = _claim_values(claims, p)
        if vals:
            network_qid = vals[0]
            break
    if network_qid:
        to_label.append(network_qid)

    genre_qids = _claim_values(claims, "P136")  # genre
    to_label.extend(genre_qids)

    labels = _labels(to_label) if to_label else {}

    if network_qid and labels.get(network_qid):
        meta["network"] = labels[network_qid]

    genre_names = [labels[q] for q in genre_qids if labels.get(q)]
    if genre_names:
        meta["genre"] = "\n".join(genre_names)
        meta["primary_genre"] = genre_names[0]

    # Wikipedia page
    enwiki = entity.get("sitelinks", {}).get("enwiki")
    if enwiki and enwiki.get("title"):
        meta["wikipedia_page"] = "http://en.wikipedia.org/wiki/" + enwiki["title"].replace(" ", "_")

    return meta


_CACHE = {}


def fetch_metadata(title):
    """title -> dict of discovered column values. Always returns a dict; never raises."""
    if not title:
        return {}
    key = title.strip().lower()
    if key in _CACHE:
        return dict(_CACHE[key])
    result = {}
    try:
        clean = re.sub(r"\s*-\s*DAR\s*$", "", title, flags=re.IGNORECASE).strip()
        qid, entity = resolve_entity(clean)
        if entity:
            result = build_metadata(clean, entity)
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
