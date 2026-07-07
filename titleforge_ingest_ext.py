"""
titleforge_ingest_ext.py
========================

Drop-in extension for the TitleForge / title-automation-tool Review + Validator flow.

Adds FOUR new ingest schemas on top of the existing four
(Movies / TV Shows / Talent / Video Game):

    - Beauty            -> title_category = "Health & Beauty"
    - Beverages         -> title_category = "Beverages"
    - Sports Teams      -> title_category = "Sports Franchise"
    - General           -> title_category = user-picked (49-value master list)

The two public entry points are:

    detect_schema(row)            -> schema key ("beauty" / "beverages" / ...) or None
    fill_category(row)            -> (title_category, title_sub_category)  -- backfills blanks

...and a bonus full converter for when you are reviewing the *Ingest Template*
sheet (human input) rather than a finished BrandDef sheet:

    build_branddef_row(schema, ingest_row)   -> dict keyed by BrandDef column name

`row` is a plain dict: {column_name: value}. Column names are matched
case-insensitively and whitespace-insensitively, so it works whether the row
comes from a BrandDef sheet or an Ingest Template sheet.

No third-party deps. Pure stdlib. Python 3.8+.
"""

from __future__ import annotations
import re
from typing import Dict, List, Optional, Tuple, Any

# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _norm(s: Any) -> str:
    """normalize a header/key for tolerant matching."""
    return re.sub(r"[^a-z0-9]", "", str(s).strip().lower())


def _get(row: Dict[str, Any], *names: str) -> str:
    """case/space-insensitive lookup; returns "" if absent/blank."""
    idx = {_norm(k): v for k, v in row.items()}
    for n in names:
        v = idx.get(_norm(n))
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return ""


def _has_col(row: Dict[str, Any], *names: str) -> bool:
    idx = {_norm(k) for k in row.keys()}
    return any(_norm(n) in idx for n in names)


def _is_standard(row: Dict[str, Any]) -> bool:
    """Standard perspective: explicit Perspective column (Ingest Template sheets)
    OR a ' - DAR' title suffix (finished BrandDef sheets have no Perspective col)."""
    if _get(row, "Perspective").lower() == "standard":
        return True
    title = _get(row, "title", "Title", "Title Name")
    return title.endswith(" - DAR")


def _dar(title: str, row: Dict[str, Any]) -> str:
    """append ' - DAR' when Perspective == Standard  (=B & IF(A="Standard"," - DAR",""))"""
    if title and _is_standard(row) and not title.endswith(" - DAR"):
        return f"{title} - DAR"
    return title


_HASHTAG_STRIP = str.maketrans({c: "" for c in " :,-!'.?"})

def _hashtag(name: str) -> str:
    """
    Mirror of the sheet formula:
      Substitute(... "#"&Title ... strip spaces/:,-!'./ ... "&"->"and")
    """
    if not name:
        return ""
    s = "#" + name
    s = s.replace("&", "and")
    s = s.translate(_HASHTAG_STRIP)
    return s


def _clean_instagram(url: str) -> str:
    if not url:
        return ""
    s = url
    for a, b in (("https:", ""), ("www.", ""), ("/", ""),
                 ("?hl=en", ""), ("instagram.com", "")):
        s = s.replace(a, b)
    return s.lower()


def _clean_slashes(url: str) -> str:
    if not url:
        return ""
    s = url
    for a in ("http:", "https:", "/"):
        s = s.replace(a, "")
    return s


def _stack(parts: List[str]) -> str:
    """join non-empty parts with newlines -- the sub_category 'stacked cell' convention."""
    return "\n".join(p for p in (str(x).strip() for x in parts) if p)


# ---------------------------------------------------------------------------
# schema definitions (the 4 NEW templates)
# ---------------------------------------------------------------------------
# Each schema declares:
#   category            fixed title_category, or None if user-supplied (General)
#   ingest_cols         ordered Ingest Template headers (for detection + conversion)
#   brand_set_standard  brand_set value when Perspective == Standard
#   sub_from_ingest(row)-> title_sub_category built from Ingest Template columns
#   sub_signals         substrings that identify this schema inside a *BrandDef* sub_category
#   brand_set_signals   substrings that identify this schema from brand_set
# ---------------------------------------------------------------------------

def _beauty_sub(row: Dict[str, Any]) -> str:
    return _stack([
        _get(row, "Beauty Type 1"), _get(row, "Beauty Type 2"),
        _get(row, "Beauty Type 3"), _get(row, "Beauty Type 4"),
        _get(row, "Beauty Type 5"), _get(row, "Beauty Type 6"),
        _get(row, "Beauty Company"),
    ])


def _beverages_sub(row: Dict[str, Any]) -> str:
    # sheet uses CONCATENATE(Type, Company) with no separator; we stack for readability
    return _stack([_get(row, "Beverage Type"), _get(row, "Beverage Company")])


def _sports_sub(row: Dict[str, Any]) -> str:
    return _get(row, "Sports Type")


def _general_sub(row: Dict[str, Any]) -> str:
    return ""  # General has no sub_category; genre carries the detail


SCHEMAS: Dict[str, Dict[str, Any]] = {
    "beauty": {
        "label": "Beauty",
        "category": "Health & Beauty",
        "brand_set_standard": "LF // Beauty",
        "brand_set_competitive": "Competitive View",
        "ingest_cols": [
            "Perspective", "Title", "Beauty Type 1", "Beauty Type 2",
            "Beauty Type 3", "Beauty Type 4", "Beauty Type 5", "Beauty Type 6",
            "Beauty Company", "Wikipedia", "Twitter Hashtag 1", "# 2", "# 3",
            "Twitter Page (URL)", "Facebook Page (URL)", "Instagram Account (URL)",
            "YouTube (Brand)", "TikTok", "Tumblr", "Pinterest Username",
        ],
        "sub_from_ingest": _beauty_sub,
        "sub_signals": ["beauty type -", "beauty company -"],
        "brand_set_signals": ["lf // beauty"],
        "detect_cols": ["Beauty Type 1", "Beauty Company"],
    },
    "beverages": {
        "label": "Beverages",
        "category": "Beverages",
        "brand_set_standard": "LF // Beverages",
        "brand_set_competitive": "Competitive View",
        "ingest_cols": [
            "Perspective", "Title", "Beverage Type", "Beverage Company",
            "Wikipedia", "Twitter Hashtag 1", "# 2", "# 3",
            "Twitter Page (URL)", "Facebook Page (URL)", "Instagram Account (URL)",
            "YouTube (Brand)", "TikTok", "Tumblr", "Pinterest Username",
        ],
        "sub_from_ingest": _beverages_sub,
        "sub_signals": ["beverage type -", "beverage company -"],
        "brand_set_signals": ["lf // beverages"],
        "detect_cols": ["Beverage Type", "Beverage Company"],
    },
    "sports": {
        "label": "Sports Teams",
        "category": "Sports Franchise",
        "brand_set_standard": "LF // Professional Sports Teams",
        "brand_set_competitive": "Competitive View",
        "ingest_cols": [
            "Perspective", "Title Name", "Title Category", "League", "Sports Type",
            "Wikipedia", "Facebook", "Twitter", "Instagram", "YouTube", "TikTok",
            "Tumblr", "Pinterest Username", "Twitter Hashtag 1", "# 2", "# 3",
            "Company", "Brand Sets",
        ],
        "sub_from_ingest": _sports_sub,
        "sub_signals": ["sports type -"],
        "brand_set_signals": [
            "lf // professional sports teams", "lf // english premier league",
            "lf // serie a", "lf // bundesliga", "lf // laliga", "lf // ligue 1",
            "lf // nba", "lf // nfl", "lf // nhl", "lf // mlb", "lf // mls",
        ],
        "detect_cols": ["League", "Sports Type"],
    },
    "general": {
        "label": "General",
        "category": None,  # user-picked from the master list below
        "brand_set_standard": "",  # carried through from the sheet's own Brand Sets col
        "brand_set_competitive": "",
        "ingest_cols": [
            "Perspective", "Title Name", "Title Category", "Ticker Symbol",
            "Genres", "Wikipedia", "Facebook", "Twitter", "Instagram", "YouTube",
            "TikTok", "LinkedIn", "Tumblr", "Rotten Tomatoes", "IMDB URL",
            "Metacritic", "Pinterest Username", "Twitter Hashtag 1", "# 2", "# 3",
            "Company", "Brand Sets",
        ],
        "sub_from_ingest": _general_sub,
        "sub_signals": [],
        "brand_set_signals": [],
        "detect_cols": ["Genres", "Ticker Symbol"],
    },
}

# The 49-value master Title Category list (from the General template DropDown).
# Used to validate/accept a user-supplied category for the General schema.
GENERAL_TITLE_CATEGORIES = [
    "Airlines", "Automotive", "Car Rental", "Consumer Electronics", "CPG",
    "Health & Beauty", "Education", "Energy", "Venues, Events & Attractions",
    "Fashion", "Financial Services", "Food Products", "Health, Wellness, Fitness",
    "Hospital & Health Care", "Hospitality", "Insurance", "Internet Services",
    "IT, Internet, Computing", "Legal", "Government Entities",
    "Marketing, Advertising and Research", "Materials and Construction", "Media",
    "Movies", "Music and Entertainment", "TV Network",
    "Non-Profit/Charity/Philanthropy", "Pets, Pet Foods & Pet Supplies",
    "Pharmaceuticals", "Radio", "Real Estate", "Restaurants", "Retail",
    "Beverages", "Sports Franchise", "Sports Organizations and Bodies",
    "Film Studio", "Supermarket, Grocery, Food & Convenience Stores", "Talent",
    "Tourism Boards", "Travel", "TV Shows", "Video Game", "Video Game Publishers",
    "Wireless and Telecom", "Publishers", "Podcasts", "Other",
    "Manufacturing & Infrastructure",
]

# Allowed dropdown value sets (handy for the Validator tab).
DROPDOWNS = {
    "beauty": {
        "Beauty Type": [
            "Beauty Type - Makeup", "Beauty Type - Skincare", "Beauty Type - Hair",
            "Beauty Type - Fragrance", "Beauty Type - Bath & Body",
            "Beauty Type - Tools & Brushes",
        ],
    },
    "beverages": {
        "Beverage Type": [
            "Beverage Type - Energy Drinks", "Beverage Type - Juices & Shakes",
            "Beverage Type - Water", "Beverage Type - Tea", "Beverage Type - Coffee",
            "Beverage Type - Hard Seltzer/RTD Cocktails", "Beverage Type - Cider",
            "Beverage Type - Spirits", "Beverage Type - Wine/Champagne",
            "Beverage Type - Sports Drinks", "Beverage Type - Beer",
            "Beverage Type - Soft Drinks", "Beverage Type - Dairy/Alternative",
        ],
    },
    "sports": {
        "League": [
            "Major League Baseball", "Major League Soccer",
            "National Basketball Association", "National Football League",
            "National Hockey League", "English Premier League", "Serie A",
            "Bundesliga", "Ligue 1",
        ],
        "Sports Type": [
            "Sports Type - Football", "Sports Type - Baseball",
            "Sports Type - Basketball", "Sports Type - Hockey", "Sports Type - Soccer",
        ],
    },
}


# ---------------------------------------------------------------------------
# PUBLIC: schema detection
# ---------------------------------------------------------------------------

def detect_schema(row: Dict[str, Any]) -> Optional[str]:
    """
    Figure out which of the 4 NEW schemas a row belongs to.

    Works in two modes:
      1. Ingest Template row  -> detected by the presence of signature columns
                                 (e.g. 'Beauty Type 1', 'League'+'Sports Type').
      2. Finished BrandDef row -> detected from the contents of title_sub_category
                                 and brand_set (e.g. 'Beauty Type -', 'LF // Beverages').

    Returns the schema key or None (None => fall through to your existing
    Movies/TV/Talent/Video Game detection).
    """
    # --- mode 1: column signatures (strongest) ---
    if _has_col(row, "Beauty Type 1") or _has_col(row, "Beauty Company"):
        return "beauty"
    if _has_col(row, "Beverage Type") or _has_col(row, "Beverage Company"):
        return "beverages"
    if _has_col(row, "League") and _has_col(row, "Sports Type"):
        return "sports"

    # --- mode 2: BrandDef content signals ---
    sub = _get(row, "title_sub_category", "title sub category").lower()
    bset = _get(row, "brand_set", "brand set", "brand sets").lower()
    cat = _get(row, "title_category", "title category").lower()

    for key in ("beauty", "beverages", "sports"):
        sc = SCHEMAS[key]
        if any(sig in sub for sig in sc["sub_signals"]):
            return key
        if any(sig in bset for sig in sc["brand_set_signals"]):
            return key

    # category itself may already tell us
    if cat == "health & beauty":
        return "beauty"
    if cat == "beverages":
        return "beverages"
    if cat == "sports franchise":
        return "sports"

    # General: has Genres/Ticker but none of the above specialised markers
    if _has_col(row, "Genres") and _has_col(row, "Ticker Symbol"):
        return "general"

    return None


# ---------------------------------------------------------------------------
# PUBLIC: category / sub-category backfill  (the core of the request)
# ---------------------------------------------------------------------------

def fill_category(row: Dict[str, Any]) -> Tuple[str, str]:
    """
    Given a row whose title_category (and/or title_sub_category) may be blank,
    return the (title_category, title_sub_category) it SHOULD have,
    inferred from the template logic.

    - If title_category is already present, it is preserved.
    - Blanks are filled from the detected schema.
    - For General, category cannot be invented (it's user-picked); if it's blank
      the existing value is returned unchanged and you should flag it as a gap.
    """
    existing_cat = _get(row, "title_category", "title category")
    existing_sub = _get(row, "title_sub_category", "title sub category")

    schema_key = detect_schema(row)
    if schema_key is None:
        return existing_cat, existing_sub

    sc = SCHEMAS[schema_key]

    # ---- category ----
    if existing_cat:
        cat = existing_cat
    elif sc["category"] is not None:
        cat = sc["category"]              # fixed for beauty/beverages/sports
    else:
        cat = existing_cat               # General: leave blank -> caller flags gap

    # ---- sub-category ----
    if existing_sub:
        sub = existing_sub
    else:
        # try to rebuild from ingest-type columns if they're present
        sub = sc["sub_from_ingest"](row)

    return cat, sub


# ---------------------------------------------------------------------------
# BONUS: full Ingest Template -> BrandDef row converter (for the Generator tab)
# ---------------------------------------------------------------------------

def build_branddef_row(schema_key: str, ingest_row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert one Ingest Template row into a BrandDef row for the given schema,
    reproducing the workbook formulas in Python. Returns a dict keyed by
    BrandDef column name (only the columns the templates actually populate).
    """
    sc = SCHEMAS[schema_key]
    r = ingest_row
    standard = _is_standard(r)

    # title name column differs across templates
    raw_title = _get(r, "Title", "Title Name")
    title = _dar(raw_title, r)

    # category + sub
    if sc["category"] is not None:
        category = sc["category"]
    else:
        category = _get(r, "Title Category")
    sub = sc["sub_from_ingest"](r)

    # companies
    if schema_key in ("general", "sports"):
        companies = _get(r, "Company") or ("Pristine Brand" if standard else "")
    else:
        companies = "Pristine Brand" if standard else "Unknown"

    # brand_set
    if schema_key in ("general", "sports"):
        brand_set = _get(r, "Brand Sets") or (sc["brand_set_standard"] if standard else "")
    else:
        brand_set = sc["brand_set_standard"] if standard else sc["brand_set_competitive"]

    out: Dict[str, Any] = {
        "title": title,
        "title_category": category,
        "title_sub_category": sub,
        "companies": companies,
        "brand_set": brand_set,
        "active": "t",
        "wikipedia_page": _get(r, "Wikipedia"),
        "twitter_search_terms": _hashtag(raw_title),
    }

    # social handles (column names vary a little per template)
    fb = _get(r, "Facebook Page (URL)", "Facebook")
    tw = _get(r, "Twitter Page (URL)", "Twitter")
    ig = _get(r, "Instagram Account (URL)", "Instagram")
    yt = _get(r, "YouTube (Brand)", "YouTube")
    tk = _get(r, "TikTok")
    tm = _get(r, "Tumblr")
    if fb: out["facebook_page"] = fb
    if tw: out["twitter_handle"] = tw
    if ig: out["instagram_user"] = _clean_instagram(ig)
    if yt: out["youtube_channel_username"] = yt
    if tk: out["tiktok_user"] = tk
    if tm: out["tumblr_page"] = _clean_slashes(tm)

    # schema-specific extras
    if schema_key == "sports":
        out["network"] = _get(r, "League")            # League maps to network
    if schema_key == "general":
        out["genre"] = _get(r, "Genres")
        out["ticker_symbol"] = _get(r, "Ticker Symbol")
        if _get(r, "Rotten Tomatoes"): out["rottentomatoes"] = _get(r, "Rotten Tomatoes")
        if _get(r, "IMDB URL"):        out["imdb_id"] = _get(r, "IMDB URL")
        if _get(r, "Metacritic"):      out["metacritic"] = _get(r, "Metacritic")
        if _get(r, "LinkedIn"):        out["linkedin_page"] = _get(r, "LinkedIn")

    return out


# convenience: expose the full category list your existing detector should know about
ALL_TITLE_CATEGORIES = sorted(set(GENERAL_TITLE_CATEGORIES))


if __name__ == "__main__":
    # tiny smoke test
    tests = [
        ({"Perspective": "Standard", "Title": "Fenty Beauty",
          "Beauty Type 1": "Beauty Type - Makeup", "Beauty Company": "Beauty Company - LVMH"},
         "beauty"),
        ({"Perspective": "Standard", "Title": "Red Bull",
          "Beverage Type": "Beverage Type - Energy Drinks",
          "Beverage Company": "Beverage Company - Red Bull GMBH"}, "beverages"),
        ({"Perspective": "Standard", "Title Name": "LA Lakers",
          "League": "National Basketball Association",
          "Sports Type": "Sports Type - Basketball"}, "sports"),
        # BrandDef row with a MISSING category, inferred from sub_category:
        ({"title": "Some Brand - DAR", "title_category": "",
          "title_sub_category": "Beauty Type - Skincare\nBeauty Company - COTY",
          "brand_set": "LF // Beauty"}, "beauty"),
        ({"Perspective": "Standard", "Title Name": "Acme Corp",
          "Genres": "Tech", "Ticker Symbol": "ACME", "Title Category": "IT, Internet, Computing"},
         "general"),
    ]
    for row, expect in tests:
        got = detect_schema(row)
        cat, sub = fill_category(row)
        flag = "OK " if got == expect else "!! "
        print(f"{flag}detect={got:<10} expect={expect:<10} "
              f"cat={cat!r:<22} sub={sub!r}")
