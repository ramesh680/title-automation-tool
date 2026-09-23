"""
source_pages.py
---------------
PURE parsing helpers (no network) for the pages TitleForge cross-checks a
movie / show against. Kept network-free so every rule is unit-testable
offline; metadata_fetcher.py does the fetching and passes the HTML in.

  * Rotten Tomatoes title page  -> release date, year, Wide/Limited, distributor
  * Metacritic title page       -> release date, year, Wide/Limited (if shown)
  * distributor / official site -> Wide/Limited wording, social links
  * YouTube trailer description -> the studio's own list of the title's
                                   socials (e.g. '#TheRescueMovie /
                                   Instagram: instagram.com/therescuemovie')
  * IMDb title page             -> 'Official sites' social links

and the rules that turn many sources into ONE answer:

  * release_scale_consensus()  -- Wide vs Limited across all sources
  * handle_matches_title()     -- is a social handle this title's own account
                                  (and not the studio's / another title's)?
  * handle_guesses()           -- candidate handles to probe when no source
                                  listed one (hashtag, other platforms, the
                                  studio naming patterns)
"""

import html as _html
import json
import re

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
     "nov", "dec"], 1)}


# ---------------------------------------------------------------- basics
def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def page_text(html):
    """Visible text of an HTML page, whitespace-collapsed (scripts, styles
    and tags removed, entities decoded)."""
    s = str(html or "")
    s = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = _html.unescape(s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def _date_from_text(s):
    """'Jan 29, 2027' / 'January 29, 2027' / '2027-01-29' -> '2027-01-29'."""
    s = str(s or "")
    m = re.search(r"((?:19|20)\d{2})-(\d{2})-(\d{2})", s)
    if m:
        return "%s-%s-%s" % m.groups()
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+((?:19|20)\d{2})", s)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return "%s-%02d-%02d" % (m.group(3), _MONTHS[m.group(1)[:3].lower()],
                                 int(m.group(2)))
    return ""


def _jsonld_blocks(html):
    out = []
    for m in re.finditer(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>',
                         str(html or "")):
        try:
            j = json.loads(m.group(1).strip())
        except Exception:  # noqa: BLE001
            continue
        stack = [j]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                out.append(x)
                if isinstance(x.get("@graph"), list):
                    stack.extend(x["@graph"])
    return out


def _scale_word(s):
    s = str(s or "").strip().lower()
    if s.startswith("wide") or "nationwide" in s:
        return "Wide"
    if s.startswith("limited") or "select theater" in s or "select cinema" in s:
        return "Limited"
    return ""


# ------------------------------------------------------- Rotten Tomatoes
_RT_LABELS = ("Director", "Producer", "Screenwriter", "Distributor",
              "Production Co", "Rating", "Genre", "Original Language",
              "Release Date (Theaters)", "Release Date (Streaming)",
              "Box Office (Gross USA)", "Runtime", "Sound Mix", "Aspect Ratio",
              "Rerelease Date (Theaters)", "Network", "Premiere Date")


def _rt_field(text, label):
    """Value that follows `label` in RT's Movie Info list, up to the next
    known label."""
    others = "|".join(re.escape(l) for l in _RT_LABELS if l != label)
    m = re.search(re.escape(label) + r"\s*:?\s*(.+?)\s*(?=(?:" + others
                  + r")\s*:?|$)", text)
    if not m:
        return ""
    return m.group(1).strip(" ,:")[:200]


def parse_rottentomatoes(html):
    """{title, release_date, year, scale, distributor} from an RT page.

    RT prints e.g. 'Release Date (Theaters): Jan 29, 2027, Wide' and
    'Distributor: Paramount Pictures' in its Movie Info block. Parsing runs on
    the tag-stripped text so it survives RT's frequent markup changes."""
    out = {}
    if not html:
        return out
    text = page_text(html)
    theat = _rt_field(text, "Release Date (Theaters)")
    if theat:
        d = _date_from_text(theat)
        if d:
            out["release_date"] = d
        m = re.search(r"\b(wide|limited)\b", theat, re.I)
        if m:
            out["scale"] = m.group(1).title()
    if "release_date" not in out:
        stream = _rt_field(text, "Release Date (Streaming)")
        d = _date_from_text(stream)
        if d:
            out["release_date"] = d
    dist = _rt_field(text, "Distributor")
    if dist:
        out["distributor"] = re.split(r",\s*|\s{2,}", dist)[0].strip()
    for j in _jsonld_blocks(html):
        if str(j.get("@type") or "").lower() in ("movie", "tvseries"):
            if j.get("name") and "title" not in out:
                out["title"] = str(j["name"])
            dc = _date_from_text(j.get("dateCreated") or j.get("datePublished"))
            if dc and "release_date" not in out:
                out["release_date"] = dc
    if "title" not in out:
        m = re.search(r"(?is)<title>\s*(.*?)\s*(?:\|\s*Rotten Tomatoes)?\s*</title>", html)
        if m:
            out["title"] = _html.unescape(m.group(1)).strip()
    if out.get("release_date"):
        out["year"] = int(out["release_date"][:4])
    else:
        m = re.search(r"\((?:19|20)\d{2}\)", out.get("title", ""))
        if m:
            out["year"] = int(m.group(0)[1:5])
    return out


# ------------------------------------------------------------ Metacritic
def parse_metacritic(html):
    """{title, release_date, year, scale, distributor} from a Metacritic page."""
    out = {}
    if not html:
        return out
    for j in _jsonld_blocks(html):
        t = str(j.get("@type") or "").lower()
        if t in ("movie", "tvseries", "tvseason", "creativework"):
            if j.get("name"):
                out.setdefault("title", str(j["name"]))
            d = _date_from_text(j.get("datePublished") or j.get("dateCreated"))
            if d:
                out.setdefault("release_date", d)
            pc = j.get("productionCompany")
            if isinstance(pc, list) and pc:
                pc = pc[0]
            if isinstance(pc, dict) and pc.get("name"):
                out.setdefault("production_company", str(pc["name"]))
    text = page_text(html)
    if "release_date" not in out:
        m = re.search(r"Release Date\s*:?\s*([A-Za-z]{3,9}\.? \d{1,2}, \d{4})", text)
        if m:
            out["release_date"] = _date_from_text(m.group(1))
    m = re.search(r"Release Date\s*:?\s*[A-Za-z]{3,9}\.? \d{1,2}, \d{4}\s*\(?\s*(wide|limited)\b",
                  text, re.I)
    if m:
        out["scale"] = m.group(1).title()
    m = re.search(r"Distributor\s*:?\s*([A-Z][^:]{1,60}?)(?=\s+(?:Production|Release|Genre|Rating|Duration|Tagline|Director|Writer|$))",
                  text)
    if m:
        out["distributor"] = m.group(1).strip(" ,")
    if out.get("release_date"):
        out["year"] = int(out["release_date"][:4])
    return out


# ------------------------------------------------- distributor / official
def parse_official_site_scale(html, title=""):
    """Wide / Limited from a distributor or official-site page's wording.

    Only unambiguous phrases count: 'in select theaters' / 'limited
    engagement' -> Limited; 'in theaters everywhere' / 'nationwide' -> Wide.
    ('only in theaters' is used for both, so it is ignored.)"""
    text = page_text(html).lower()
    if not text:
        return ""
    if re.search(r"\bin select (?:theat(?:er|re)s|cinemas)\b|\blimited (?:engagement|release|theatrical)\b", text):
        return "Limited"
    if re.search(r"\b(?:in )?theat(?:er|re)s everywhere\b|\bnationwide\b|\bwide release\b|\beverywhere [a-z]+ \d{1,2}\b", text):
        return "Wide"
    return ""


def release_scale_consensus(votes, priority=("rottentomatoes", "distributor",
                                              "metacritic", "boxofficemojo",
                                              "tmdb")):
    """One Wide/Limited answer from {source: 'Wide'|'Limited'|''}.

    Majority of the sources that SAID something wins; a tie is broken by
    `priority` (Rotten Tomatoes first -- it labels the scale explicitly --
    then the distributor, Metacritic, Box Office Mojo's calendar, TMDB).
    Returns (scale, conflict_note). conflict_note is non-empty when the
    sources disagree, naming who said what, so a reviewer can confirm."""
    said = {k: v for k, v in (votes or {}).items() if v in ("Wide", "Limited")}
    if not said:
        return "", ""
    wide = [k for k, v in said.items() if v == "Wide"]
    lim = [k for k, v in said.items() if v == "Limited"]
    if len(wide) != len(lim):
        scale = "Wide" if len(wide) > len(lim) else "Limited"
    else:
        rank = {s: i for i, s in enumerate(priority)}
        top = min(said, key=lambda k: rank.get(k, 99))
        scale = said[top]
    note = ""
    if wide and lim:
        label = {"rottentomatoes": "Rotten Tomatoes", "metacritic": "Metacritic",
                 "boxofficemojo": "Box Office Mojo", "distributor": "distributor site",
                 "tmdb": "TMDB"}
        note = ("Release scale: sources disagree (Wide: %s; Limited: %s) -- "
                "used %s, verify." % (", ".join(label.get(k, k) for k in wide),
                                      ", ".join(label.get(k, k) for k in lim), scale))
    return scale, note


def tmdb_us_scale(us_types):
    """TMDB US release types -> scale. Type 2 = 'Theatrical (limited)',
    type 3 = 'Theatrical'. Only a limited-only pattern is a usable signal."""
    types = set(us_types or [])
    if 2 in types and 3 not in types:
        return "Limited"
    return ""


# ---------------------------------------------------------- social links
_SOCIAL_PATTERNS = {
    "instagram": re.compile(r"(?<![A-Za-z0-9-])(?:www\.|m\.|mobile\.)?instagram\.com/([A-Za-z0-9_.]{1,30})", re.I),
    "facebook": re.compile(r"(?<![A-Za-z0-9-])(?:www\.|m\.|mobile\.)?(?:facebook|fb)\.com/([A-Za-z0-9.\-]{2,80})", re.I),
    "twitter": re.compile(r"(?<![A-Za-z0-9-])(?:www\.|m\.|mobile\.)?(?:twitter|x)\.com/@?([A-Za-z0-9_]{1,15})(?![A-Za-z0-9_.])", re.I),
    "tiktok": re.compile(r"(?<![A-Za-z0-9-])(?:www\.|m\.|mobile\.)?tiktok\.com/@([A-Za-z0-9_.]{2,24})", re.I),
    "threads": re.compile(r"(?<![A-Za-z0-9-])(?:www\.|m\.|mobile\.)?threads\.(?:net|com)/@([A-Za-z0-9_.]{1,30})", re.I),
}
_RESERVED = {
    "instagram": {"p", "reel", "reels", "explore", "stories", "accounts", "tv",
                  "about", "developer", "legal", "direct"},
    "facebook": {"sharer", "sharer.php", "share", "share.php", "pages", "groups",
                 "events", "watch", "profile.php", "p", "people", "plugins",
                 "dialog", "tr", "login", "login.php", "help", "policies",
                 "privacy", "hashtag", "photo", "photo.php", "story.php",
                 "permalink.php", "gaming", "business", "ads", "home.php"},
    "twitter": {"intent", "share", "home", "hashtag", "search", "i", "settings",
                "privacy", "tos", "login", "explore", "messages", "notifications",
                "compose", "widgets", "status"},
    "tiktok": set(),
    "threads": set(),
}


def extract_social_links(text):
    """{platform: [handle, ...]} in first-seen order from any text / HTML
    (JSON-escaped URLs included)."""
    s = str(text or "").replace("\\/", "/").replace("\\u002F", "/")
    s = _html.unescape(s)
    out = {}
    for plat, pat in _SOCIAL_PATTERNS.items():
        seen = []
        for m in pat.finditer(s):
            h = m.group(1).rstrip(".")
            if h.lower() in _RESERVED[plat] or not h:
                continue
            if plat == "facebook" and h.lower().endswith(".php"):
                continue
            if h.lower() not in [x.lower() for x in seen]:
                seen.append(h)
        if seen:
            out[plat] = seen
    return out


def extract_hashtags(text):
    s = _html.unescape(str(text or ""))
    tags = []
    for m in re.finditer(r"(?<![&\w])#([A-Za-z][A-Za-z0-9_]{2,60})", s):
        t = m.group(1)
        if t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    return tags


_STOP_LEAD = ("the", "a", "an")


def title_keys(title):
    """Normalised forms of a title a handle may be built from:
    'The Rescue' -> {'therescue', 'rescue'}; 'Dune: Part Two' ->
    {'duneparttwo', 'dune'}."""
    t = re.sub(r"\s*\((?:[^)]*)\)\s*$", "", str(title or "")).strip()
    keys = set()
    full = _norm(t)
    if full:
        keys.add(full)
    words = re.findall(r"[A-Za-z0-9']+", t)
    if words and words[0].lower() in _STOP_LEAD and len(words) > 1:
        keys.add(_norm(" ".join(words[1:])))
    main = re.split(r"\s*[:–—-]\s+", t)[0]
    if main and main != t:
        keys.add(_norm(main))
        mw = re.findall(r"[A-Za-z0-9']+", main)
        if mw and mw[0].lower() in _STOP_LEAD and len(mw) > 1:
            keys.add(_norm(" ".join(mw[1:])))
    return {k for k in keys if k}


_HANDLE_SUFFIXES = ("", "movie", "film", "themovie", "thefilm", "official",
                    "series", "show", "tv", "onnetflix", "hbo", "max")


def handle_matches_title(handle, title, year=None):
    """True when `handle` is plausibly the title's OWN account.

    A long key (>= 5 chars) only has to appear in the handle
    ('therescuemovie' contains 'rescue'); a short one must make up the whole
    handle apart from a known studio suffix or the year ('usmovie'), so
    'itunes' is never taken for 'It'."""
    h = _norm(handle)
    if not h:
        return False
    yr = str(year or "")
    for k in title_keys(title):
        if len(k) >= 5 and k in h:
            return True
        for pre in ("", "the"):
            for suf in _HANDLE_SUFFIXES:
                for y in ("", yr):
                    if h == pre + k + suf + y:
                        return True
    return False


def pick_title_handles(links, title, year=None, exclude=()):
    """From extract_social_links() output, keep the first handle per platform
    that belongs to the title (studio / talent handles are dropped)."""
    ex = {_norm(x) for x in exclude if x}
    out = {}
    for plat, handles in (links or {}).items():
        for h in handles:
            if _norm(h) in ex:
                continue
            if handle_matches_title(h, title, year):
                out[plat] = h
                break
    return out


def handle_guesses(title, year=None, known=(), hashtags=()):
    """Ordered candidate handles to probe for a platform no source covered.

    Returns [(handle, strength)]: strength 'strong' for a handle already
    confirmed on another platform or derived from the title's own hashtag
    (studios reuse one handle everywhere -- 'therescuemovie' on IG, FB, X and
    TikTok), 'pattern' for a naming-pattern guess."""
    out, seen = [], set()

    def add(h, strength):
        n = _norm(h)
        if n and n not in seen:
            seen.add(n)
            out.append((h, strength))

    for h in known:
        if h:
            add(str(h).lstrip("@"), "strong")
    for tag in hashtags:
        if handle_matches_title(tag, title, year):
            add(tag.lower(), "strong")
    keys = sorted(title_keys(title), key=len, reverse=True)
    if keys:
        full = keys[0]
        base = full if full.startswith("the") else full
        for suf in ("movie", "film", "themovie"):
            add(base + suf, "pattern")
        if not full.startswith("the"):
            add("the" + full + "movie", "pattern")
        if year:
            add(full + str(year), "pattern")
            add(full + "movie" + str(year), "pattern")
    return out


# ---------------------------------------------------- YouTube / IMDb pages
def youtube_description_from_watch_html(html):
    m = re.search(r'"shortDescription":"((?:[^"\\]|\\.)*)"', str(html or ""))
    if not m:
        return ""
    try:
        return json.loads('"' + m.group(1) + '"')
    except Exception:  # noqa: BLE001
        return m.group(1)


def youtube_search_results(html, limit=6):
    """[(video_id, title)] from a keyless youtube.com/results page."""
    out, seen = [], set()
    for m in re.finditer(r'"videoRenderer":\{"videoId":"([\w-]{11})".{0,1500}?"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"',
                         str(html or ""), re.S):
        vid = m.group(1)
        if vid in seen:
            continue
        seen.add(vid)
        try:
            t = json.loads('"' + m.group(2) + '"')
        except Exception:  # noqa: BLE001
            t = m.group(2)
        out.append((vid, t))
        if len(out) >= limit:
            break
    return out


def is_trailer_for(video_title, title):
    vt = _norm(video_title)
    return any(k in vt for k in title_keys(title) if len(k) >= 3) and \
        re.search(r"trailer|teaser", str(video_title or ""), re.I) is not None


def imdb_official_socials(html):
    """Social links IMDb lists under the title's 'Official sites'."""
    s = str(html or "").replace("\\/", "/")
    chunks = []
    for m in re.finditer(r'"officialSites"\s*:', s):
        chunks.append(s[m.start():m.start() + 6000])
    m = re.search(r'data-testid="details-officialsites"(.*?)</(?:li|div)>\s*</(?:ul|div)>', s, re.S)
    if m:
        chunks.append(m.group(1))
    return extract_social_links(" ".join(chunks)) if chunks else {}


def homepage_from_imdb(html):
    """First non-social official site URL IMDb lists (usually the studio's
    title page or the film's own site)."""
    s = str(html or "").replace("\\/", "/")
    m = re.search(r'"officialSites"\s*:(.{0,6000})', s, re.S)
    if not m:
        return ""
    for u in re.findall(r'"url"\s*:\s*"(https?://[^"]+)"', m.group(1)):
        if not re.search(r"instagram|facebook|twitter|x\.com|tiktok|youtube|threads", u, re.I):
            return u
    return ""
