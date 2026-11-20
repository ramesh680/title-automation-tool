"""
reference_data.py
-----------------
Loads the Ops ingest templates as AUTHORITATIVE reference data:

  reference/film_ingest_template.xlsx  ("NEW Ingest Template - Film w/ Language")
    - DropDown sheet: 271 studios -> Studio Type, YouTube (Company), parent
      Company, Twitter Keyword String, Reddit Keyword String
    - 'Media Conglomerate Brand Sets' sheet: per-studio DAR roll-up lines

  reference/tv_ingest_template.xlsx    ("New Brand Definitions Ingest Template - TV")
    - DropdownLists sheet: 228 networks -> Network Type, Ticker, YouTube,
      Twitter/Reddit Keyword Strings; primary-genre Order of Operations
    - 'Conglomerate Brand Sets' sheet: per-network DAR roll-up block

To update the tool's logic, Ops replaces these files in the repo's
reference/ folder -- no code change needed. Paths can be overridden with the
REFERENCE_DIR / FILM_TEMPLATE / TV_TEMPLATE environment variables.

Everything fails soft: if the files are missing or unreadable, the app falls
back to its built-in tables.
"""

import logging
import os

log = logging.getLogger(__name__)

try:
    import openpyxl
except Exception:  # pragma: no cover
    openpyxl = None

REF_DIR = os.getenv("REFERENCE_DIR",
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference"))
FILM_TEMPLATE = os.getenv("FILM_TEMPLATE", os.path.join(REF_DIR, "film_ingest_template.xlsx"))
TV_TEMPLATE = os.getenv("TV_TEMPLATE", os.path.join(REF_DIR, "tv_ingest_template.xlsx"))
TALENT_TEMPLATE = os.getenv("TALENT_TEMPLATE", os.path.join(REF_DIR, "talent_ingest_template.xlsx"))
GAME_TEMPLATE = os.getenv("GAME_TEMPLATE", os.path.join(REF_DIR, "videogame_ingest_template.xlsx"))

FILM_STUDIOS = {}       # studio(lower) -> dict(studio_type, youtube, company, twitter_clause, reddit_clause)
FILM_ROLLUPS = {}       # studio(lower) -> "Roll-Up 1\nRoll-Up 2[\nRoll-Up 3]"
TV_NETWORKS = {}        # network(lower) -> dict(network_type, ticker, youtube, twitter_clause, reddit_clause)
TV_CONGLOMERATES = {}   # network(lower) -> multi-line brand_set block
TV_PRIMARY_ORDER = []   # [(genre, mapped_primary), ...] in Order of Operations
TALENT_TYPES = []       # ['Talent Type - Actor', ...]
TALENT_SUBTYPES = []    # ['Talent Subtype - Athlete - Basketball', ...]
TALENT_BRAND_SETS = []  # optional extra talent brand sets
GAME_DEVELOPERS = {}    # dev(lower, no prefix) -> dict(twitter_clause, reddit_clause, youtube)
GAME_PUBLISHERS = {}    # publisher(lower) -> dict(youtube, twitter_clause)
GAME_GENRE_MAP = {}     # genre(lower) -> Primary Genre
GAME_PLATFORMS = []     # ['Platform - PC', ...]

LOADED = False


def _s(v):
    return str(v).strip() if v is not None else ""


# ---------------- column lookup by header ----------------
# Templates are matched by HEADER name (the n-th column with that header) so an
# Ops edit that inserts or moves a column cannot silently shift every field;
# the historical fixed position is the fallback when a header is missing.
def _headers(ws):
    return [_s(c.value) for c in next(ws.iter_rows(min_row=1, max_row=1))]


def _col(hdr, name, nth=1, default=None, prefix=False):
    """1-based index of the nth column whose header is "name" (or starts with
    it when prefix=True); "default" when not found."""
    seen = 0
    want = name.lower()
    for i, h in enumerate(hdr):
        hl = h.lower().replace("\n", " ")
        if (hl.startswith(want) if prefix else hl == want):
            seen += 1
            if seen == nth:
                return i + 1
    return default


def _rows(ws):
    return list(ws.iter_rows(min_row=2, values_only=True))


def _cell(row, idx):
    return _s(row[idx - 1]) if idx and idx - 1 < len(row) else ""


def _load_film(path, T):
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb["DropDown"]
    h = _headers(ws)
    c_st = _col(h, "Studio", 1, 10)
    c_ty = _col(h, "Studio Type", 2, 11)
    c_yt = _col(h, "YouTube (Company)", 1, 12)
    c_co = _col(h, "Company", 2, 13)
    c_tw = _col(h, "Twitter Keyword String", 1, 14)
    c_st2 = _col(h, "Studio", 2, 18)
    c_rd = _col(h, "Reddit Keyword String", 1, 22)
    rows = _rows(ws)
    for r in rows:
        st = _cell(r, c_st)
        if not st:
            continue
        T["FILM_STUDIOS"][st.lower()] = dict(
            name=st, studio_type=_cell(r, c_ty), youtube=_cell(r, c_yt),
            company=_cell(r, c_co), twitter_clause=_cell(r, c_tw), reddit_clause="")
    for r in rows:
        st = _cell(r, c_st2)
        if st and st.lower() in T["FILM_STUDIOS"]:
            T["FILM_STUDIOS"][st.lower()]["reddit_clause"] = _cell(r, c_rd)
    # per-studio conglomerate roll-ups (column-oriented: row 2 = studio)
    ws2 = wb["Media Conglomerate Brand Sets"]
    grid = list(ws2.iter_rows(min_row=1, max_row=6, values_only=True))
    width = max(len(g) for g in grid) if grid else 0
    for c in range(1, width):
        studio = _s(grid[1][c]) if len(grid) > 1 and c < len(grid[1]) else ""
        if not studio:
            continue
        lines = [_s(grid[rr][c]) for rr in (2, 3, 4) if rr < len(grid) and c < len(grid[rr])]
        lines = [l for l in lines if l]
        if lines:
            T["FILM_ROLLUPS"][studio.lower()] = "\n".join(lines)
    wb.close()


def _load_tv(path, T):
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb["DropdownLists"]
    h = _headers(ws)
    c_co = _col(h, "Companies", 1, 2)
    c_nw = _col(h, "Network", 1, 3)
    c_ty = _col(h, "Network Type", 1, 4)
    c_tk = _col(h, "Ticker Symbol", 1, 5)
    c_yt = _col(h, "YouTube (Company)", 1, 6)
    c_tw = _col(h, "Twitter Keyword String", 1, 7)
    c_nw2 = _col(h, "Network", 2, 18)
    c_rd = _col(h, "Reddit Keyword String", 1, 22)
    c_g = _col(h, "List of Regular Genres", 1, 14, prefix=True)
    c_m = _col(h, "Mapped Primary Genre", 1, 15)
    rows = _rows(ws)
    for r in rows:
        n = _cell(r, c_nw)
        if not n:
            continue
        T["TV_NETWORKS"][n.lower()] = dict(
            name=n, company=_cell(r, c_co), network_type=_cell(r, c_ty),
            ticker=_cell(r, c_tk), youtube=_cell(r, c_yt),
            twitter_clause=_cell(r, c_tw), reddit_clause="")
    for r in rows:
        n = _cell(r, c_nw2)
        if n and n.lower() in T["TV_NETWORKS"]:
            T["TV_NETWORKS"][n.lower()]["reddit_clause"] = _cell(r, c_rd)
    for r in rows:
        g, m = _cell(r, c_g), _cell(r, c_m)
        if g and m:
            T["TV_PRIMARY_ORDER"].append((g, m))
    ws2 = wb["Conglomerate Brand Sets"]
    for r in _rows(ws2):
        n, b = _cell(r, 1), _cell(r, 2)
        if not n or b in ("", "None"):
            continue
        for key in (n.lower(), n.split("\n")[0].strip().lower()):
            T["TV_CONGLOMERATES"].setdefault(key, b)
    wb.close()


def _load_talent(path, T):
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb["DropDown"]
    h = _headers(ws)
    c_bs = _col(h, "Brand Sets", 1, 4)
    c_sub = _col(h, "Sub Category", 1, 6)
    c_typ = c_sub + 1          # 'Talent Type - ...' sits right of Sub Category
    for r in _rows(ws):
        sub, typ, bs = _cell(r, c_sub), _cell(r, c_typ), _cell(r, c_bs)
        if sub and sub not in T["TALENT_SUBTYPES"]:
            T["TALENT_SUBTYPES"].append(sub)
        if typ and typ not in T["TALENT_TYPES"]:
            T["TALENT_TYPES"].append(typ)
        if bs and bs not in T["TALENT_BRAND_SETS"]:
            T["TALENT_BRAND_SETS"].append(bs)
    wb.close()


def _dev_key(v):
    return _s(v).replace("Developer - ", "").strip().lower()


def _load_game(path, T):
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb["DropDown"]
    h = _headers(ws)
    c_dev = _col(h, "Developer", 1, 2)
    c_dtw = _col(h, "Developer Twitter Keyword", 1, 3)
    c_pub = _col(h, "Publisher", 1, 4)
    c_pyt = _col(h, "Publisher YouTube (Company)", 1, 5)
    c_ptw = _col(h, "Publisher Twitter Keyword", 1, 6)
    c_plat = _col(h, "Platform", 1, 8)
    c_g = _col(h, "Mapped Genres", 1, 9)
    c_p = _col(h, "Primary Genres", 1, 10)
    c_dev2 = _col(h, "Developer", 2, 11)
    c_drd = _col(h, "Developer Reddit Keyword", 1, 12)
    c_dev3 = _col(h, "Developer", 3, 14)
    c_dyt = _col(h, "Developer YouTube (Company)", 1, 15)
    D, P = T["GAME_DEVELOPERS"], T["GAME_PUBLISHERS"]
    for r in _rows(ws):
        dev = _cell(r, c_dev)
        if dev:
            D.setdefault(_dev_key(dev), {})["twitter_clause"] = _cell(r, c_dtw)
        dev_r = _cell(r, c_dev2)
        if dev_r:
            D.setdefault(_dev_key(dev_r), {})["reddit_clause"] = _cell(r, c_drd)
        dev_y = _cell(r, c_dev3)
        if dev_y:
            D.setdefault(_dev_key(dev_y), {})["youtube"] = _cell(r, c_dyt)
        pub = _cell(r, c_pub)
        if pub:
            P[pub.lower()] = dict(youtube=_cell(r, c_pyt), twitter_clause=_cell(r, c_ptw))
        plat = _cell(r, c_plat)
        if plat and plat not in T["GAME_PLATFORMS"]:
            T["GAME_PLATFORMS"].append(plat)
        g, p = _cell(r, c_g), _cell(r, c_p)
        if g and p:
            T["GAME_GENRE_MAP"][g.lower()] = p
    wb.close()


_TABLES = ("FILM_STUDIOS", "FILM_ROLLUPS", "TV_NETWORKS", "TV_CONGLOMERATES",
           "TV_PRIMARY_ORDER", "TALENT_TYPES", "TALENT_SUBTYPES", "TALENT_BRAND_SETS",
           "GAME_DEVELOPERS", "GAME_PUBLISHERS", "GAME_GENRE_MAP", "GAME_PLATFORMS")
_LOADERS = {"film": _load_film, "tv": _load_tv, "talent": _load_talent, "game": _load_game}
_CATEGORY_TABLES = {
    "film": ("FILM_STUDIOS", "FILM_ROLLUPS"),
    "tv": ("TV_NETWORKS", "TV_CONGLOMERATES", "TV_PRIMARY_ORDER"),
    "talent": ("TALENT_TYPES", "TALENT_SUBTYPES", "TALENT_BRAND_SETS"),
    "game": ("GAME_DEVELOPERS", "GAME_PUBLISHERS", "GAME_GENRE_MAP", "GAME_PLATFORMS"),
}
VERSION = 0              # bumped on every successful (re)load; caches key on it
SOURCES = {}             # category -> path the live tables came from


def _empty():
    return {t: ([] if t in ("TV_PRIMARY_ORDER", "TALENT_TYPES", "TALENT_SUBTYPES",
                            "TALENT_BRAND_SETS", "GAME_PLATFORMS") else {})
            for t in _TABLES}


def parse_category(category, path):
    """Parse one template file into fresh tables WITHOUT touching the live
    ones -- used to validate a downloaded template before switching to it."""
    T = _empty()
    _LOADERS[category](path, T)
    return {t: T[t] for t in _CATEGORY_TABLES[category]}


def counts():
    g = globals()
    return {t: len(g[t]) for t in _TABLES}


def install(category, tables, path=""):
    """Publish freshly parsed tables for one category. Each table is REBOUND
    (not mutated), so a request reading the old dict mid-swap stays consistent."""
    global VERSION, LOADED
    g = globals()
    for t in _CATEGORY_TABLES[category]:
        g[t] = tables[t]
    SOURCES[category] = path
    VERSION += 1
    LOADED = bool(g["FILM_STUDIOS"] or g["TV_NETWORKS"])


def load(paths=None):
    """(Re)load the templates. "paths" maps category -> file (defaults to the
    bundled reference/ copies). Returns True when any reference data loaded."""
    if openpyxl is None:
        return False
    default = {"film": FILM_TEMPLATE, "tv": TV_TEMPLATE,
               "talent": TALENT_TEMPLATE, "game": GAME_TEMPLATE}
    default.update(paths or {})
    for cat, path in default.items():
        try:
            if os.path.exists(path):
                install(cat, parse_category(cat, path), path)
            else:
                log.warning("%s template not found: %s", cat, path)
        except Exception as e:  # noqa: BLE001
            log.warning("%s template load failed (%s): %s", cat, path, e)
    c = counts()
    log.info("reference templates loaded: %d studios, %d networks, %d film roll-ups, "
             "%d tv conglomerates, %d genre mappings", c["FILM_STUDIOS"], c["TV_NETWORKS"],
             c["FILM_ROLLUPS"], c["TV_CONGLOMERATES"], c["TV_PRIMARY_ORDER"])
    return LOADED


# ---------------- lookups ----------------
def film_studio(studio):
    return FILM_STUDIOS.get(_s(studio).lower())


def film_rollup(studio):
    return FILM_ROLLUPS.get(_s(studio).lower(), "")


def tv_network(network):
    return TV_NETWORKS.get(_s(network).lower())


def tv_conglomerate(network):
    return TV_CONGLOMERATES.get(_s(network).lower(), "")


def tv_primary_genre(genres):
    """First genre in the Order-of-Operations list that the title has wins;
    its Mapped Primary Genre is the answer ('' when nothing matches)."""
    gs = {_s(g) for g in genres}
    for g, mapped in TV_PRIMARY_ORDER:
        if g in gs and mapped != "N/A":
            return mapped
    return ""


def game_developer(name):
    return GAME_DEVELOPERS.get(_dev_key(name))


def game_publisher(name):
    return GAME_PUBLISHERS.get(_s(name).lower())


def game_primary_genre(genre):
    return GAME_GENRE_MAP.get(_s(genre).lower(), "")


def game_platform_for(term):
    """Match a discovered platform label to the template's 'Platform - X'.
    Exact match wins before fuzzy (so 'Switch 2' doesn't hit 'Switch')."""
    t = _s(term).lower()
    if not t:
        return ""
    for p in GAME_PLATFORMS:
        if p.split(" - ", 1)[1].lower() == t:
            return p
    for p in GAME_PLATFORMS:
        tail = p.split(" - ", 1)[1].lower()
        if tail in t or t in tail:
            return p
    return ""


def talent_subtype_for(kind, term):
    """Find the canonical 'Talent Subtype - <kind> - <x>' entry matching a
    discovered term (e.g. kind='Athlete', term='Basketball')."""
    t = _s(term).lower()
    if not t:
        return ""
    prefix = f"talent subtype - {_s(kind).lower()} - "
    for sub in TALENT_SUBTYPES:
        sl = sub.lower()
        if sl.startswith(prefix):
            tail = sl[len(prefix):]
            if tail == t or t in tail or tail in t:
                return sub
    return ""


load()
