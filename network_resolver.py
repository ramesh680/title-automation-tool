"""
network_resolver.py
-------------------
Map a raw distributor / network name, as the sources spell it, onto the
label that exists in OUR database (the Ops ingest templates in reference/).

Why: Box Office Mojo, Wikipedia, Rotten Tomatoes and TMDB all spell studios
their own way -- "Paramount Pictures", "Paramount Pictures Releasing",
"Focus Features LLC" -- while the ingest database only knows "Paramount",
"Focus Features", ... Shipping the raw spelling creates a network that does
not exist in the database (the 'The Rescue' -> "Paramount Pictures" bug).

Resolution order (first hit wins):
  1. exact (case-insensitive) match against the database list
  2. curated alias table (ALIASES below, plus the app's own inline map)
  3. corporate-suffix stripping on BOTH sides: "Paramount Pictures" ->
     "paramount" == "Paramount"; "A24 Films" -> "a24" == "A24"
  4. leading-token match: the raw name starts with a database label as whole
     words ("Neon Rated LLC" -> "Neon") -- the longest such label wins

If nothing matches, the raw value is returned with matched=False so the
caller can flag the row for review instead of silently shipping a network
the database does not have.
"""

import re

# raw spelling (lower-case) -> database label. Only needed where suffix
# stripping cannot get there on its own (different words, abbreviations).
ALIASES_MOVIE = {
    "paramount pictures": "Paramount",
    "paramount pictures releasing": "Paramount",
    "paramount pictures corporation": "Paramount",
    "paramount skydance": "Paramount",
    "walt disney studios motion pictures": "Disney",
    "walt disney pictures": "Disney",
    "buena vista pictures": "Disney",
    "buena vista pictures distribution": "Disney",
    "the walt disney company": "Disney",
    "20th century fox": "20th Century Studios",
    "twentieth century fox": "20th Century Studios",
    "sony pictures releasing": "Sony / Columbia",
    "sony pictures": "Sony / Columbia",
    "sony pictures entertainment": "Sony / Columbia",
    "columbia pictures": "Sony / Columbia",
    "tristar pictures": "Sony / Columbia",
    "sony pictures classics": "Sony Classics",
    "warner bros. pictures": "Warner Bros.",
    "warner bros": "Warner Bros.",
    "warner bros. discovery": "Warner Bros.",
    "new line cinema": "Warner Bros.",
    "universal": "Universal Pictures",
    "universal studios": "Universal Pictures",
    "lionsgate": "Lionsgate / Summit",
    "lionsgate films": "Lionsgate / Summit",
    "lions gate films": "Lionsgate / Summit",
    "summit entertainment": "Lionsgate / Summit",
    "amazon studios": "Amazon MGM Studios",
    "united artists releasing": "United Artists Releasing",
    "neon rated": "Neon",
    "focus features llc": "Focus Features",
    "fox searchlight pictures": "Searchlight Pictures",
    "stx": "STX Entertainment",
    "stxfilms": "STX Entertainment",
    "ifc": "IFC Films",
    "magnolia pictures": "Magnolia Pictures",
    "cineverse entertainment": "Cineverse",
    "cineverse corp.": "Cineverse",
    "pbs": "PBS network",
    "public broadcasting service": "PBS network",
    "pbs distribution": "PBS network",
    "angel": "Angel Studios",
}
ALIASES_TV = {
    "paramount+ with showtime": "Paramount+",
    "max": "HBO Max",
    "apple tv": "Apple TV+",
    "apple tv plus": "Apple TV+",
    "disney plus": "Disney+",
    "prime video": "Amazon Prime Video",
    "amazon prime": "Amazon Prime Video",
    "peacock": "NBC Peacock",
    "the cw television network": "The CW",
    "cw": "The CW",
    "fox broadcasting company": "Fox",
    "american broadcasting company": "ABC",
    "national broadcasting company": "NBC",
    "cbs television network": "CBS",
    "history channel": "History",
}

# corporate words that never distinguish one studio from another
_SUFFIX_WORDS = {
    "pictures", "picture", "films", "film", "studios", "studio",
    "entertainment", "releasing", "release", "distribution", "distributors",
    "distributing", "media", "motion", "inc", "llc", "ltd", "corp",
    "corporation", "company", "co", "group", "worldwide", "international",
    "network", "networks", "television", "tv", "channel", "the", "usa", "us",
    "america", "americas",
}


def _clean(s):
    s = str(s or "").lower().replace("&", " and ")
    s = re.sub(r"[.,'\"()]", "", s)
    s = re.sub(r"[^a-z0-9+!]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _core(s):
    """Name with the generic corporate words removed from BOTH ends."""
    toks = _clean(s).split()
    while toks and toks[0] in _SUFFIX_WORDS:
        toks.pop(0)
    while toks and toks[-1] in _SUFFIX_WORDS:
        toks.pop()
    return " ".join(toks)


def _first_line(raw):
    for part in re.split(r"[\n;]|\s/\s(?=[A-Z])", str(raw or "")):
        if part.strip():
            return part.strip()
    return ""


def canonical_network(raw, labels, aliases=None):
    """(label, matched) for `raw` against the database `labels` iterable.

    `labels` are the exact database spellings. `aliases` maps a lower-case raw
    spelling to a database label."""
    raw_s = _first_line(raw)
    if not raw_s:
        return "", False
    labels = [str(l).strip() for l in (labels or []) if str(l or "").strip()]
    by_lower = {l.lower(): l for l in labels}
    rl = raw_s.lower().strip()

    # 1) exact
    if rl in by_lower:
        return by_lower[rl], True

    # 2) aliases (only honoured when the target really is in the database,
    #    or when no database list is loaded at all)
    for amap in (aliases or {},):
        for key in (rl, _clean(raw_s)):
            tgt = amap.get(key)
            if tgt and (not labels or tgt.lower() in by_lower):
                return by_lower.get(tgt.lower(), tgt), True

    # 3) suffix-stripped equality
    core = _core(raw_s)
    if core:
        cores = {}
        for l in labels:
            cores.setdefault(_core(l), []).append(l)
        hits = cores.get(core)
        if hits:
            # "magnolia" matches both "Magnolia" and "Magnolia Pictures":
            # prefer the label sharing the most words with the raw name
            rw = set(_clean(raw_s).split())
            return max(hits, key=lambda l: (len(rw & set(_clean(l).split())),
                                            -len(l))), True
        # alias on the stripped form ("Paramount Pictures Releasing Inc")
        for amap in (aliases or {},):
            for k, tgt in amap.items():
                if _core(k) == core and (not labels or tgt.lower() in by_lower):
                    return by_lower.get(tgt.lower(), tgt), True

    # 4) the raw name begins with a whole database label
    cl = _clean(raw_s)
    best = ""
    for l in labels:
        ll = _clean(l)
        if len(ll) >= 3 and (cl == ll or cl.startswith(ll + " ")) \
                and len(ll) > len(_clean(best)):
            best = l
    if best:
        return best, True
    return raw_s, False


def movie_labels(tref, extra=()):
    """Every studio label the film template (plus inline tables) knows."""
    out = list(extra)
    try:
        out += [v.get("name") or k for k, v in (tref.FILM_STUDIOS or {}).items()]
    except Exception:  # noqa: BLE001
        pass
    return [l for l in out if l]


def tv_labels(tref, extra=()):
    out = list(extra)
    try:
        out += [v.get("name") or k for k, v in (tref.TV_NETWORKS or {}).items()]
    except Exception:  # noqa: BLE001
        pass
    return [l for l in out if l]
