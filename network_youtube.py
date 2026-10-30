"""
network_youtube.py
------------------
Finds the OFFICIAL YouTube channel of a title's NETWORK / STUDIO / PUBLISHER
live, instead of reading it from the static "YouTube" column of the Ops ingest
templates (reference/*.xlsx).

Why: the template column is a hand-kept snapshot. ~70 film studios and ~30 TV
networks have no channel there at all, some entries are stale or point at a
sister label (e.g. Magnet Releasing -> Magnolia), and any network that is not
in the template gets no channel. The rule now is simply: once a network is
found for the title, add THAT network's (or studio's) own channel.

Resolution order (first hit wins):
  1. Wikidata entity of the network
       a. the title's own distributor / original-network / publisher QID
          (P750 / P449 / P123) when its label names the same network, else
       b. a name search, keeping only organisations whose label or alias is the
          network name (generic words like "Pictures" / "Studios" ignored)
     -> P11245 YouTube handle, else P2397 YouTube channel ID (preferred rank
        first, deprecated values skipped).
  2. YouTube Data API channel search on the network name (only when
     YOUTUBE_API_KEY is set): the channel title must be the network name, fan /
     clips / reaction channels are rejected, the biggest channel wins and it
     needs at least NETWORK_YT_MIN_SUBS subscribers.
  Whenever a YouTube key is set, a channel found in step 1 is also confirmed
  to still exist (channels.list, 1 quota unit) - a terminated channel is
  skipped.
  3. Only if 1-2 find nothing: the ingest-template / inline value the caller
     passes as ``fallback`` (switch off with NETWORK_YT_TEMPLATE_FALLBACK=0).

Environment:
  NETWORK_YT_LIVE=0               -> old behaviour (template value only)
  NETWORK_YT_TEMPLATE_FALLBACK=0  -> never use the template value
  NETWORK_YT_MIN_SUBS             -> API-search subscriber floor (default 1000)

Results are cached for the process lifetime, so a batch with 50 Warner Bros.
titles costs one lookup. Everything fails soft: any error -> fallback.
"""

import logging
import os
import re
import threading

log = logging.getLogger(__name__)


def _flag(name, default):
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off")


def live_enabled():
    return _flag("NETWORK_YT_LIVE", "1")


def template_fallback_enabled():
    return _flag("NETWORK_YT_TEMPLATE_FALLBACK", "1")


try:
    MIN_SUBS = int(os.getenv("NETWORK_YT_MIN_SUBS", "1000"))
except ValueError:
    MIN_SUBS = 1000

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
YT_SEARCH = "https://www.googleapis.com/youtube/v3/search"
YT_CHANNELS = "https://www.googleapis.com/youtube/v3/channels"

# Wikidata classes that are never a network/studio (a film, show, game or
# person can share the studio's name and carry its own channel)
_EXCLUDED_P31 = {
    "Q5",          # human
    "Q11424",      # film
    "Q5398426",    # television series
    "Q7889",       # video game
    "Q24856",      # film series
    "Q196600",     # media franchise
    "Q482994",     # album
    "Q134556",     # single
    "Q7725634",    # literary work
}

# words that do not distinguish one studio from another
_GENERIC = {
    "the", "pictures", "picture", "studios", "studio", "films", "film",
    "entertainment", "releasing", "release", "distribution", "distributors",
    "media", "network", "networks", "channel", "tv", "television", "inc",
    "llc", "ltd", "limited", "corporation", "corp", "company", "co", "group",
    "movies", "official", "productions", "production", "worldwide", "global",
    "usa", "us", "america", "games", "interactive", "presents", "and",
}

# channel names that are never the company's own channel
_FAN_WORDS = re.compile(
    r"\b(fan|fans|fanmade|unofficial|clips?|reactions?|tribute|parody|"
    r"compilation|edits?|archive|reupload)\b", re.I)

_YT_URL_RE = re.compile(
    r"youtube\.com/(?:(@[\w.\-]+)|channel/(UC[\w\-]{20,})|user/([\w.\-]+)|c/([\w.\-]+))",
    re.I)

_CACHE = {}
_LOCK = threading.Lock()


# --------------------------------------------------------------- helpers
def _fetch():
    """metadata_fetcher._get_json (shared session, timeouts, YouTube
    rate-limit breaker) and the API key; imported lazily."""
    import metadata_fetcher as mf
    return mf._get_json, (mf.YOUTUBE_API_KEY or "")


def _tokens(name):
    s = re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower().replace("&", " and "))
    return [t for t in s.split() if t]


def _core(name):
    """Distinguishing words of a company name ('Warner Bros. Pictures' ->
    ('bros', 'warner'))."""
    toks = [t for t in _tokens(name) if t not in _GENERIC]
    return tuple(sorted(toks)) if toks else tuple(sorted(_tokens(name)))


def names_match(a, b):
    ca, cb = _core(a), _core(b)
    return bool(ca) and ca == cb


def query_names(network, raw="", company=""):
    """Names to look the network up under, best first: the raw distributor
    spelling the sources gave, the database label, the parent company, then the
    first half of a combined label ('Lionsgate / Summit' -> 'Lionsgate')."""
    out = []

    def add(n):
        n = str(n or "").strip()
        if n and not n.startswith("\u00ab") and n.lower() != "unknown" \
                and n.lower() not in [o.lower() for o in out]:
            out.append(n)

    add(raw)
    add(network)
    add(company)
    for n in (network, raw):
        if "/" in str(n or ""):
            add(str(n).split("/")[0])
    return out


def _url(scheme, handle=None, channel_id=None, user=None):
    base = f"{scheme}://www.youtube.com/"
    if handle:
        return base + "@" + handle.lstrip("@")
    if channel_id:
        return base + "channel/" + channel_id
    if user:
        return base + "user/" + user
    return ""


def _with_scheme(url, scheme):
    return re.sub(r"^https?://", scheme + "://", str(url or "").strip())


# --------------------------------------------------------------- Wikidata
def _ranked(claims, prop):
    vals = []
    for c in claims.get(prop, []) or []:
        rank = c.get("rank", "normal")
        if rank == "deprecated":
            continue
        dv = (c.get("mainsnak", {}) or {}).get("datavalue", {}) or {}
        v = dv.get("value")
        if isinstance(v, dict):
            v = v.get("id")
        if v:
            vals.append((0 if rank == "preferred" else 1, str(v)))
    return [v for _, v in sorted(vals, key=lambda x: x[0])]


def _entity_names(ent):
    names = []
    lbl = (ent.get("labels", {}) or {}).get("en", {}) or {}
    if lbl.get("value"):
        names.append(lbl["value"])
    for a in (ent.get("aliases", {}) or {}).get("en", []) or []:
        if a.get("value"):
            names.append(a["value"])
    return names


def _entity_channels(ent, scheme):
    """[(url, kind, value)] for an entity: handles first, then channel ids."""
    claims = ent.get("claims", {}) or {}
    out = []
    for h in _ranked(claims, "P11245"):
        out.append((_url(scheme, handle=h), "handle", h.lstrip("@")))
    for cid in _ranked(claims, "P2397"):
        out.append((_url(scheme, channel_id=cid), "id", cid))
    return out


def _is_org(ent):
    p31 = set(_ranked(ent.get("claims", {}) or {}, "P31"))
    return not (p31 & _EXCLUDED_P31)


def _wd_entities(get_json, qids):
    if not qids:
        return {}
    data = get_json(WIKIDATA_API, {
        "action": "wbgetentities", "ids": "|".join(qids[:20]),
        "props": "labels|aliases|claims", "languages": "en", "format": "json"})
    return (data or {}).get("entities", {}) or {}


def _wd_search(get_json, name, limit=7):
    data = get_json(WIKIDATA_API, {
        "action": "wbsearchentities", "search": name, "language": "en",
        "format": "json", "type": "item", "limit": limit})
    return [c["id"] for c in (data or {}).get("search", []) or [] if c.get("id")]


# --------------------------------------------------------------- YouTube API
def _yt_alive(get_json, key, kind, value):
    """True / False when the API answered, None when it could not be asked."""
    if not key:
        return None
    params = {"part": "id", "key": key}
    if kind == "handle":
        params["forHandle"] = "@" + value.lstrip("@")
    else:
        params["id"] = value
    data = get_json(YT_CHANNELS, params)
    if data is None:
        return None
    return bool(data.get("items"))


def _yt_search(get_json, key, names, scheme):
    if not key:
        return ""
    for name in names[:2]:              # search.list costs 100 quota units
        data = get_json(YT_SEARCH, {"part": "snippet", "type": "channel",
                                    "maxResults": 6, "q": name, "key": key})
        ids = []
        for item in (data or {}).get("items", []) or []:
            snip = item.get("snippet", {}) or {}
            cid = snip.get("channelId") or (item.get("id", {}) or {}).get("channelId")
            title = snip.get("title") or ""
            if cid and names_match(title, name) and not _FAN_WORDS.search(title):
                ids.append(cid)
        if not ids:
            continue
        det = get_json(YT_CHANNELS, {"part": "snippet,statistics",
                                     "id": ",".join(ids), "key": key})
        best, best_subs = "", -1
        for item in (det or {}).get("items", []) or []:
            stats = item.get("statistics", {}) or {}
            try:
                subs = int(stats.get("subscriberCount") or 0)
            except (TypeError, ValueError):
                subs = 0
            if subs >= MIN_SUBS and subs > best_subs:
                best, best_subs = item.get("id"), subs
        if best:
            return _url(scheme, channel_id=best)
    return ""


# --------------------------------------------------------------- public API
def resolve(network, raw="", company="", qid="", scheme="http"):
    """The network's own YouTube channel URL, or '' when none can be found.
    Never raises."""
    names = query_names(network, raw, company)
    if not names:
        return ""
    ck = (tuple(n.lower() for n in names), str(qid or ""), scheme)
    with _LOCK:
        if ck in _CACHE:
            return _CACHE[ck]
    try:
        url = _resolve(names, qid, scheme)
    except Exception as e:  # noqa: BLE001 - fail soft
        log.warning("network_youtube: lookup failed for %r (%s)", network, e)
        url = ""
    with _LOCK:
        _CACHE[ck] = url
    if url:
        log.info("network_youtube: %r -> %s", network, url)
    return url


def _resolve(names, qid, scheme):
    get_json, key = _fetch()

    def first_alive(ent):
        for url, kind, value in _entity_channels(ent, scheme):
            alive = _yt_alive(get_json, key, kind, value)
            if alive is False:
                log.info("network_youtube: %s no longer exists - skipped", url)
                continue
            return url
        return ""

    # 1a. the title's own distributor / network / publisher entity
    if qid:
        ent = _wd_entities(get_json, [qid]).get(qid)
        if ent and any(names_match(n, q) for n in _entity_names(ent) for q in names):
            url = first_alive(ent)
            if url:
                return url
    # 1b. name search on Wikidata
    for name in names:
        qids = [q for q in _wd_search(get_json, name) if q != qid]
        ents = _wd_entities(get_json, qids)
        for q in qids:                      # keep Wikidata's relevance order
            ent = ents.get(q)
            if not ent or not _is_org(ent):
                continue
            if not any(names_match(n, name) for n in _entity_names(ent)):
                continue
            url = first_alive(ent)
            if url:
                return url
    # 2. YouTube Data API channel search
    return _yt_search(get_json, key, names, scheme)


def channel_for(network, raw="", company="", qid="", fallback="", scheme="http"):
    """What the generators call. Live channel first; the template value only
    when the live lookup finds nothing (and the fallback is enabled)."""
    fallback = _with_scheme(fallback, scheme) if fallback else ""
    if not live_enabled():
        return fallback
    live = resolve(network, raw=raw, company=company, qid=qid, scheme=scheme)
    if live:
        return live
    return fallback if template_fallback_enabled() else ""


def clear_cache():
    with _LOCK:
        _CACHE.clear()
