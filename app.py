from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd
from io import BytesIO
from datetime import datetime
import re
import time
import uuid
import threading
import logging
import os

try:
    from metadata_fetcher import (fetch_metadata, fetch_metadata_by_tt,
                                  fetch_person, fetch_game, fetch_brand,
                                  warm_upcoming)
except Exception:  # keep the app running even if the module is missing
    def fetch_metadata(title, is_movie=True, year_hint=""):
        return {}

    def fetch_metadata_by_tt(tt, is_movie=True, title="", year_hint=""):
        return {}

    def fetch_person(name, qid=None, profession=""):
        return {}

    def fetch_game(name):
        return {}

    def fetch_brand(name):
        return {}

    def warm_upcoming():
        pass

import json
import base64

import types as _types

# Ops ingest templates (reference/*.xlsx) are the AUTHORITATIVE logic source
# for studios/networks/keywords/roll-ups; the inlined tables below remain as
# fallback when the template files are absent.
try:
    import reference_data as TREF
except Exception:
    TREF = None

# Four additional ingest schemas (Beauty / Beverages / Sports Teams / General)
# with category+sub-category auto-detection and dropdown validation rules.
# Fails soft: if the modules/JSON are missing the app behaves exactly as before.
try:
    import os as _os
    from titleforge_ingest_ext import (detect_schema as _tfx_detect,
                                       fill_category as _tfx_fill,
                                       build_branddef_row as _tfx_build,
                                       BRANDDEF_COLUMNS as _TFX_COLUMNS,
                                       SCHEMAS as _TFX_SCHEMAS,
                                       GENERAL_TITLE_CATEGORIES as _TFX_MASTER)
    from titleforge_validator import (load_rules as _tfx_load_rules,
                                      validate_row as _tfx_validate)
    _TFX_RULES = _tfx_load_rules(_os.path.join(
        _os.path.dirname(_os.path.abspath(__file__)),
        'titleforge_validation_rules.json'))
    TFX_OK = True
except Exception as _e:  # pragma: no cover
    logging.warning(f"titleforge ingest extension unavailable: {_e}")
    TFX_OK = False
    _TFX_MASTER = []
    _TFX_RULES = {}
    _TFX_COLUMNS = {}
    _TFX_SCHEMAS = {}


def _tref():
    return TREF if (TREF is not None and getattr(TREF, "LOADED", False)) else None

# ---- Reference tables (inlined so no separate file can be missed on deploy) ----
# distributor (raw from BOM/Wikipedia/Wikidata/IMDb/TMDB) -> LF network label
_NETWORK_LABEL = {
    "lionsgate": "Lionsgate / Summit", "summit entertainment": "Lionsgate / Summit",
    "lionsgate films": "Lionsgate / Summit", "lionsgate premiere": "Lionsgate / Summit",
    "columbia pictures": "Sony / Columbia",
    "sony pictures releasing": "Sony / Columbia", "sony pictures": "Sony / Columbia",
    "sony pictures entertainment": "Sony / Columbia", "sony pictures classics": "Sony Classics",
    "20th century fox": "20th Century Studios", "20th century studios": "20th Century Studios",
    "walt disney studios motion pictures": "Disney", "walt disney pictures": "Disney",
    "amazon mgm studios": "Amazon MGM Studios", "amazon studios": "Amazon MGM Studios",
    "warner bros.": "Warner Bros.", "warner bros. pictures": "Warner Bros.",
    "warner bros. discovery": "Warner Bros.",
    "neon rated": "Neon",
    "pbs": "PBS network", "public broadcasting service": "PBS network",
    "pbs distribution": "PBS network",
    "cineverse entertainment": "Cineverse", "cineverse corp.": "Cineverse",
}
_NETWORK_TO_COMPANIES = {
    "20th Century Studios": "Walt Disney Pictures", "Amazon MGM Studios": "Amazon Studios",
    "Disney": "Walt Disney Pictures", "Lionsgate / Summit": "Lionsgate", "Neon": "Neon",
    "Sony / Columbia": "Sony Pictures", "Sony Classics": "Sony Pictures",
    "Warner Bros.": "Warner Bros. Pictures",
}
_NETWORK_TO_YOUTUBE = {
    "20th Century Studios": "http://www.youtube.com/user/FoxMovies",
    "Amazon MGM Studios": "http://www.youtube.com/channel/UCf5CjDJvsFvtVIhkfmKAwAA",
    "Atlas Distribution": "http://www.youtube.com/channel/UCMLA_XtSbnfjXHL2An8zfGg",
    "Aura Entertainment": "http://www.youtube.com/@AuraEntFilms",
    "Big World Pictures": "http://www.youtube.com/channel/UCx1mHWMsCO96ungWSwS5Udg",
    "Blue Fox": "http://www.youtube.com/channel/UCmHYPCM_h8Tw9JkI3UnrCvA",
    "Cineverse": "http://www.youtube.com/@cineverse_ent",
    "Dark Sky Films": "http://www.youtube.com/user/dsf2006",
    "Disney": "http://www.youtube.com/@pixar",
    "Fathom Events": "http://www.youtube.com/user/FathomEvents",
    "Fin & Fur Films": "http://www.youtube.com/@finfurfilms/videos",
    "GKIDS": "http://www.youtube.com/user/GKIDStv",
    "Giant Pictures": "http://www.youtube.com/@GiantPictures",
    "Greenwich Entertainment": "http://www.youtube.com/channel/UCLFmfzQaJE_YlgXtnkr3e_Q",
    "IFC Films": "http://www.youtube.com/user/IFCFilmsTube",
    "Iconic Events": "http://www.youtube.com/@iconicreleasing",
    "Independent Film Company": "http://www.youtube.com/@IndependentFilmCompany",
    "Indican Pictures": "http://www.youtube.com/user/IndicanPictures",
    "Janus Films": "http://www.youtube.com/user/janusfilmsnyc",
    "Kani Releasing": "http://www.youtube.com/@kani-releasing",
    "Kino Lorber": "http://www.youtube.com/user/kinolorber",
    "Lionsgate / Summit": "http://www.youtube.com/user/LionsgateLIVE",
    "MUBI": "http://www.youtube.com/@mubi",
    "Magnolia": "http://www.youtube.com/user/MagnoliaPictures",
    "Neon": "http://www.youtube.com/channel/UCpy5dRhZd-JbZP4NsrnLt1w",
    "Oscilloscope Pictures": "http://www.youtube.com/user/oscopelabs",
    "PBS network": "http://www.youtube.com/@PBS",
    "Persimmon": "http://www.youtube.com/@persimmonpresents",
    "Roadside Attractions": "http://www.youtube.com/user/RoadsideFlix",
    "Row K Entertainment": "http://youtube.com/@rowkpresents",
    "Sandbox Films": "http://www.youtube.com/@sandboxdocs",
    "Sony / Columbia": "http://www.youtube.com/@sonypictures",
    "Sony Classics": "http://www.youtube.com/user/SonyPicturesClassics",
    "Strand Releasing": "http://www.youtube.com/user/StrandReleasing",
    "Sumerian Pictures": "http://www.youtube.com/@SumerianRecords",
    "Trafalgar Releasing": "http://www.youtube.com/channel/UC_0NZhyl9KH0aMWXRnAKM4g",
    "Warner Bros.": "http://www.youtube.com/@WarnerBros",
    "Watermelon Pictures": "http://www.youtube.com/@watermelonpicturesco",
    "Well Go USA": "http://www.youtube.com/user/wellgousa",
}
_NETWORK_TO_MANAGER = {
    "20th Century Studios": "Disney Insights & Analytics + Disney Theatrical Research + Disney Ad Sales",
    "Disney": "Disney Insights & Analytics + Disney Theatrical Research + Disney Ad Sales",
    "Amazon MGM Studios": "Amazon PV Enterprise", "Lionsgate / Summit": "Lionsgate",
    "Neon": "Neon", "Sony / Columbia": "Sony Enterprise", "Sony Classics": "Sony Enterprise",
    "Warner Bros.": "Warner Bros.",
}
_NETWORK_TO_SUBCATEGORY = {
    "Disney": "Release - Wide\nStudio - Major",
    "Warner Bros.": "Release - Wide\nStudio - Major",
    "Sony / Columbia": "Release - Wide\nStudio - Independent",
    "Amazon MGM Studios": "Release - Wide\nStudio - Independent",
    "AMC Network": "Release - Wide\nStudio - Independent",
    "Neon": "Release - Wide\nStudio - Independent",
    "Cineverse": "Release - Wide\nStudio - Independent",
}
# extra brand_set lines a DAR row carries when its network's parent company
# has corporate roll-ups (learned from the manual file)
_DAR_ROLLUPS = {
    "Walt Disney Pictures": ("The Walt Disney Company > Film Roll-up\n"
                             "The Walt Disney Company > Film + TV + Publishing Roll-up\n"
                             "The Walt Disney Company > Overall Roll-up"),
    "Warner Bros. Pictures": ("Warner Bros. Pictures Films\n"
                              "WarnerMedia > Film Roll-up\n"
                              "WarnerMedia > Film + TV + Publishing Roll-up\n"
                              "WarnerMedia > Overall Roll-up"),
}
_GENRE_FIX = {"Sci-Fi": "Sci Fi", "Science Fiction": "Sci Fi", "Film-Noir": "Film Noir", "Rom-Com": "Romance"}


def _ref_ci_get(mapping, key):
    if key is None:
        return None
    k = str(key).strip()
    if k in mapping:
        return mapping[k]
    kl = k.lower()
    for mk, mv in mapping.items():
        if mk.lower() == kl:
            return mv
    return None


def _ref_normalize_network(raw):
    if not raw:
        return raw
    return _ref_ci_get(_NETWORK_LABEL, raw) or str(raw).strip()


def _ref_normalize_genres(genre_multiline):
    if not genre_multiline:
        return genre_multiline, ""
    parts = [p.strip() for p in str(genre_multiline).split("\n") if p.strip()]
    fixed = [_GENRE_FIX.get(p, p) for p in parts]
    seen = set()
    uniq = [g for g in fixed if not (g in seen or seen.add(g))]
    return "\n".join(uniq), (uniq[0] if uniq else "")


REF = _types.SimpleNamespace(
    NETWORK_TO_MANAGER=_NETWORK_TO_MANAGER,
    normalize_network=_ref_normalize_network,
    companies_for=lambda n: _ref_ci_get(_NETWORK_TO_COMPANIES, n) or "",
    youtube_for=lambda n: _ref_ci_get(_NETWORK_TO_YOUTUBE, n) or "",
    subcategory_for=lambda n: _ref_ci_get(_NETWORK_TO_SUBCATEGORY, n) or "",
    dar_rollup_for=lambda c: _ref_ci_get(_DAR_ROLLUPS, c) or "",
    normalize_genres=_ref_normalize_genres,
)

try:
    from validator import validate_workbook, DEFAULT_RULES
except Exception:  # validator is optional; page still loads
    validate_workbook = None
    DEFAULT_RULES = {"rules": []}

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
logging.basicConfig(level=logging.INFO)

# Define ALL 42 columns in EXACT order (matches Test_Run.xlsx: A -> AP)
COLUMNS = [
    'record_type', 'brand_id', 'title', 'title_created_date', 'title_category',
    'title_sub_category', 'genre', 'primary_genre', 'iso_mic', 'stock_exchange',
    'ticker_symbol', 'companies', 'brand_set', 'composite_brand_set', 'active',
    'released_on', 'domestic_opening_weekend_box_office', 'domestic_opening_weekend_screens',
    'domestic_opening_weekend_rank', 'street_date', 'network', 'facebook_page',
    'facebook_verified', 'twitter_handle', 'twitter_verified', 'instagram_user',
    'youtube_channel_username', 'youtube_channel_company', 'tiktok_user', 'linkedin_page',
    'threads_page', 'pinterest_user_username', 'pinterest_board', 'wikipedia_page',
    'rottentomatoes', 'imdb_id', 'metacritic',
    'twitter_search_terms', 'instagram_business_hashtags', 'twitter_search_term_keywords',
    'url_managers', 'last_reviewed'
]

# Social-media / metadata columns that identify a "full schema" upload
SOCIAL_COLUMNS = [
    'facebook_page', 'facebook_verified', 'twitter_handle', 'twitter_verified',
    'instagram_user', 'youtube_channel_username', 'youtube_channel_company',
    'tiktok_user', 'linkedin_page', 'threads_page', 'pinterest_user_username',
    'pinterest_board',
]

# Fixed keyword tail used in twitter_search_term_keywords (derived from Test_Run)
_KEYWORDS = ('"all new" or episode or watch or tv or show or series or season or '
             'binge or stream or film or movie or premiere or screening or feature '
             'or trailer or teaser or theater or release')


def _alnum(s):
    """Lowercase and strip everything except letters/digits (for hashtags).
    Accented characters are transliterated (é -> e) rather than dropped, so
    brand names like L'Oréal hash to 'loreal' instead of 'loral'."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode(
        "ascii", "ignore").decode("ascii")
    return re.sub(r'[^a-z0-9]', '', s.lower())


def _title_variants(clean_title):
    """Lowercase title variants used in youtube_channel_username lines.
    Titles with a colon get TWO lines: punctuation-stripped first, then the
    original (matches the manual Ops format, e.g. the PBS documentary case)."""
    tl = clean_title.lower()
    stripped = re.sub(r'\s*:\s*', ' ', tl)
    stripped = re.sub(r'\s+', ' ', stripped).strip()
    return [stripped, tl] if stripped != tl else [tl]


def build_youtube_username(company_channel, clean_title, own_channel=""):
    """youtube_channel_username lines:
      - the title's OWN channel URL(s) first, one per line
      - then '<network channel>|<title variant>' for each title variant

    A title may legitimately have more than one channel of its own (a franchise
    or regional channel alongside the main one), so ``own_channel`` accepts a
    newline-separated list: they share the one cell, one per row. Duplicates and
    the distributor's own channel are dropped.
    """
    lines = []
    company = (company_channel or "").strip()
    for own in str(own_channel or "").splitlines():
        own = own.strip()
        if own and own != company and own not in lines:
            lines.append(own)
    if company_channel:
        lines.extend(f"{company_channel}|{v}" for v in _title_variants(clean_title))
    return "\n".join(lines)


def generate_search_terms(clean_title, network, year, is_dar, twitter_handle="",
                          network_clause=""):
    """Generate twitter_search_terms (AL) and twitter_search_term_keywords (AN).
    When the title has its own @handle, it gets its own leading lines
    (matching the manual Ops pattern)."""
    label = "DAR" if is_dar else "Operations - Core Title"
    t_hash = _alnum(clean_title)
    n_hash = _alnum(network)

    # twitter_search_terms
    lines = []
    handle = (twitter_handle or "").strip().lstrip("@").lower()
    if handle:
        if is_dar:
            lines.append(f"@{handle}|DAR|DAR")
        else:
            lines.append(f"@{handle}|TV Ops|TV Ops")
            lines.append(f"@{handle}|Film Ops|Film Ops")
            lines.append(f"@{handle}|Operations - Core Title|Operations - Core Title")
    lines.append(f"#{t_hash}|{label}|{label}")
    if n_hash:
        lines.append(f"#{t_hash}{n_hash}|{label}|{label}")
    terms = "\n".join(lines)

    # twitter_search_term_keywords -- the per-studio clause from the ingest
    # template wins (lowercased, matching the export format); else generic
    inner = []
    if network_clause:
        inner.append(network_clause.lower())
    elif network:
        inner.append(f'"{network.lower()}" or @{n_hash} or #{n_hash}')
    if year:
        inner.append(f'"{year}"')
    inner.append(_KEYWORDS)
    clause = "(" + " or ".join(inner) + ")"
    kw = f'("{clean_title.lower()}") {clause}|{label}|{label}'
    if is_dar:
        kw += "|2021-01-01"
    return terms, kw


# network -> url_managers team (from reference_data, learned from the manual file).
URL_MANAGER_MAP = {}
if REF is not None:
    URL_MANAGER_MAP = {k.lower(): v for k, v in REF.NETWORK_TO_MANAGER.items()}


def _first_line(v):
    if not v:
        return ""
    return str(v).split("\n")[0].strip()


def _resolve_manager(row):
    for key in (row.get("network"), row.get("companies")):
        m = URL_MANAGER_MAP.get((str(key) or "").strip().lower())
        if m:
            return m
    return ""


# companies values for which url_managers is NOT generated
URL_MANAGER_SKIP_COMPANIES = {"unknown", "pristine brand"}


def generate_url_managers(row):
    """Build url_managers: one 'platform|value|manager' line per social present.
    Skips titles whose companies is 'Unknown' or 'Pristine Brand'.
    Returns '' if skipped or if no manager is resolvable.
    """
    companies = (str(row.get("companies")) or "").strip().lower()
    if companies in URL_MANAGER_SKIP_COMPANIES:
        return ""
    manager = _resolve_manager(row)
    if not manager:
        return ""
    entries = []
    fb = row.get("facebook_page"); ig = row.get("instagram_user")
    yt = _first_line(row.get("youtube_channel_company"))
    tk = row.get("tiktok_user"); tw = row.get("twitter_handle")
    if fb:
        entries.append(f"facebook|{fb}|{manager}")
    if ig:
        entries.append(f"instagram|{ig}|{manager}")
    if yt:
        entries.append(f"youtube|{yt}|{manager}")
    if tk:
        entries.append(f"tiktok|{tk}|{manager}")
    if tw:
        entries.append(f"twitter|http://twitter.com/{tw}|{manager}")
    return "\n".join(entries)


def _norm_bool(v):
    """Normalise a truthiness/string into the lowercase 'true'/'false' the feed expects."""
    if isinstance(v, bool):
        return "true" if v else "false"
    sv = str(v).strip().lower()
    if sv in ("false", "0", "no", "n", ""):
        return "false"
    return "true"


# ======================= TV Shows (BrandIngest schema) =======================
# TV rows export in the 39-column ApplyBrandDefinitionReport / BrandIngest
# format (learned from the manual Ops file), NOT the movie schema.
TV_COLUMNS = [
    'record_type', 'brand_id', 'title', 'title_category', 'title_sub_category',
    'genre', 'primary_genre', 'rovi_id', 'ticker_symbol', 'title_content_windows',
    'companies', 'brand_set', 'active', 'released_on', 'box_office', 'street_date',
    'gross_screen', 'opening_weekend_box_office', 'network', 'brand_listing_hidden',
    'facebook_page', 'twitter_handle', 'instagram_user', 'youtube_channel_username',
    'tiktok_user', 'tumblr_page', 'pinterest_user_username', 'pinterest_board',
    'wikipedia_page', 'rottentomatoes', 'imdb_id', 'metacritic',
    'facebook_search_terms', 'twitter_search_terms', 'instagram_search_terms',
    'tumblr_search_terms', 'twitter_search_term_keywords', 'youtube_search_terms',
    'reddit_search_terms',
]

# corporate roll-up brand_set blocks (appended to DAR rows)
_DISNEY_TV = ("The Walt Disney Company > Overall Roll-up\n"
              "The Walt Disney Company > TV Roll-up\n"
              "The Walt Disney Company > TV {kind} Roll-up\n"
              "The Walt Disney Company > TV + Publishing Roll-up\n"
              "The Walt Disney Company > Film + TV + Publishing Roll-up")
_NBCU_TV = ("NBCUniversal > Overall Roll-up\nNBCUniversal > TV Roll-up\n"
            "NBCUniversal > TV {kind} Roll-up\nNBCUniversal > TV + Publishing Roll-up\n"
            "NBCUniversal > Film + TV + Publishing Roll-up")

# per-network reference: ticker, parent company, YouTube channel(s), network
# tier, twitter keyword clause, corporate roll-ups, extra DAR brand sets
_TV_NETWORK = {
    "Netflix": dict(
        ticker="NFLX", companies="Netflix",
        yt=["https://www.youtube.com/user/NewOnNetflix"], tier="Streaming",
        clause='"Netflix" OR @netflix OR #Netflix OR #NowonNetflix',
        extras="Netflix - Emerging Titles\nPV Monthly - Emerging Titles"),
    "Paramount+": dict(
        ticker="VIA", companies="Viacom",
        yt=["https://www.youtube.com/channel/UCrRttZIypNTA1Mrfwo745Sg"], tier="Streaming",
        clause='"Paramount+" OR "Paramount Plus" OR @paramountplus OR #ParamountPlus',
        extras="Paramount+ - Emerging Titles\nPV Monthly - Emerging Titles"),
    "Amazon Prime Video": dict(
        ticker="AMZN", companies="Amazon Prime Video",
        yt=["https://www.youtube.com/user/amazonstudios",
            "https://www.youtube.com/@AmazonMGMStudios"], tier="Streaming",
        clause=('"prime video" or "amazon studios" or "amazon mgm studios" or '
                '@primevideo or @amazonmgmstudio or @primemovies OR #primevideo '
                'or #amazonmgmstudios or #primemovies or #amazonstudios'),
        extras=("Amazon Prime Video TV Network\nAmazon Prime Video - Emerging Titles\n"
                "PV Monthly - Emerging Titles")),
    "NBC": dict(
        ticker="CMCSA", companies="NBCU Research - Entertainment Networks",
        yt=["https://www.youtube.com/user/NBC"], tier="Broadcast",
        clause='"NBC" OR @nbc OR #NBC OR #NBCNetwork',
        corp=_NBCU_TV.format(kind="Broadcast")),
    "ABC": dict(
        ticker="DIS", companies="American Broadcasting Company",
        yt=["https://www.youtube.com/user/ABCNetwork"], tier="Broadcast",
        clause='"ABC" OR @ABCNetwork OR #ABC OR #ABCNetwork',
        corp=_DISNEY_TV.format(kind="Broadcast")),
    "National Geographic": dict(
        ticker="DIS", companies="National Geographic",
        yt=["https://www.youtube.com/user/NationalGeographic"], tier="Ad Supported Cable",
        clause=('"National Geographic Channel" OR "Nat Geo" OR "NatGeo" OR @NatGeoTV '
                'OR #NationalGeographicChannel OR #NatGeoTV OR #NatGeo'),
        corp=_DISNEY_TV.format(kind="Cable")),
    "FX": dict(
        ticker="DIS", companies="FX Network",
        yt=["https://www.youtube.com/user/FXNetworks"], tier="Ad Supported Cable",
        clause='"FX" OR @FXNetworks OR #FX OR #FXNetwork',
        corp=_DISNEY_TV.format(kind="Cable"), extras="FX All Brands Roll-Up"),
    "Bravo": dict(
        ticker="CMCSA", companies="Bravo!",
        yt=["https://www.youtube.com/user/VideoByBravo"], tier="Ad Supported Cable",
        clause='"Bravo" OR @BravoTV OR #BravoTV OR #Bravo',
        corp=_NBCU_TV.format(kind="Cable")),
    "Adult Swim": dict(
        ticker="T", companies="Adult Swim",
        yt=["https://www.youtube.com/user/adultswim"], tier="Ad Supported Cable",
        daypart="Other",  # late-night network
        clause='"Adult Swim" OR @adultswim OR #AdultSwim',
        corp=("WarnerMedia > Overall Roll-up\nWarnerMedia > TV Roll-up\n"
              "WarnerMedia > TV Cable Roll-up\nWarnerMedia > TV + Publishing Roll-up\n"
              "WarnerMedia > Film + TV + Publishing Roll-up\nAT&T Overall Roll-Up")),
    "Food Network": dict(
        ticker="DISCB", companies="Unknown",
        yt=["https://www.youtube.com/user/FoodNetworkTV"], tier="Ad Supported Cable",
        clause='"Food Network" OR @FoodNetwork OR #foodnetwork',
        corp=("Discovery > TV Roll-up\nDiscovery > TV + Publishing Roll-up\n"
              "Discovery > Film + TV + Publishing Roll-up")),
    "History": dict(
        ticker="", companies="A&E Television Networks",
        yt=["https://www.youtube.com/user/historychannel"], tier="Ad Supported Cable",
        clause=('"History Channel" OR "History Network" OR @HISTORY OR '
                '#historychannel OR #historytv'),
        corp=("A+E > TV Roll-up\nA+E > TV + Publishing Roll-up\n"
              "A+E > Film + TV + Publishing Roll-up")),
    "BritBox": dict(
        ticker="", companies="Unknown",
        yt=["https://www.youtube.com/channel/UC0yD7rYO26CAbkOx4rJkCzg"], tier="Streaming",
        clause='"BritBox" OR #BritBox OR @BritBox_US'),
    "Tubi": dict(
        ticker="", companies="Unknown",
        yt=["https://www.youtube.com/channel/UCNDsk0uhSlG1-br1Ex2Rgfg"], tier="Streaming",
        clause='"Tubi" OR #Tubi OR @tubi'),
    "Shudder": dict(
        ticker="", companies="Unknown",
        yt=["https://www.youtube.com/channel/UCcCCIrXmIJOYamxWqeJzI2Q"], tier="Streaming",
        clause='"Shudder" OR @Shudder OR #Shudder', extras="Shudder TV Network"),
    "Starz": dict(
        ticker="LGF.A", companies="Starz Entertainment",
        yt=["https://www.youtube.com/user/Starz"], tier="Premium Cable",
        clause='"Starz" OR @STARZ OR #Starz OR #StarzNetwork OR #StarzTV'),
    "Great American Family": dict(
        ticker="", companies="Unknown",
        yt=["https://www.youtube.com/channel/UCjIRUzJ-6-4nyX3GIsfwOOg"],
        tier="Ad Supported Cable",
        clause=('"Great American Family" OR "on GAF" OR @GAfamilyTV OR '
                '#greatamericanfamilychannel OR #GAFTV')),
}

_TV_KEYWORD_TAIL = ('"All New" OR Episode OR Watch OR tv OR Show OR Series OR season '
                    'OR binge OR Stream OR Film OR Movie OR Premiere OR Screening OR '
                    'Feature OR Trailer OR Teaser OR theater OR release')

# unscripted genres win primary_genre, in this priority order; otherwise
# scripted shows are bucketed Drama unless they are Comedy-without-Drama
_TV_UNSCRIPTED_PRIORITY = ["Game Show", "Reality", "Sport", "Documentary",
                           "Talk Show", "News"]


def _tv_net(network):
    return _ref_ci_get(_TV_NETWORK, network) or {}


def _tv_primary_genre(genres):
    for g in _TV_UNSCRIPTED_PRIORITY:
        if g in genres:
            return g
    if "Comedy" in genres and "Drama" not in genres:
        return "Comedy"
    return "Drama" if genres else ""


def _camel(s):
    """Hashtag form: keep case, '+' -> 'plus', drop everything non-alnum."""
    return re.sub(r'[^A-Za-z0-9]', '', str(s or '').replace('+', 'plus'))


def _https(u):
    return re.sub(r'^http://', 'https://', str(u or ''))


def _strip_disambiguator(title):
    """'Steps (Netflix)' -> 'Steps' (used in YouTube username lines)."""
    return re.sub(r'\s*\([^)]*\)\s*$', '', title).strip()


def _tv_youtube_username(channels, clean_title):
    """One '<channel>|<Title>' line per network channel; titles containing a
    colon get a second pass with the colon removed (matches the manual file:
    colon variant lines come AFTER the original lines)."""
    t = _strip_disambiguator(clean_title)
    variants = [t]
    stripped = re.sub(r'\s*:\s*', ' ', t)
    stripped = re.sub(r'\s+', ' ', stripped).strip()
    if stripped != t:
        variants.append(stripped)
    return "\n".join(f"{ch}|{v}" for v in variants for ch in channels)


def _tv_search_terms(title, network, is_dar):
    label = "DAR" if is_dar else "TV Ops"
    # base rows hashtag the disambiguator-stripped title; DAR rows keep the
    # full title (both patterns are consistent in the manual Ops file)
    t = _camel(title if is_dar else _strip_disambiguator(title))
    n = _camel(network)
    lines = [f"#{t}|{label}"]
    if n:
        lines.append(f"#{t}{n}|{label}")
    return "\n".join(lines)


def _tv_keywords_and_reddit(title, network, year, program_type, is_dar,
                            clause="", reddit_clause=""):
    """twitter_search_term_keywords + reddit_search_terms.
    Base rows of Specials use the short 'tonight' pattern; DAR rows and
    everything else use the full pattern (per the manual Ops file).
    `clause`/`reddit_clause` come from the ingest template when available."""
    info = _tv_net(network)
    title = _strip_disambiguator(title)
    tail = "DAR|DAR|2021-01-01" if is_dar else "Operations - Core Title|Operations - Core Title"
    if program_type == "Special" and not is_dar:
        inner = f'tonight OR watch OR tv OR show OR program OR "{network}"'
        kw = f'("{title}")({inner})|{tail}'
        r_inner = re.sub(r'\s+(?:OR|or)\s+', ' | ', inner)
    else:
        clause = clause or info.get("clause")
        if not clause and network:
            clause = f'"{network}" OR @{_camel(network).lower()} OR #{_camel(network)}'
        parts = [p for p in (clause, f'"{year}"' if year else "", _TV_KEYWORD_TAIL) if p]
        inner = " OR ".join(parts)
        kw = f'("{title}") ({inner})|{tail}'
        # reddit: template's ready-made ' | ' clause when present, else transform
        r_clause = reddit_clause or re.sub(r'\s+(?:OR|or)\s+', ' | ', clause or '')
        r_parts = [p for p in (r_clause, f'"{year}"' if year else "",
                               re.sub(r'\s+(?:OR|or)\s+', ' | ', _TV_KEYWORD_TAIL)) if p]
        r_inner = " | ".join(r_parts)
    reddit = f'("{title.replace("+", " ")}") ({r_inner.replace("+", " ")})'
    if is_dar:
        reddit += "|2021-01-01"
    return kw, reddit


def create_tv_row(title, network="", metadata=None):
    """Create a TV Shows row in the 39-column BrandIngest schema.
    Values present in `metadata` always win over computed defaults."""
    metadata = metadata or {}
    is_dar = " - DAR" in title
    clean_title = re.sub(r"\s*-\s*DAR\s*$", "", title, flags=re.IGNORECASE).strip()

    eff_network = (str(metadata.get('network') or network or '')).strip()
    info = _tv_net(eff_network)
    # network record from the Ops ingest template (authoritative when present)
    tinfo = _tref().tv_network(eff_network) if (_tref() and eff_network) else None

    # ---- title_sub_category (4 lines) ----
    ptype = str(metadata.get('program_type') or '').strip() or "Series"
    tier = ""
    if tinfo and tinfo.get("network_type"):
        tier = re.sub(r'^Network - ', '', tinfo["network_type"]).strip()
    tier = tier or info.get("tier") or "Streaming"
    daypart = info.get("daypart") or (
        "Prime Time" if tier in ("Broadcast", "Ad Supported Cable") else "Other")
    lang = str(metadata.get('original_language') or 'en').strip().lower()
    lang_line = "English" if lang in ("en", "english", "") else "Other"
    _sub = (f"Daypart - {daypart}\nProgram Type - {ptype}\n"
            f"Language Type - {lang_line}\nNetwork - {tier}")

    # ---- companies / ticker / brand_set ----
    if is_dar:
        companies = "Pristine Brand"
        n = eff_network
        if ptype in ("Series", "Mini-Series"):
            brand_set = (f"{n} -- Episodic + Roll-Up\n{n}-- Episodic Network Roll-Up\n"
                         "LF // TV Universe\nLF // TV // Episodic\n"
                         "LF // TV // Episodic Plus\nPristine DAR Brands")
        else:  # Special / TV Movie
            film_line = "LF // Film - Majors + Independents\n" if ptype == "TV Movie" else ""
            brand_set = ("LF // TV Universe\nLF // TV // Episodic Plus\n"
                         f"{n} -- Episodic + Roll-Up\n{film_line}Pristine DAR Brands")
        # conglomerate roll-up block from the ingest template; inline fallback
        conglom = (_tref().tv_conglomerate(eff_network) if _tref() else "") or \
            "\n".join(x for x in (info.get("corp"), info.get("extras")) if x)
        if conglom:
            brand_set += "\n" + conglom
    else:
        companies = (tinfo.get("company") if tinfo else "") or \
            info.get("companies") or "Unknown"
        brand_set = "Competitive View"

    # ---- genre / primary ----
    _genre = metadata.get('genre', '')
    if REF is not None and _genre:
        _genre, _ = REF.normalize_genres(_genre)
    _primary = str(metadata.get('primary_genre') or '').strip()
    if not _primary:
        genres_list = [g for g in str(_genre).split("\n") if g]
        # the template's Order-of-Operations mapping is authoritative
        _primary = (_tref().tv_primary_genre(genres_list) if _tref() else "") or \
            _tv_primary_genre(genres_list)

    # ---- youtube / search terms ----
    channels = list(info.get("yt") or [])
    if tinfo and tinfo.get("youtube"):
        # template channel wins; the cell may hold several channels
        # (newline-separated, e.g. Amazon Prime Video)
        channels = [c.strip() for c in str(tinfo["youtube"]).split("\n") if c.strip()]
    _yt = str(metadata.get('youtube_channel_username') or '').strip()
    if not _yt and channels:
        _yt = _tv_youtube_username(channels, clean_title)
    rel = str(metadata.get('released_on') or '')
    year = rel[:4] if rel[:4].isdigit() else ''
    gen_terms = _tv_search_terms(clean_title, eff_network, is_dar)
    gen_kw, gen_reddit = _tv_keywords_and_reddit(
        clean_title, eff_network, year, ptype, is_dar,
        clause=(tinfo.get("twitter_clause") if tinfo else ""),
        reddit_clause=(tinfo.get("reddit_clause") if tinfo else ""))

    def mv(key, default=''):
        v = metadata.get(key, '')
        return v if v not in (None, '') else default

    row = {
        'record_type': mv('record_type', 'INGESTED'),
        'brand_id': metadata.get('brand_id', ''),
        'title': title,
        'title_category': 'TV Shows',
        'title_sub_category': mv('title_sub_category', _sub),
        'genre': _genre,
        'primary_genre': _primary,
        'rovi_id': metadata.get('rovi_id', ''),
        'ticker_symbol': mv('ticker_symbol',
                            (tinfo.get("ticker") if tinfo else "") or info.get("ticker", "")),
        'title_content_windows': metadata.get('title_content_windows', ''),
        'companies': mv('companies', companies),
        'brand_set': mv('brand_set', brand_set),
        'active': mv('active', 't'),
        'released_on': metadata.get('released_on', ''),
        'box_office': metadata.get('box_office', ''),
        'street_date': metadata.get('street_date', ''),
        'gross_screen': metadata.get('gross_screen', ''),
        'opening_weekend_box_office': metadata.get('opening_weekend_box_office', ''),
        'network': eff_network,
        'brand_listing_hidden': mv('brand_listing_hidden', 'f'),
        # per the manual file, per-title social handles stay blank for TV
        # (coverage runs through the network accounts)
        'facebook_page': metadata.get('facebook_page', ''),
        'twitter_handle': metadata.get('twitter_handle', ''),
        'instagram_user': metadata.get('instagram_user', ''),
        'youtube_channel_username': _yt,
        'tiktok_user': metadata.get('tiktok_user', ''),
        'tumblr_page': metadata.get('tumblr_page', ''),
        'pinterest_user_username': metadata.get('pinterest_user_username', ''),
        'pinterest_board': metadata.get('pinterest_board', ''),
        'wikipedia_page': _https(metadata.get('wikipedia_page', '')),
        'rottentomatoes': _https(metadata.get('rottentomatoes', '')),
        'imdb_id': _https(metadata.get('imdb_id', '')),
        'metacritic': _https(metadata.get('metacritic', '')),
        'facebook_search_terms': metadata.get('facebook_search_terms', ''),
        'twitter_search_terms': mv('twitter_search_terms', gen_terms),
        'instagram_search_terms': metadata.get('instagram_search_terms', ''),
        'tumblr_search_terms': metadata.get('tumblr_search_terms', ''),
        'twitter_search_term_keywords': mv('twitter_search_term_keywords', gen_kw),
        'youtube_search_terms': metadata.get('youtube_search_terms', ''),
        'reddit_search_terms': mv('reddit_search_terms', gen_reddit),
    }
    return row


# ======================= Talent (BrandDef schema) =======================
# Talent rows export in the 38-column BrandDef format (from the Talent ingest
# template): ONE row per person, title suffixed ' - DAR', companies 'Pristine
# Brand', brand_set 'LF // Talent\nPristine DAR Brands', a single
# '#name|DAR|DAR' twitter search term, and a 3-line sub-category
# (Talent Subtype / Gender / Talent Type).
TALENT_COLUMNS = [
    'brand_id', 'title', 'title_category', 'title_sub_category', 'genre',
    'primary_genre', 'rovi_id', 'ticker_symbol', 'title_content_windows',
    'companies', 'brand_set', 'active', 'released_on', 'box_office',
    'street_date', 'gross_screen', 'opening_weekend_box_office', 'network',
    'facebook_page', 'twitter_handle', 'instagram_user',
    'youtube_channel_username', 'tiktok_user', 'linkedin_page', 'tumblr_page',
    'pinterest_user_username', 'pinterest_board', 'wikipedia_page',
    'rottentomatoes', 'imdb_id', 'metacritic', 'facebook_search_terms',
    'twitter_search_terms', 'instagram_search_terms', 'tumblr_search_terms',
    'twitter_search_term_keywords', 'youtube_search_terms', 'url_managers',
]

TALENT_DEFAULT_BRAND_SET = "LF // Talent\nPristine DAR Brands"

# discovered occupation keyword -> (Talent Type, subtype kind, subtype term)
# checked in order; 'Actor' becomes 'Actress' for Gender - Woman
_TALENT_OCC_MAP = [
    ('television presenter', 'Media Personality', 'Media Personality', 'TV'),
    ('television host', 'Media Personality', 'Media Personality', 'TV'),
    ('radio personality', 'Media Personality', 'Media Personality', 'Radio'),
    ('radio host', 'Media Personality', 'Media Personality', 'Radio'),
    ('podcaster', 'Media Personality', 'Media Personality', 'Podcaster'),
    ('youtuber', 'Internet Personality', 'Internet Personality', 'Content Creator'),
    ('internet celebrity', 'Internet Personality', 'Internet Personality', 'Influencer'),
    ('influencer', 'Internet Personality', 'Internet Personality', 'Influencer'),
    ('streamer', 'Internet Personality', 'Internet Personality', 'Streamer'),
    ('rapper', 'Musician', 'Musician', 'Rapper'),
    ('singer', 'Musician', 'Musician', 'Singer'),
    ('composer', 'Musician', 'Musician', 'Composer'),
    ('disc jockey', 'Musician', 'Musician', 'DJ / Producer'),
    ('record producer', 'Musician', 'Musician', 'DJ / Producer'),
    ('musician', 'Musician', '', ''),
    ('actor', 'Actor', '', ''),
    ('film director', 'Director', '', ''),
    ('director', 'Director', '', ''),
    ('comedian', 'Comedian', '', ''),
    ('politician', 'Politician', '', ''),
    ('journalist', 'Journalist', '', ''),
    ('chef', 'Chef', '', ''),
    ('model', 'Model', '', ''),
    ('dancer', 'Dancer', '', ''),
    ('choreographer', 'Dancer', '', ''),
    ('screenwriter', 'Writer', '', ''),
    ('author', 'Writer', '', ''),
    ('writer', 'Writer', '', ''),
    ('film producer', 'Producer', '', ''),
    ('producer', 'Producer', '', ''),
    ('activist', 'Activist', '', ''),
    ('entrepreneur', 'Entrepreneur', '', ''),
    ('businessperson', 'Entrepreneur', '', ''),
    ('scientist', 'Scientist', '', ''),
    ('photographer', 'Photographer', '', ''),
    ('fashion designer', 'Designer', '', ''),
    ('designer', 'Designer', '', ''),
    ('magician', 'Magician', '', ''),
    ('physician', 'Doctor', '', ''),
    ('teacher', 'Education', '', ''),
    ('professor', 'Education', '', ''),
    ('athlete', 'Athlete', '', ''),
]

# Talent Types that are usually a SECONDARY trade -- chosen only when the person
# has no primary performing/creative occupation. Keeps e.g. Dove Cameron
# (actor + singer + model) as Actress/Musician rather than Model.
_SECONDARY_TALENT_TYPES = {'Model'}

# discovered sport label -> template subtype term (rest matched literally)
_TALENT_SPORT_ALIAS = {
    'association football': 'Soccer', 'american football': 'Football',
    'track and field': 'Running / Track & Field',
    'athletics': 'Running / Track & Field',
    'mixed martial arts': 'MMA', 'auto racing': 'Racing',
    'motorsport': 'Motorsports', 'professional wrestling': 'Pro Wrestling',
    'ice hockey': 'Ice Hockey', 'basketball': 'Basketball',
    'baseball': 'Baseball', 'tennis': 'Tennis', 'golf': 'Golf',
    'boxing': 'Boxing', 'swimming': 'Swimming', 'gymnastics': 'Gymnastics',
    'cricket': 'Cricket', 'surfing': 'Surfer', 'skateboarding': 'Skateboarding',
}


# A hint can name a sport without describing an athlete. "basketball coach",
# "NFL commentator" and "golf writer" must not export as Talent Type - Athlete.
_NON_ATHLETE_ROLES = (
    'coach', 'manager', 'commentator', 'analyst', 'pundit', 'presenter',
    'anchor', 'host', 'journalist', 'reporter', 'writer', 'author',
    'broadcaster', 'promoter', 'agent', 'executive', 'owner', 'referee',
    'umpire', 'official', 'trainer', 'scout', 'physio', 'doctor',
    'announcer', 'blogger', 'podcaster', 'youtuber', 'influencer',
)


def _hint_terms_for(hint):
    """Expanded hint terms, via the resolver's expansion when importable."""
    try:
        from metadata_fetcher import _hint_terms
        return _hint_terms(hint)
    except Exception:
        return [w for w in str(hint or '').lower().replace('-', ' ').split()
                if w]


def _hint_sport(hint):
    """Template sport term named by an Ops profession hint, or ''.
    Runs the hint through the same expansion the resolver uses, so "NBA
    basketball" and "soccer player" land on 'Basketball' / 'Soccer'."""
    if not hint:
        return ''
    low = str(hint).lower()
    if any(r in low for r in _NON_ATHLETE_ROLES):
        return ''      # names a sport, but describes a non-playing role
    terms = _hint_terms_for(hint)
    tset = set(terms)
    # longest alias keys first so 'american football' beats bare 'football'
    for label in sorted(_TALENT_SPORT_ALIAS, key=len, reverse=True):
        if label in tset:
            return _TALENT_SPORT_ALIAS[label]
    for label in sorted(_TALENT_SPORT_ALIAS, key=len, reverse=True):
        if any(label in t for t in terms):
            return _TALENT_SPORT_ALIAS[label]
    # Bare "football" is deliberately NOT resolved here: it means soccer in most
    # of the world and gridiron in the US. Leave it to the resolved person's own
    # P641 sport claim rather than guessing a subtype we may have to unpick.
    return ''


def _occ_entry(occ):
    """First _TALENT_OCC_MAP entry matching one occupation label, or None."""
    o = str(occ).lower()
    for kw, typ, skind, sterm in _TALENT_OCC_MAP:
        if kw in o:
            return (typ, skind, sterm)
    return None


def _talent_classify(metadata):
    """(talent_type_line, subtype_line) from the Ops profession hint first,
    then discovered occupations/sports.

    Occupation priority used to be the position of a keyword in
    _TALENT_OCC_MAP, which is hand-ordered with 'rapper'/'singer' above
    'actor'. That silently misclassified anyone with a side career -- Mark
    Wahlberg came back "Musician - Rapper" because Wikidata lists rapper
    among his occupations at all. We now walk the occupations in the order
    WIKIDATA returns them (P106 is roughly prominence-ordered) and take the
    first that maps to a known type, so the person's primary trade wins.
    """
    occs = [str(o).lower() for o in (metadata.get('occupations') or [])]
    sports = [str(s).lower() for s in (metadata.get('sports') or [])]
    gender = str(metadata.get('gender') or '')
    hint = str(metadata.get('profession') or '').strip()
    if metadata.get('hint_rejected'):
        hint = ''   # every candidate contradicted it; do not classify from it
    ttype = subtype = ''

    # 1) the Ops-supplied professional details win when they name a type or a
    #    sport -- that is the entire point of collecting them.
    if hint:
        hsport = _hint_sport(hint)
        if hsport:
            ttype = 'Athlete'
            subtype = (_tref().talent_subtype_for('Athlete', hsport)
                       if _tref() else '') or \
                f"Talent Subtype - Athlete - {hsport}"
        else:
            # Match the EXPANDED terms, not the raw string: otherwise
            # "actress"/"DJ"/"TV host" never hit _TALENT_OCC_MAP and the whole
            # non-sport half of the alias table goes unused.
            hit = None
            for t in [hint] + _hint_terms_for(hint):
                hit = _occ_entry(t)
                if hit:
                    break
            if hit:
                ttype, skind, sterm = hit
                if skind and sterm:
                    subtype = (_tref().talent_subtype_for(skind, sterm)
                               if _tref() else '') or \
                        f"Talent Subtype - {skind} - {sterm}"

    # 2) otherwise fall back to what Wikidata discovered. A sport-adjacent hint
    #    ("basketball coach") suppresses the P641 shortcut too -- most coaches
    #    and commentators carry a sport claim, so without this the guard above
    #    would be bypassed one line later.
    hint_is_non_athlete = bool(hint) and any(
        r in hint.lower() for r in _NON_ATHLETE_ROLES)
    if not ttype and sports and not hint_is_non_athlete:
        ttype = 'Athlete'
        term = _TALENT_SPORT_ALIAS.get(sports[0], sports[0].title())
        if _tref():
            subtype = _tref().talent_subtype_for('Athlete', term)
        if not subtype:
            subtype = f"Talent Subtype - Athlete - {term}"
    elif not ttype:
        # Walk Wikidata's own order, but let a primary trade (Actor, Musician,
        # ...) outrank a secondary one (Model): a model who also acts or sings
        # is classified by the headline trade, not by 'model'.
        primary = secondary = None
        for o in occs:
            hit = _occ_entry(o)
            if not hit:
                continue
            if hit[0] in _SECONDARY_TALENT_TYPES:
                secondary = secondary or hit
            else:
                primary = hit
                break
        hit = primary or secondary
        if hit:
            ttype, skind, sterm = hit
            if skind and sterm:
                subtype = (_tref().talent_subtype_for(skind, sterm)
                           if _tref() else '') or \
                    f"Talent Subtype - {skind} - {sterm}"
    if ttype == 'Actor' and gender == 'Gender - Woman':
        ttype = 'Actress'
    if ttype == 'Politician':
        subtype = ('Talent Subtype - Politician - United States'
                   if metadata.get('us_citizen')
                   else 'Talent Subtype - Politician - International')
    return (f"Talent Type - {ttype}" if ttype else '', subtype)


def create_talent_row(title, metadata=None):
    """Create a Talent row in the 38-column BrandDef schema.
    Values present in `metadata` always win over computed defaults."""
    metadata = metadata or {}
    clean_name = re.sub(r"\s*-\s*DAR\s*$", "", title, flags=re.IGNORECASE).strip()
    clean_name = _clean_title_text(clean_name)
    out_title = f"{clean_name} - DAR"   # talent brands are DAR rows
    _review = bool(metadata.get('needs_review'))

    # sub-category: Subtype \n Gender \n Talent Type (template CONCAT order)
    _sub = str(metadata.get('title_sub_category') or '').strip()
    if not _sub:
        ttype_line, subtype_line = _talent_classify(metadata)
        if not ttype_line and not _review:
            # Template default. Deliberately NOT applied to unresolved rows:
            # stamping "Media Personality" on a person we could not identify
            # turns a visible gap into an invisible wrong answer.
            ttype_line = 'Talent Type - Media Personality'
        gender_line = str(metadata.get('gender') or '').strip()
        _sub = "\n".join(x for x in (subtype_line, gender_line, ttype_line) if x)

    # twitter_search_terms: same logic as Movies/TV Shows (talent = DAR row)
    gen_terms, _ = generate_search_terms(
        clean_name, '', None, True,
        twitter_handle=str(metadata.get('twitter_handle') or ''))

    def mv(key, default=''):
        v = metadata.get(key, '')
        return v if v not in (None, '') else default

    row = {
        'brand_id': metadata.get('brand_id', ''),
        'title': out_title,
        'title_category': 'Talent',
        'title_sub_category': _sub,
        'genre': metadata.get('genre', ''),
        'primary_genre': metadata.get('primary_genre', ''),
        'rovi_id': metadata.get('rovi_id', ''),
        'ticker_symbol': metadata.get('ticker_symbol', ''),
        'title_content_windows': metadata.get('title_content_windows', ''),
        'companies': mv('companies', 'Pristine Brand'),
        'brand_set': mv('brand_set', TALENT_DEFAULT_BRAND_SET),
        'active': mv('active', 't'),
        'released_on': metadata.get('released_on', ''),
        'box_office': metadata.get('box_office', ''),
        'street_date': metadata.get('street_date', ''),
        'gross_screen': metadata.get('gross_screen', ''),
        'opening_weekend_box_office': metadata.get('opening_weekend_box_office', ''),
        'network': metadata.get('network', ''),
        'facebook_page': metadata.get('facebook_page', ''),
        'twitter_handle': metadata.get('twitter_handle', ''),
        'instagram_user': str(metadata.get('instagram_user') or '').lower(),
        'youtube_channel_username': metadata.get('youtube_channel_username', ''),
        'tiktok_user': metadata.get('tiktok_user', ''),
        'linkedin_page': metadata.get('linkedin_page', ''),
        'tumblr_page': metadata.get('tumblr_page', ''),
        'pinterest_user_username': metadata.get('pinterest_user_username', ''),
        'pinterest_board': metadata.get('pinterest_board', ''),
        'wikipedia_page': metadata.get('wikipedia_page', ''),
        'rottentomatoes': metadata.get('rottentomatoes', ''),
        'imdb_id': metadata.get('imdb_id', ''),
        'metacritic': metadata.get('metacritic', ''),
        'facebook_search_terms': metadata.get('facebook_search_terms', ''),
        'twitter_search_terms': mv('twitter_search_terms', gen_terms),
        'instagram_search_terms': metadata.get('instagram_search_terms', ''),
        'tumblr_search_terms': metadata.get('tumblr_search_terms', ''),
        'twitter_search_term_keywords': metadata.get('twitter_search_term_keywords', ''),
        'youtube_search_terms': metadata.get('youtube_search_terms', ''),
        'url_managers': metadata.get('url_managers', ''),
    }
    if _review:
        # Underscore keys are stripped by the reindex(columns=...) in
        # _rows_to_workbook, so the 38-column BrandDef schema is untouched --
        # they only feed the separate 'Needs Review' sheet and the preview.
        row['_needs_review'] = True
        row['_review_reason'] = str(metadata.get('review_reason') or
                                    'Could not resolve this person.')
        row['_review_profession'] = str(metadata.get('profession') or '')
    return row


# ===================== Publishers (BrandDefinitionReport schema) =====================
# Publisher brands export in the 40-column BrandDefinitionReport / BrandIngest
# format (learned from the Brand Definition Report template): ONE ' - DAR' row
# per publication -- no regular twin, like Talent. title_category 'Publishers',
# companies 'Pristine Brand', brand_set 'LF // Publishing\nPristine DAR Brands',
# an optional 'Publication Type - X' sub-category, and '#name|DAR|DAR' twitter
# search terms. genre / box-office / ratings (RT, IMDb, Metacritic) stay blank --
# publishing brands are not rated titles. network carries the parent publisher.
PUBLISHER_COLUMNS = [
    'brand_id', 'title', 'title_created_date', 'title_category',
    'title_sub_category', 'genre', 'primary_genre', 'iso_mic', 'stock_exchange',
    'ticker_symbol', 'companies', 'brand_set', 'composite_brand_set', 'active',
    'released_on', 'domestic_opening_weekend_box_office',
    'domestic_opening_weekend_screens', 'domestic_opening_weekend_rank',
    'street_date', 'network', 'facebook_page', 'facebook_verified',
    'twitter_handle', 'twitter_verified', 'instagram_user',
    'youtube_channel_username', 'youtube_channel_company', 'tiktok_user',
    'linkedin_page', 'threads_page', 'pinterest_user_username',
    'pinterest_board', 'wikipedia_page', 'rottentomatoes', 'imdb_id',
    'metacritic', 'twitter_search_terms', 'instagram_business_hashtags',
    'twitter_search_term_keywords', 'last_reviewed',
]

PUBLISHER_DEFAULT_BRAND_SET = "LF // Publishing\nPristine DAR Brands"


def create_publisher_row(title, metadata=None):
    """Create a Publisher row in the 40-column BrandDefinitionReport schema.

    Publisher brands are DAR rows: one ' - DAR' row per publication, no twin.
    Any value present in `metadata` (an uploaded/payload row or auto-discovered
    brand data) always wins over the computed default, matching the app's other
    row builders.
    """
    metadata = metadata or {}
    clean_name = re.sub(r"\s*-\s*DAR\s*$", "", title, flags=re.IGNORECASE).strip()
    out_title = f"{clean_name} - DAR"   # publisher brands are DAR rows

    # sub-category: 'Publication Type - X' when known; blank is valid (some
    # template rows carry no publication type, e.g. iMore).
    _sub = str(metadata.get('title_sub_category') or '').strip()

    # twitter_search_terms: DAR row -> '@handle|DAR|DAR' + '#name|DAR|DAR'
    gen_terms, _ = generate_search_terms(
        clean_name, '', None, True,
        twitter_handle=str(metadata.get('twitter_handle') or ''))

    def mv(key, default=''):
        v = metadata.get(key, '')
        return v if v not in (None, '') else default

    row = {
        'brand_id': metadata.get('brand_id', ''),
        'title': out_title,
        'title_created_date': mv('title_created_date',
                                 datetime.now().strftime('%Y-%m-%d')),
        'title_category': mv('title_category', 'Publishers'),
        'title_sub_category': _sub,
        'genre': metadata.get('genre', ''),
        'primary_genre': metadata.get('primary_genre', ''),
        'iso_mic': metadata.get('iso_mic', ''),
        'stock_exchange': metadata.get('stock_exchange', ''),
        'ticker_symbol': metadata.get('ticker_symbol', ''),
        'companies': mv('companies', 'Pristine Brand'),
        'brand_set': mv('brand_set', PUBLISHER_DEFAULT_BRAND_SET),
        'composite_brand_set': metadata.get('composite_brand_set', ''),
        'active': _norm_bool(metadata.get('active', True)),
        'released_on': metadata.get('released_on', ''),
        'domestic_opening_weekend_box_office': metadata.get('domestic_opening_weekend_box_office', ''),
        'domestic_opening_weekend_screens': metadata.get('domestic_opening_weekend_screens', ''),
        'domestic_opening_weekend_rank': metadata.get('domestic_opening_weekend_rank', ''),
        'street_date': metadata.get('street_date', ''),
        'network': metadata.get('network', ''),
        'facebook_page': metadata.get('facebook_page', ''),
        'facebook_verified': metadata.get('facebook_verified', ''),
        'twitter_handle': metadata.get('twitter_handle', ''),
        'twitter_verified': metadata.get('twitter_verified', ''),
        'instagram_user': str(metadata.get('instagram_user') or '').lower(),
        'youtube_channel_username': metadata.get('youtube_channel_username', ''),
        'youtube_channel_company': metadata.get('youtube_channel_company', ''),
        'tiktok_user': metadata.get('tiktok_user', ''),
        'linkedin_page': metadata.get('linkedin_page', ''),
        'threads_page': metadata.get('threads_page', ''),
        'pinterest_user_username': metadata.get('pinterest_user_username', ''),
        'pinterest_board': metadata.get('pinterest_board', ''),
        'wikipedia_page': metadata.get('wikipedia_page', ''),
        'rottentomatoes': metadata.get('rottentomatoes', ''),
        'imdb_id': metadata.get('imdb_id', ''),
        'metacritic': metadata.get('metacritic', ''),
        'twitter_search_terms': mv('twitter_search_terms', gen_terms),
        'instagram_business_hashtags': metadata.get('instagram_business_hashtags', ''),
        'twitter_search_term_keywords': metadata.get('twitter_search_term_keywords', ''),
        'last_reviewed': metadata.get('last_reviewed', ''),
    }
    return row


# ===================== Video Games (BDR schema) =====================
# Games export in the 39-column Video-Game BDR format. Like movies/TV they
# get a base row (Operations - Core Title, brand_set 'Competitive View') and
# a ' - DAR' twin (DAR labels, brand_set 'LF // Video Games // Games').
GAME_COLUMNS = [
    'brand_id', 'title', 'title_category', 'title_sub_category', 'genre',
    'primary_genre', 'rovi_id', 'ticker_symbol', 'title_content_windows',
    'companies', 'brand_set', 'active', 'released_on', 'box_office',
    'street_date', 'gross_screen', 'opening_weekend_box_office', 'network',
    'brand_listing_hidden', 'facebook_page', 'twitter_handle', 'instagram_user',
    'youtube_channel_username', 'tiktok_user', 'tumblr_page',
    'pinterest_user_username', 'pinterest_board', 'wikipedia_page',
    'rottentomatoes', 'imdb_id', 'metacritic', 'facebook_search_terms',
    'twitter_search_terms', 'instagram_search_terms', 'tumblr_search_terms',
    'twitter_search_term_keywords', 'youtube_search_terms',
    'reddit_search_terms', 'url_managers',
]

GAME_DAR_BRAND_SET = "LF // Video Games // Games\nPristine DAR Brands"

# Placeholders for the Video Game fields that are mandatory. They are loud on
# purpose: a flagged cell gets fixed, a blank one ships.
GAME_CONFIRM_DEVELOPER = "«CONFIRM developer»"
GAME_CONFIRM_PLATFORMS = "«CONFIRM platforms»"
GAME_CONFIRM_NETWORK = "«CONFIRM network (publisher)»"
# fixed clause tails from the ingest template (incl. its 'Swtich 2' spelling)
_GAME_KW_TAIL = ('"Video Game" OR Playstation OR iOS OR PS4 OR PS5 OR Xbox OR '
                 'Switch OR Swtich 2 OR PC')
_GAME_RD_TAIL = ('"Video Game" | Playstation | iOS | PS4 | PS5 | Xbox | '
                 'Switch | Switch 2 | PC')

# discovered platform label -> template platform tail. Keys are lower-cased and
# cover the spellings Wikidata AND Metacritic use for the same platform, so a
# label like 'Xbox Series X and Series S' or 'PlayStation 5 Pro' normalises
# instead of being silently dropped from the sub-category cell.
_GAME_PLATFORM_ALIAS = {
    'playstation 5': 'PS5', 'playstation 5 pro': 'PS5', 'ps5': 'PS5',
    'playstation 4': 'PS4', 'playstation 4 pro': 'PS4', 'ps4': 'PS4',
    'playstation 2': 'PS2', 'playstation': 'PS5',
    'xbox series x': 'Xbox Series X', 'xbox series x/s': 'Xbox Series X',
    'xbox series x|s': 'Xbox Series X', 'xbox series s': 'Xbox Series X',
    'xbox series x and series s': 'Xbox Series X',
    'xbox series x and s': 'Xbox Series X', 'xbox series': 'Xbox Series X',
    'xbox one': 'Xbox One', 'xbox one x': 'Xbox One',
    'nintendo switch 2': 'Switch 2', 'switch 2': 'Switch 2',
    'nintendo switch': 'Switch', 'switch': 'Switch',
    'microsoft windows': 'PC', 'windows': 'PC', 'windows pc': 'PC',
    'pc': 'PC', 'macos': 'PC', 'mac os': 'PC', 'linux': 'PC',
    'ios': 'Mobile', 'ipados': 'Mobile', 'android': 'Mobile',
    'game boy': 'Game Boy',
}


def _game_hashtag(name):
    """Template hashtag cleaning: case preserved; '&'->'and', '+'->'plus';
    removes space : , - ! ' . \\ ( )"""
    s = str(name or '')
    for a, b in ((' ', ''), (':', ''), (',', ''), ('-', ''), ('!', ''),
                 ("'", ''), ('.', ''), ('&', 'and'), ('+', 'plus'),
                 ('\\', ''), ('(', ''), (')', '')):
        s = s.replace(a, b)
    return '#' + s


# The six platform values a Video Game title_sub_category may carry, in the
# order the 2026-07-31 ingest used them. Only the platforms a game actually
# releases on are emitted - the list is the vocabulary, not a checklist - so a
# PC-only indie gets one Platform line and a full multiplatform release gets six.
GAME_PLATFORM_VOCAB = ["PC", "PS5", "PS4", "Switch 2", "Xbox One", "Xbox Series X"]

# Anything outside the vocabulary is folded into its nearest member; a platform
# with no sensible equivalent (PS2, Game Boy, Mobile) is left out of
# title_sub_category rather than inventing a seventh value.
_GAME_PLATFORM_FOLD = {
    'Switch': 'Switch 2',
    'Xbox Series S': 'Xbox Series X',
}


def _game_platform_lines(platforms):
    """`Platform - X` lines for title_sub_category, vocabulary-ordered.

    Returns at most six lines, ordered by :data:`GAME_PLATFORM_VOCAB` rather than
    by the order the source listed them, so two games on the same platforms
    always produce byte-identical cells.
    """
    wanted = set()
    for p in (platforms or []):
        pl = str(p).strip()
        if not pl:
            continue
        name = _GAME_PLATFORM_ALIAS.get(pl.lower(), pl)
        name = _GAME_PLATFORM_FOLD.get(name, name)
        if name in GAME_PLATFORM_VOCAB:
            wanted.add(name)

    out = []
    for name in GAME_PLATFORM_VOCAB:
        if name not in wanted:
            continue
        line = (_tref().game_platform_for(name) if _tref() else '') or f"Platform - {name}"
        if line not in out:
            out.append(line)
    return out[:6]


def _game_title_variants(clean_title):
    """Title spellings used in a game's youtube_channel_username lines.

    Titles with a colon get TWO variants: the title as written first, then the
    punctuation-stripped form. (Note the order is the opposite of the Movies/TV
    `_title_variants` helper - the games ingest writes them this way round.)
    """
    stripped = re.sub(r'\s+', ' ', re.sub(r'\s*:\s*', ' ', clean_title)).strip()
    return [clean_title, stripped] if stripped != clean_title else [clean_title]


def _game_youtube_lines(channels, clean_title):
    """'<channel>|<title variant>' for every channel x title variant pair.

    Two or more channels therefore share the one cell, one per line - a game can
    legitimately have a franchise or regional channel besides its main one.
    """
    lines = []
    for ch in channels or []:
        ch = str(ch).strip()
        if not ch:
            continue
        for variant in _game_title_variants(clean_title):
            line = ch + variant if ch.endswith('|') else f"{ch}|{variant}"
            if line not in lines:
                lines.append(line)
    return "\n".join(lines)


def _game_search_terms(clean_title, is_dar):
    """twitter_search_terms for a Video Game.

    Deliberately NOT `generate_search_terms`: the games ingest preserves the
    title's capitalisation, appends the literal 'videoGame' rather than the
    publisher name, and carries a single '|DAR' label, e.g.

        #Akatori|DAR
        #AkatorivideoGame|DAR
    """
    label = "DAR" if is_dar else "Operations - Core Title"
    tag = _game_hashtag(clean_title)
    return f"{tag}|{label}\n{tag}videoGame|{label}"


def create_game_row(title, metadata=None):
    """Create a Video Game row in the 39-column BDR schema.
    Values present in `metadata` always win over computed defaults."""
    metadata = metadata or {}
    is_dar = " - DAR" in title
    clean_title = re.sub(r"\s*-\s*DAR\s*$", "", title, flags=re.IGNORECASE).strip()
    label = "DAR" if is_dar else "Operations - Core Title"

    developer = str(metadata.get('developer') or '').strip()
    dev_line = developer if developer.startswith('Developer - ') else \
        (f"Developer - {developer}" if developer else '')
    dev_name = dev_line.replace('Developer - ', '', 1)

    # network = the PUBLISHER, and it is mandatory for a Video Game. When it is
    # self-published the developer is the publisher, so fall back to that before
    # flagging the cell for Ops.
    publisher = str(metadata.get('network') or '').strip() or dev_name or GAME_CONFIRM_NETWORK

    # sub-category = Developer line + one Platform line per applicable platform.
    # Both parts are mandatory: a blank cell here silently breaks the ingest, so
    # a missing half is flagged instead of omitted.
    _sub = str(metadata.get('title_sub_category') or '').strip()
    if not _sub:
        _plat_lines = _game_platform_lines(metadata.get('platforms'))
        _sub = "\n".join([dev_line or GAME_CONFIRM_DEVELOPER]
                         + (_plat_lines or [GAME_CONFIRM_PLATFORMS]))

    # genre (single, per template) + mapped primary
    _genre = str(metadata.get('genre') or '').split('\n')[0].strip()
    _primary = str(metadata.get('primary_genre') or '').strip()
    if not _primary and _genre:
        _primary = (_tref().game_primary_genre(_genre) if _tref() else '') or _genre

    # youtube_channel_username: '<channel>|<title variant>' per line. The game's
    # OWN channel(s) win; the publisher's channel is the fallback. A title with a
    # colon gets two lines per channel - as written, then punctuation-stripped -
    # so both spellings are matched (as in the 2026-07-31 ingest).
    _yt = str(metadata.get('youtube_channel_username') or '').strip()
    if not _yt:
        _channels = [u.strip() for u in
                     str(metadata.get('youtube_own_channel') or '').splitlines() if u.strip()]
        if not _channels and publisher and _tref():
            pinfo = _tref().game_publisher(publisher)
            if pinfo and pinfo.get('youtube'):
                _channels = [pinfo['youtube']]
        _yt = _game_youtube_lines(_channels, clean_title)

    # developer keyword clauses (template tables; constructed fallback)
    dinfo = (_tref().game_developer(dev_name) if (_tref() and dev_name) else None) or {}
    tw_clause = dinfo.get('twitter_clause') or \
        (f'{_game_hashtag(dev_name)} OR "{dev_name}"' if dev_name else '"Video Game"')
    rd_clause = dinfo.get('reddit_clause') or \
        (f'{_game_hashtag(dev_name)} | "{dev_name}"' if dev_name else '"Video Game"')

    gen_terms = _game_search_terms(clean_title, is_dar)
    kw_tail = "|DAR|DAR|2021-01-01" if is_dar else \
        "|Operations - Core Title|Operations - Core Title"
    gen_kw = f'("{clean_title}") ({tw_clause} OR {_GAME_KW_TAIL}){kw_tail}'
    gen_reddit = f'("{clean_title}") ({rd_clause} | {_GAME_RD_TAIL})' + \
        ("|2021-01-01" if is_dar else "")

    def mv(key, default=''):
        v = metadata.get(key, '')
        return v if v not in (None, '') else default

    row = {
        'brand_id': metadata.get('brand_id', ''),
        'title': title,
        'title_category': 'Video Game',
        'title_sub_category': _sub,
        'genre': _genre,
        'primary_genre': _primary,
        'rovi_id': metadata.get('rovi_id', ''),
        'ticker_symbol': metadata.get('ticker_symbol', ''),
        'title_content_windows': metadata.get('title_content_windows', ''),
        'companies': mv('companies', 'Pristine Brand' if is_dar else 'Unknown'),
        'brand_set': mv('brand_set',
                        GAME_DAR_BRAND_SET if is_dar else 'Competitive View'),
        'active': mv('active', 't'),
        'released_on': metadata.get('released_on', ''),
        'box_office': metadata.get('box_office', ''),
        'street_date': metadata.get('street_date', ''),
        'gross_screen': metadata.get('gross_screen', ''),
        'opening_weekend_box_office': metadata.get('opening_weekend_box_office', ''),
        'network': publisher,
        'brand_listing_hidden': mv('brand_listing_hidden', 'f'),
        'facebook_page': metadata.get('facebook_page', ''),
        'twitter_handle': metadata.get('twitter_handle', ''),
        'instagram_user': str(metadata.get('instagram_user') or '').lower(),
        'youtube_channel_username': _yt,
        'tiktok_user': metadata.get('tiktok_user', ''),
        'tumblr_page': metadata.get('tumblr_page', ''),
        'pinterest_user_username': metadata.get('pinterest_user_username', ''),
        'pinterest_board': metadata.get('pinterest_board', ''),
        'wikipedia_page': metadata.get('wikipedia_page', ''),
        'rottentomatoes': metadata.get('rottentomatoes', ''),
        'imdb_id': metadata.get('imdb_id', ''),
        'metacritic': metadata.get('metacritic', ''),
        'facebook_search_terms': metadata.get('facebook_search_terms', ''),
        'twitter_search_terms': mv('twitter_search_terms', gen_terms),
        'instagram_search_terms': metadata.get('instagram_search_terms', ''),
        'tumblr_search_terms': metadata.get('tumblr_search_terms', ''),
        'twitter_search_term_keywords': mv('twitter_search_term_keywords', gen_kw),
        'youtube_search_terms': metadata.get('youtube_search_terms', ''),
        'reddit_search_terms': mv('reddit_search_terms', gen_reddit),
        'url_managers': metadata.get('url_managers', ''),
    }
    return row


def make_row(title, is_movie, network="", metadata=None, talent=False, game=False,
             publisher=False):
    """Dispatch: movies (42-col), TV (39-col BrandIngest), Talent (38-col
    BrandDef), Video Games (39-col BDR), Publishers (40-col BrandDefinitionReport)."""
    if talent:
        return create_talent_row(title, metadata)
    if game:
        return create_game_row(title, metadata)
    if publisher:
        return create_publisher_row(title, metadata)
    if is_movie:
        return create_row(title, is_movie, network, metadata)
    return create_tv_row(title, network, metadata)


def _strip_language_subcategory(sub):
    """Drop any 'Language Type - ...' line from a title_sub_category value.

    Movies do not carry Language Type in their sub-category (it belongs to TV
    Shows), so it is removed regardless of source: an explicit upload, the
    per-network subcategory map, or a reference-template default.
    """
    lines = [ln for ln in str(sub or '').split('\n')
             if not ln.strip().lower().startswith('language type')]
    return '\n'.join(lines)


def create_row(title, is_movie, network="", metadata=None):
    """Create a data row for a title - ALL 42 COLUMNS POPULATED.

    Any value present in `metadata` overrides the computed default, so an
    uploaded row's channels (and every other field) are preserved.
    """
    metadata = metadata or {}
    is_dar = " - DAR" in title
    clean_title = re.sub(r"\s*-\s*DAR\s*$", "", title, flags=re.IGNORECASE).strip()
    # a trailing '(2026)' / '(Studio)' disambiguator stays in the title column
    # but is ignored for social fields and search terms
    social_title = _strip_disambiguator(clean_title)
    title_category = "Movies" if is_movie else "TV Shows"

    # Effective network = discovered/explicit network, else the passed arg;
    # normalise raw distributor -> LF network label (e.g. "Lionsgate" -> "Lionsgate / Summit").
    eff_network = (str(metadata.get('network') or network or '')).strip()
    if REF is not None and eff_network:
        eff_network = REF.normalize_network(eff_network)

    # studio record from the Ops ingest template (authoritative when present)
    sinfo = _tref().film_studio(eff_network) if (_tref() and eff_network) else None

    # sub-category is shared by the base title AND its DAR twin
    _sub_explicit = str(metadata.get('title_sub_category') or '').strip()
    _sub = _sub_explicit
    _scale = str(metadata.get('release_scale') or '').strip().title()
    if not _sub and sinfo:
        # template-driven: Release scale + Studio Type. Movies deliberately omit
        # Language Type from title_sub_category.
        scale = _scale if _scale in ('Wide', 'Limited') else 'Limited'
        stype = sinfo.get('studio_type') or 'Studio - Independent'
        _sub = f"Release - {scale}\n{stype}"
    if not _sub and REF is not None:
        _sub = REF.subcategory_for(eff_network)
    if not _sub:
        _sub = 'Release - Limited\nStudio - Independent'
    # the upcoming-release-movies calendar knows the actual Wide/Limited scale;
    # it overrides the per-network default (but never an explicit upload value)
    if _scale in ('Wide', 'Limited') and not _sub_explicit:
        _sub = re.sub(r'Release - (Wide|Limited)', 'Release - ' + _scale, _sub)
    # Movies never carry a Language Type line in title_sub_category; strip it from
    # whatever source produced _sub (network subcategory map, reference default,
    # or an explicit upload).
    if is_movie:
        _sub = _strip_language_subcategory(_sub)
    is_wide = 'release - wide' in _sub.lower()

    # curated PARENT company of the network (e.g. Warner Bros. -> Warner Bros. Pictures)
    parent = (sinfo.get('company') if sinfo else '') or \
        (REF.companies_for(eff_network) if (REF is not None and eff_network) else "")
    if parent == 'Unknown':
        parent = ''

    if is_dar:
        companies = "Pristine Brand"
        if is_movie:
            brand_set = "LF // Film - Majors + Independents\nPristine DAR Brands"
        else:
            brand_set = "Pristine DAR Brands"
        # major-studio DAR rows also carry the corporate roll-up brand sets
        # (per-studio block from the ingest template; inline map as fallback)
        rollup = ""
        if _tref():
            # the template's roll-up sheet may key by studio OR parent company
            rollup = _tref().film_rollup(eff_network) or _tref().film_rollup(parent)
        if not rollup and REF is not None and parent:
            rollup = REF.dar_rollup_for(parent)
        if rollup:
            brand_set += "\n" + rollup
    else:
        companies = parent or "Unknown"
        brand_set = "Competitive View"
        # Wide theatrical releases carry an extra brand_set line
        if is_wide:
            brand_set += "\n[Data Feed] Film - Wide Release + Custom Requests"

    # Release year for search-term generation
    rel = str(metadata.get('released_on') or metadata.get('title_created_date') or '')
    year = rel[:4] if rel[:4].isdigit() else ''

    gen_terms, gen_keywords = generate_search_terms(
        social_title, eff_network, year, is_dar,
        twitter_handle=str(metadata.get('twitter_handle') or ''),
        network_clause=(sinfo.get('twitter_clause') if sinfo else ''))

    # normalise genre tokens to the LF taxonomy (Sci-Fi -> Sci Fi, etc.)
    _genre = metadata.get('genre', '')
    _primary = metadata.get('primary_genre', '')
    if REF is not None and _genre:
        _genre, _primary_fix = REF.normalize_genres(_genre)
        if not _primary:            # keep a provided primary_genre; else derive
            _primary = _primary_fix

    # YouTube: company channel comes from the network; username lines combine
    # the title's own channel (if any) + '<network channel>|<title>' variants
    _yt_company = str(metadata.get('youtube_channel_company') or '').strip()
    if not _yt_company and sinfo and sinfo.get('youtube'):
        # movie schema uses http:// URLs; take the first channel if several
        _yt_company = re.sub(r'^https://', 'http://',
                             str(sinfo['youtube']).split('\n')[0].strip())
    if not _yt_company and REF is not None and eff_network:
        _yt_company = REF.youtube_for(eff_network)
    _yt_username = str(metadata.get('youtube_channel_username') or '').strip()
    if not _yt_username:
        _yt_username = build_youtube_username(
            _yt_company, social_title,
            own_channel=str(metadata.get('youtube_own_channel') or ''))

    def mv(key, default=''):
        """metadata value, falling back to default when missing OR blank."""
        v = metadata.get(key, '')
        return v if v not in (None, '') else default

    row = {
        'record_type': mv('record_type', 'INGESTED'),
        'brand_id': metadata.get('brand_id', ''),
        'title': title,
        'title_created_date': mv('title_created_date', datetime.now().strftime('%Y-%m-%d')),
        'title_category': mv('title_category', title_category),
        'title_sub_category': _sub,
        'genre': _genre,
        'primary_genre': _primary,
        'iso_mic': metadata.get('iso_mic', ''),
        'stock_exchange': metadata.get('stock_exchange', ''),
        'ticker_symbol': metadata.get('ticker_symbol', ''),
        'companies': mv('companies', companies),
        'brand_set': mv('brand_set', brand_set),
        'composite_brand_set': metadata.get('composite_brand_set', ''),
        'active': _norm_bool(metadata.get('active', True)),
        'released_on': metadata.get('released_on', ''),
        'domestic_opening_weekend_box_office': metadata.get('domestic_opening_weekend_box_office', ''),
        'domestic_opening_weekend_screens': metadata.get('domestic_opening_weekend_screens', ''),
        'domestic_opening_weekend_rank': metadata.get('domestic_opening_weekend_rank', ''),
        'street_date': metadata.get('street_date', ''),
        'network': eff_network,
        'facebook_page': metadata.get('facebook_page', ''),
        'facebook_verified': metadata.get('facebook_verified', ''),
        'twitter_handle': metadata.get('twitter_handle', ''),
        'twitter_verified': metadata.get('twitter_verified', ''),
        'instagram_user': metadata.get('instagram_user', ''),
        'youtube_channel_username': _yt_username,
        'youtube_channel_company': _yt_company,
        'tiktok_user': metadata.get('tiktok_user', ''),
        'linkedin_page': metadata.get('linkedin_page', ''),
        'threads_page': metadata.get('threads_page', ''),
        'pinterest_user_username': metadata.get('pinterest_user_username', ''),
        'pinterest_board': metadata.get('pinterest_board', ''),
        'wikipedia_page': metadata.get('wikipedia_page', ''),
        'rottentomatoes': metadata.get('rottentomatoes', ''),
        'imdb_id': metadata.get('imdb_id', ''),
        'metacritic': metadata.get('metacritic', ''),
        'twitter_search_terms': mv('twitter_search_terms', gen_terms),
        'instagram_business_hashtags': metadata.get('instagram_business_hashtags', ''),
        'twitter_search_term_keywords': mv('twitter_search_term_keywords', gen_keywords),
        'url_managers': metadata.get('url_managers', ''),
        'last_reviewed': metadata.get('last_reviewed', ''),
    }

    if not row.get('url_managers'):
        row['url_managers'] = generate_url_managers(row)

    return row


def _read_upload(src):
    """Read an uploaded CSV/XLSX into a DataFrame.
    `src` is a Werkzeug FileStorage OR a (bytes, filename) tuple (used by jobs)."""
    if isinstance(src, tuple):
        data, filename = src
        stream = BytesIO(data)
    else:
        filename = src.filename
        stream = src
    fn = (filename or '').lower()
    if fn.endswith('.csv'):
        df = pd.read_csv(stream)
    else:
        df = pd.read_excel(stream, engine='openpyxl')
    df.columns = [str(c).strip() for c in df.columns]
    df = df.where(pd.notnull(df), '')
    return df


def _merge_meta(base_meta, title, auto_fetch, is_movie=True):
    """Overlay auto-discovered metadata under any explicit metadata.
    Explicit values always win; auto-discovery only fills missing/blank fields.
    """
    if not auto_fetch:
        return base_meta or {}
    base = base_meta or {}
    # release date first: a known date makes IMDb resolution year-specific
    yr = str(base.get('released_on') or base.get('street_date') or '')
    tt = re.search(r"tt\d{5,}", str(base.get('imdb_id') or base.get('imdb_url') or ''))
    if tt:
        discovered = fetch_metadata_by_tt(tt.group(0), is_movie, title, year_hint=yr) or {}
    else:
        discovered = fetch_metadata(title, is_movie, year_hint=yr) or {}
    merged = dict(discovered)
    for k, v in base.items():
        if v not in (None, ''):
            merged[k] = v
    return merged


def _norm_kind(v, default='movie'):
    """'tvshow'/'TV Shows' -> 'tv'; 'Talent' -> 'talent'; 'Video Game(s)' ->
    'game'; Beauty/Beverages/Sports/General -> their tfx kind;
    else 'movie'. 'mixed' (or blank) falls back to the given default."""
    s = str(v or '').strip().lower()
    if 'talent' in s:
        return 'talent'
    if 'game' in s:
        return 'game'
    if 'publisher' in s:            # 'publisher', 'Publishers'
        return 'publisher'
    if 'beauty' in s:               # 'beauty', 'Health & Beauty'
        return 'beauty'
    if 'beverage' in s:             # 'beverages', 'Beverages'
        return 'beverages'
    if 'sport' in s:                # 'sports', 'Sports Franchise'
        return 'sports'
    if s == 'general':
        return 'general'
    if 'tv' in s:
        return 'tv'
    if s in ('', 'mixed', 'nan', 'none'):
        return default
    return 'movie'


# Idea 2: optional professional-details hint for Talent. Accepted upload column
# names (case-insensitive). Used only for talent -- it disambiguates the person
# lookup and seeds the occupation used for classification/social fetching.
PROFESSION_COLUMNS = (
    'profession', 'professional_details', 'professional details',
    'talent_profession', 'talent profession', 'profession_detail',
    'profession/category', 'profession / category', 'profession or category',
    'category_detail', 'category detail',
)


def _clean_title_text(s):
    """Strip stray leading/trailing punctuation from a pasted title line.
    "Bill Murray:" was exported verbatim as the brand title "Bill Murray: - DAR"
    and never matched anything upstream. A trailing period is kept ("... Jr.")
    and interior punctuation is untouched."""
    s = str(s or '').strip()
    s = re.sub(r"^[\s\-–—:;,.|/\\*#>\"']+", '', s)
    s = re.sub(r"[\s\-–—:;,|/\\*#\"']+$", '', s)
    return re.sub(r"\s{2,}", ' ', s).strip()


def _row_profession(r):
    """Return the professional-details hint from an uploaded record, or ''."""
    if not isinstance(r, dict):
        return ''
    low = {str(k).strip().lower(): k for k in r.keys()}
    for name in PROFESSION_COLUMNS:
        if name in low:
            v = r.get(low[name])
            if v not in (None, '') and str(v).strip().lower() not in ('nan', 'none'):
                return str(v).strip()
    return ''


def _apply_profession(meta, profession):
    """Overlay a profession hint onto a talent metadata dict so classification
    and social fetching can use it (never overrides discovered occupations)."""
    if not profession:
        return meta
    meta = dict(meta or {})
    if meta.get('hint_rejected'):
        # The resolver found same-name people and none matched this hint. Do
        # not re-seed it as an occupation here or it comes back in through the
        # side door and classifies a row we deliberately left unresolved.
        meta.setdefault('profession', profession)
        return meta
    meta.setdefault('profession', profession)
    if not meta.get('occupations'):
        meta['occupations'] = [profession]
    return meta


# kinds handled by the titleforge extension (Beauty/Beverages/Sports/General)
TFX_KINDS = {'beauty', 'beverages', 'sports', 'general'}
_TFX_SHEET_LABELS = {'beauty': 'Beauty', 'beverages': 'Beverages',
                     'sports': 'Sports Teams', 'general': 'General'}


def create_tfx_row(title, kind, seed=None):
    """Build a BrandDef row for one of the four new schemas via the template
    logic in titleforge_ingest_ext. Explicit values from an uploaded row (seed)
    always win over derived ones, matching the app's other row builders."""
    seed = dict(seed or {})
    # drop blank/nan values so they don't mask derivation
    seed = {k: v for k, v in seed.items()
            if v is not None and str(v).strip() not in ('', 'nan', 'none', 'None')}
    seed.setdefault('Title', title)
    row = _tfx_build(kind, seed)
    row['title'] = row.get('title') or title
    # uploaded explicit values win
    cols = set(_TFX_COLUMNS.get(kind, []))
    low = {c.lower(): c for c in cols}
    for k, v in seed.items():
        col = low.get(str(k).strip().lower())
        if col:
            row[col] = v
    # twitter_search_terms: same logic as Movies/TV Shows (an explicit value
    # from the upload/payload still wins -- only the derived default changes)
    if not any(str(k).strip().lower() == 'twitter_search_terms' for k in seed):
        out_title = str(row.get('title') or title)
        base_title = re.sub(r"\s*-\s*DAR\s*$", "", out_title,
                            flags=re.IGNORECASE).strip()
        handle = str(row.get('twitter_handle') or '').strip()
        if 'twitter.com' in handle or 'x.com' in handle:
            handle = handle.rstrip('/').rsplit('/', 1)[-1]  # URL -> handle
        terms, _ = generate_search_terms(base_title,
                                         str(row.get('network') or ''),
                                         None, ' - DAR' in out_title,
                                         twitter_handle=handle)
        if terms:
            row['twitter_search_terms'] = terms
    row['_tfx_schema'] = kind   # internal routing marker; stripped on output
    return row


# ---------------- parallel auto-discovery (large-file support) ----------------
# Lookups used to run one title at a time; a 5,000-title file at ~2-5s per
# title could never finish. Discovery is I/O-bound, so a small thread pool
# gives a near-linear speed-up. Tune with the FETCH_WORKERS env var.
FETCH_WORKERS = max(1, int(os.getenv('FETCH_WORKERS', '8') or 8))


def _parallel_rows(items, worker, progress=None, parallel=True):
    """Run worker(index, item) -> [row, ...] for every item, preserving input
    order in the output. One failing title never kills the batch -- it just
    yields no rows (and is logged). progress(done, total) is thread-safe."""
    total = len(items)
    if not total:
        return []
    results = [None] * total
    state = {'done': 0}
    lock = threading.Lock()

    def _safe(i, item):
        try:
            results[i] = worker(i, item) or []
        except Exception as e:  # noqa: BLE001 -- fail soft per title
            logging.warning(f"title #{i + 1} failed during generation: {e}")
            results[i] = []
        finally:
            with lock:
                state['done'] += 1
                d = state['done']
            if progress:
                progress(d, total)

    if not parallel or total == 1 or FETCH_WORKERS == 1:
        for i, item in enumerate(items):
            _safe(i, item)
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(FETCH_WORKERS, total)) as ex:
            list(ex.map(lambda p: _safe(*p), enumerate(items)))

    out = []
    for r in results:
        out.extend(r or [])
    return out


def build_rows_from_upload(src, include_dar, auto_fetch=False, max_titles=None,
                           progress=None, default_kind='movie',
                           default_profession=''):
    """Turn an uploaded file into fully-populated rows.

    max_titles caps titles processed BEFORE lookups (keeps Preview fast).
    progress(done, total) is called after each source title (for job progress).
    default_kind (movie/tv/talent) comes from the UI's Title-type selector and
    applies to rows that don't declare their own type/title_category.
    """
    df = _read_upload(src)
    lower_cols = {c.lower(): c for c in df.columns}
    has_full_schema = any(col in lower_cols for col in SOCIAL_COLUMNS + ['record_type', 'brand_id'])

    rows = []
    if has_full_schema:
        all_cols = list(dict.fromkeys(COLUMNS + TV_COLUMNS + TALENT_COLUMNS))
        rename_map = {lower_cols[c.lower()]: c for c in all_cols if c.lower() in lower_cols}
        df = df.rename(columns=rename_map)
        df = df.where(pd.notnull(df), '')
        records = df.to_dict('records')
        if max_titles:
            records = records[:max_titles]

        def _one_record(i, r):
            t = str(r.get('title', '')).strip()
            if not t:
                return []
            kind_r = _norm_kind(r.get('title_category'), default_kind)
            if kind_r in TFX_KINDS and TFX_OK:
                seed = r
                if auto_fetch:
                    # discovered socials/wikipedia fill blanks only --
                    # explicit values from the upload always win
                    seed = dict(fetch_brand(t) or {})
                    for k, v in r.items():
                        if v not in (None, ''):
                            seed[k] = v
                return [create_tfx_row(t, kind_r, seed)]
            if kind_r == 'publisher':
                # publisher brands enrich from brand discovery (like the tfx
                # schemas); explicit upload values always win
                if auto_fetch:
                    disc = dict(fetch_brand(t) or {})
                    for k, v in r.items():
                        if v not in (None, ''):
                            disc[k] = v
                    r = disc
                return [make_row(t, False, '', r, publisher=True)]
            if kind_r in ('talent', 'game'):
                prof = (_row_profession(r) or default_profession) if kind_r == 'talent' else ''
                if auto_fetch:
                    disc = dict((fetch_person(t, profession=prof) if kind_r == 'talent'
                                 else fetch_game(t)) or {})
                    for k, v in r.items():
                        if v not in (None, ''):
                            disc[k] = v
                    r = disc
                if prof:
                    r = _apply_profession(r, prof)
                return [make_row(t, False, '', r,
                                 talent=(kind_r == 'talent'),
                                 game=(kind_r == 'game'))]
            is_movie_r = kind_r == 'movie'
            if auto_fetch:
                r = _merge_meta(r, t, True, is_movie=is_movie_r)
            # route through make_row so derived fields (network label, youtube
            # lines, brand sets, search terms) are computed consistently; explicit
            # values from the upload always win inside the row builders
            return [make_row(t, is_movie_r, str(r.get('network') or ''), r)]

        rows = _parallel_rows(records, _one_record, progress=progress,
                              parallel=auto_fetch)
    else:
        title_col = lower_cols.get('title') or df.columns[0]
        type_col = lower_cols.get('type') or lower_cols.get('title_category')
        network_col = lower_cols.get('network')
        prof_col = next((lower_cols[c] for c in PROFESSION_COLUMNS
                         if c in lower_cols), None)
        specs = []
        for _, r in df.iterrows():
            title = str(r[title_col]).strip()
            if not title:
                continue
            if max_titles and len(specs) >= max_titles:
                break
            kind = _norm_kind(r[type_col] if type_col else '', default_kind)
            network = str(r[network_col]).strip() if network_col else ''
            profession = str(r[prof_col]).strip() if prof_col else ''
            if profession.lower() in ('nan', 'none'):
                profession = ''
            if not profession:
                profession = default_profession
            specs.append((title, kind, network, profession))
        def _one_spec(i, spec):
            title, kind, network, profession = spec
            out = []
            if kind in TFX_KINDS and TFX_OK:
                seed = dict(fetch_brand(title) or {}) if auto_fetch else None
                out.append(create_tfx_row(title, kind, seed))
                if include_dar and ' - DAR' not in title:
                    out.append(create_tfx_row(f"{title} - DAR", kind, seed))
            elif kind == 'talent':
                meta = dict(fetch_person(title, profession=profession) or {}) if auto_fetch else {}
                meta = _apply_profession(meta, profession)
                out.append(make_row(title, False, '', meta, talent=True))
            elif kind == 'publisher':
                # publisher = a single DAR row per publication, no twin
                meta = dict(fetch_brand(title) or {}) if auto_fetch else {}
                out.append(make_row(title, False, '', meta, publisher=True))
            elif kind == 'game':
                meta = dict(fetch_game(title) or {}) if auto_fetch else {}
                out.append(make_row(title, False, '', meta, game=True))
                if include_dar and ' - DAR' not in title:
                    out.append(make_row(f"{title} - DAR", False, '', meta, game=True))
            else:
                is_movie = kind == 'movie'
                meta = _merge_meta({}, title, auto_fetch, is_movie=is_movie)
                out.append(make_row(title, is_movie, network, meta))
                if include_dar and ' - DAR' not in title:
                    out.append(make_row(f"{title} - DAR", is_movie, network, meta))
            return out

        rows = _parallel_rows(specs, _one_spec, progress=progress,
                              parallel=auto_fetch)
    return rows


def build_rows_from_titles(data, max_titles=None, progress=None):
    """Build rows from a manual titles payload (JSON)."""
    titles = [t.strip() for t in data.get('titles', []) if t and t.strip()]
    if max_titles:
        titles = titles[:max_titles]
    include_dar = data.get('includeDar', True)
    auto_fetch = bool(data.get('autoFetch', False))
    def _one_title(i, title):
        kind = _norm_kind(data.get('titles_type', {}).get(title, 'movie'))
        network = data.get('networks', {}).get(title, '')
        base_meta = data.get('metadata', {}).get(title, {})
        out = []
        if kind in TFX_KINDS and TFX_OK:
            seed = base_meta
            if auto_fetch:
                # discovered socials/wikipedia fill blanks only --
                # explicit metadata from the payload always wins
                seed = dict(fetch_brand(title) or {})
                for k, v in (base_meta or {}).items():
                    if v not in (None, ''):
                        seed[k] = v
            out.append(create_tfx_row(title, kind, seed))
            if include_dar and ' - DAR' not in title:
                out.append(create_tfx_row(f"{title} - DAR", kind, seed))
        elif kind == 'talent':
            # profession hint: explicit per-title 'professions' map, else from
            # the title's metadata payload (any accepted profession column name)
            profession = str(data.get('professions', {}).get(title, '')
                             or _row_profession(base_meta or {})).strip()
            metadata = dict(fetch_person(title, profession=profession) or {}) if auto_fetch else {}
            for k, v in (base_meta or {}).items():
                if v not in (None, ''):
                    metadata[k] = v
            metadata = _apply_profession(metadata, profession)
            # talent = a single DAR row per person, no twin
            out.append(make_row(title, False, '', metadata, talent=True))
        elif kind == 'publisher':
            metadata = dict(fetch_brand(title) or {}) if auto_fetch else {}
            for k, v in (base_meta or {}).items():
                if v not in (None, ''):
                    metadata[k] = v
            # publisher = a single DAR row per publication, no twin
            out.append(make_row(title, False, '', metadata, publisher=True))
        elif kind == 'game':
            metadata = dict(fetch_game(title) or {}) if auto_fetch else {}
            for k, v in (base_meta or {}).items():
                if v not in (None, ''):
                    metadata[k] = v
            out.append(make_row(title, False, '', metadata, game=True))
            if include_dar and ' - DAR' not in title:
                out.append(make_row(f"{title} - DAR", False, '', metadata, game=True))
        else:
            is_movie = kind == 'movie'
            metadata = _merge_meta(base_meta, title, auto_fetch, is_movie=is_movie)
            out.append(make_row(title, is_movie, network, metadata))
            if include_dar and ' - DAR' not in title:
                out.append(make_row(f"{title} - DAR", is_movie, network, metadata))
        return out

    return _parallel_rows(titles, _one_title, progress=progress,
                          parallel=auto_fetch)


def _is_tv_row(r):
    return str(r.get('title_category', '')).lower() == 'tv shows'


def _is_talent_row(r):
    return str(r.get('title_category', '')).lower() == 'talent'


def _is_game_row(r):
    return 'game' in str(r.get('title_category', '')).lower()


def _is_publisher_row(r):
    return str(r.get('title_category', '')).lower() == 'publishers'


def _rows_to_workbook(rows):
    """Write rows to an xlsx BytesIO. Movies use the 42-col schema, TV the
    39-col BrandIngest, Talent the 38-col BrandDef, Video Games the 39-col
    BDR; Beauty/Beverages/Sports/General use their template BrandDef layouts;
    mixed runs get one sheet per schema."""
    tfx = {}
    for r in rows:
        k = r.get('_tfx_schema')
        if k:
            tfx.setdefault(k, []).append(r)
    talent = [r for r in rows if not r.get('_tfx_schema') and _is_talent_row(r)]
    games = [r for r in rows if not r.get('_tfx_schema') and _is_game_row(r)]
    publishers = [r for r in rows if not r.get('_tfx_schema') and _is_publisher_row(r)]
    tv = [r for r in rows if not r.get('_tfx_schema') and _is_tv_row(r)]
    movies = [r for r in rows if not r.get('_tfx_schema')
              and not _is_tv_row(r) and not _is_talent_row(r)
              and not _is_game_row(r) and not _is_publisher_row(r)]
    groups = [g for g in (movies, tv, talent, games, publishers, *tfx.values()) if g]
    if len(groups) > 1:
        sheets = []
        if movies:
            sheets.append(('Movies', movies, COLUMNS))
        if tv:
            sheets.append(('TV Shows', tv, TV_COLUMNS))
        if talent:
            sheets.append(('Talent', talent, TALENT_COLUMNS))
        if games:
            sheets.append(('Video Games', games, GAME_COLUMNS))
        if publishers:
            sheets.append(('Publishers', publishers, PUBLISHER_COLUMNS))
        for k, rws in tfx.items():
            sheets.append((_TFX_SHEET_LABELS.get(k, k.title()), rws,
                           _TFX_COLUMNS[k]))
    elif tfx:
        k, rws = next(iter(tfx.items()))
        sheets = [('BrandDef', rws, _TFX_COLUMNS[k])]
    elif talent:
        sheets = [('BrandDef', talent, TALENT_COLUMNS)]
    elif games:
        sheets = [('BDR', games, GAME_COLUMNS)]
    elif publishers:
        sheets = [('BrandIngest', publishers, PUBLISHER_COLUMNS)]
    elif tv:
        sheets = [('BrandIngest', tv, TV_COLUMNS)]
    else:
        sheets = [('Sheet1', movies, COLUMNS)]
    out = BytesIO()
    # Rows the resolver could not confirm get their own sheet so Ops can see
    # them at a glance. The ingestion sheets keep their exact column sets.
    review = [r for r in rows if isinstance(r, dict) and r.get('_needs_review')]
    with pd.ExcelWriter(out, engine='openpyxl') as xw:
        for name, rws, cols in sheets:
            df = pd.DataFrame(rws).reindex(columns=cols)
            df = df.where(pd.notnull(df), '')
            df.to_excel(xw, sheet_name=name, index=False)
        if review:
            rdf = pd.DataFrame([{
                'title': r.get('title', ''),
                'title_category': r.get('title_category', ''),
                'professional_details': r.get('_review_profession', ''),
                'why_flagged': r.get('_review_reason', ''),
            } for r in review])
            rdf.to_excel(xw, sheet_name='Needs Review', index=False)
    out.seek(0)
    return out


# how many titles Preview samples (keeps auto-discovery fast on free tier)
PREVIEW_MAX_TITLES = 3


def collect_rows(preview=False):
    """Collect rows from either an uploaded file or a JSON titles payload.

    When preview=True only the first PREVIEW_MAX_TITLES titles are processed,
    BEFORE any auto-discovery, so the preview stays responsive.
    """
    max_titles = PREVIEW_MAX_TITLES if preview else None
    if request.files.get('file'):
        include_dar = request.form.get('includeDar', 'true').lower() != 'false'
        auto_fetch = request.form.get('autoFetch', 'false').lower() == 'true'
        default_kind = _norm_kind(request.form.get('titleType'))
        rows = build_rows_from_upload(request.files['file'], include_dar, auto_fetch,
                                      max_titles=max_titles,
                                      default_kind=default_kind,
                                      default_profession=request.form.get('talentProfession', ''))
    else:
        data = request.get_json(silent=True) or {}
        rows = build_rows_from_titles(data, max_titles=max_titles)
    return rows


@app.route('/')
def index():
    # wake the (free-tier) upcoming-release-movies service in the background
    # so the calendar index is ready by the time the user hits Generate
    warm_upcoming()
    return render_template('index.html')


@app.route('/api/lookup')
def api_lookup():
    """Debug helper: /api/lookup?title=Animal+Friends&type=movie[&tt=tt1234567]
    Shows exactly what auto-discovery finds for one title, plus the row that
    would be generated from it. Use this to verify enrichment after a deploy."""
    title = request.args.get('title', '').strip()
    tt = request.args.get('tt', '').strip()
    kind = request.args.get('type', 'movie').lower()
    if not (title or tt):
        return jsonify({'error': 'pass ?title= or ?tt='}), 400
    kind_n = _norm_kind(kind)
    if kind_n in TFX_KINDS and TFX_OK:
        meta = fetch_brand(title)
        row = create_tfx_row(title, kind_n, dict(meta))
        row.pop('_tfx_schema', None)
        return jsonify({'discovered': meta, 'row': row})
    if 'publisher' in kind:
        meta = fetch_brand(title)
        row = make_row(title, False, '', dict(meta), publisher=True)
        return jsonify({'discovered': meta, 'row': row})
    if 'talent' in kind:
        meta = fetch_person(title)
        row = make_row(title, False, '', dict(meta), talent=True)
        return jsonify({'discovered': meta, 'row': row})
    if 'game' in kind:
        meta = fetch_game(title)
        row = make_row(title, False, '', dict(meta), game=True)
        return jsonify({'discovered': meta, 'row': row})
    is_movie = 'tv' not in kind
    yr = request.args.get('released_on', '') or request.args.get('year', '')
    if tt:
        meta = fetch_metadata_by_tt(tt, is_movie, title, year_hint=yr)
    else:
        meta = fetch_metadata(title, is_movie, year_hint=yr)
    row = make_row(title or tt, is_movie, '', dict(meta))
    return jsonify({'discovered': meta, 'row': row})


def _preview_payload(rows, preview_limited):
    """Shape enriched rows into the JSON the preview panel renders."""
    # preview shows the schema of the first title's category
    def _kind(r):
        return (r.get('_tfx_schema') or
                ('talent' if _is_talent_row(r) else
                 'game' if _is_game_row(r) else
                 'publisher' if _is_publisher_row(r) else
                 'tv' if _is_tv_row(r) else 'movie'))
    first = _kind(rows[0])
    cols = {'talent': TALENT_COLUMNS, 'game': GAME_COLUMNS,
            'publisher': PUBLISHER_COLUMNS,
            'tv': TV_COLUMNS, 'movie': COLUMNS,
            **({k: v for k, v in _TFX_COLUMNS.items()} if TFX_OK else {})}[first]
    same = [r for r in rows if _kind(r) == first]
    df = pd.DataFrame(same)
    df = df.reindex(columns=cols).where(lambda x: pd.notnull(x), '')
    return {
        'total_rows': len(df),
        'preview': df.head(4).to_dict('records'),
        'columns': list(df.columns),
        'preview_limited': preview_limited,
        # rows whose professional-details hint matched nobody -- surfaced here
        # so Ops see it in Preview rather than discovering it after ingestion
        'needs_review': [{'title': r.get('title', ''),
                          'profession': r.get('_review_profession', ''),
                          'reason': r.get('_review_reason', '')}
                         for r in rows
                         if isinstance(r, dict) and r.get('_needs_review')],
    }


@app.route('/api/preview', methods=['POST'])
def preview_data():
    """Synchronous preview. Kept for backwards compatibility, but with
    Auto-discover ON the enrichment can outlive the HTTP request timeout
    (worker killed -> 502), so the UI uses /api/preview_async instead."""
    try:
        rows = collect_rows(preview=True)
        if not rows:
            return jsonify({'error': 'No titles provided'}), 400
        # figure out whether the source had more titles than we sampled
        if request.files.get('file'):
            preview_limited = True
        else:
            src = len((request.get_json(silent=True) or {}).get('titles', []))
            preview_limited = src > PREVIEW_MAX_TITLES
        return jsonify(_preview_payload(rows, preview_limited))
    except Exception as e:
        logging.error(f"Error previewing data: {str(e)}")
        return jsonify({'error': f"Error: {str(e)}"}), 500


@app.route('/api/generate', methods=['POST'])
def generate_excel():
    try:
        rows = collect_rows()
        if not rows:
            return jsonify({'error': 'No titles provided'}), 400
        output = _rows_to_workbook(rows)
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'Titles_Export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        )
    except Exception as e:
        logging.error(f"Error generating Excel: {str(e)}")
        return jsonify({'error': f"Error: {str(e)}"}), 500


@app.route('/validator')
def validator_page():
    return render_template('validator.html',
                           default_rules=json.dumps(DEFAULT_RULES, indent=2))


@app.route('/api/validate', methods=['POST'])
def api_validate():
    try:
        if not request.files.get('file'):
            return jsonify({'error': 'Please upload a workbook (.xlsx or .csv).'}), 400
        if validate_workbook is None:
            return jsonify({'error': 'Validator module unavailable.'}), 500

        raw = request.form.get('rules')
        if request.files.get('rulesFile'):
            raw = request.files['rulesFile'].read().decode('utf-8', errors='replace')
        rules = None
        if raw and raw.strip():
            try:
                rules = json.loads(raw)
            except Exception as e:
                return jsonify({'error': f'Invalid rules JSON: {e}'}), 400

        xlsx_bytes, summary = validate_workbook(request.files['file'], rules)
        return jsonify({
            'summary': summary,
            'file_b64': base64.b64encode(xlsx_bytes).decode('ascii'),
            'filename': f"Validated_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        })
    except Exception as e:
        logging.error(f"Error validating workbook: {str(e)}")
        return jsonify({'error': f"Error: {str(e)}"}), 500


# ========================= manual-file review ============================
# Reviews a manually prepared workbook against what the tool would generate
# (ingest-template logic + auto-discovery) and returns a highlighted copy
# with a Findings sheet (gaps + mismatches + suggested values) and a Summary.

# columns that are inherently manual / not derivable -> never flagged
REVIEW_SKIP_COLS = {
    'record_type', 'brand_id', 'title', 'title_category', 'title_created_date',
    'active', 'brand_listing_hidden', 'last_reviewed', 'rovi_id',
    'title_content_windows', 'composite_brand_set', 'iso_mic', 'stock_exchange',
    'box_office', 'street_date', 'gross_screen', 'opening_weekend_box_office',
    'domestic_opening_weekend_box_office', 'domestic_opening_weekend_screens',
    'domestic_opening_weekend_rank', 'facebook_verified', 'twitter_verified',
}


def _review_norm(v):
    """Comparison form: trimmed lines, scheme-insensitive URLs, no blanks."""
    s = str(v if v is not None else '').strip()
    if s.lower() in ('nan', 'none'):
        return ''
    s = s.replace('\r\n', '\n')
    s = '\n'.join(ln.strip() for ln in s.split('\n') if ln.strip())
    return re.sub(r'^https://', 'http://', s, flags=re.M)


def _sub_parts(sub):
    return {l.split(' - ', 1)[0].strip(): l.split(' - ', 1)[1].strip()
            for l in str(sub or '').split('\n') if ' - ' in l}


# ============ column-aware review rules (reviewer feedback, Jul 2026) ============
# Encodes the reviewed-file comments so curated values that are valid
# alternatives are no longer flagged as Mismatch.

_FANPAGE_RE = re.compile(r'fan[\s_-]?(page|club)|fanpage', re.I)
# never valid in a manual value (Rule 5): /p/, /php/ and /people/ path URLs and
# profile.php URLs are not real page URLs, so such a Facebook value is not
# usable data.
_BAD_FB_MANUAL_RE = re.compile(
    r'facebook\.com/(?:p|php|people)/|facebook\.com/profile\.php', re.I)
# additionally never offered as a suggestion (unhelpful discovered URLs)
_BAD_FB_SUGG_RE = re.compile(
    r'facebook\.com/(?:p|php|people|pages)/|facebook\.com/profile\.php|facebook\.com/\d+/*$',
    re.I)


def _review_lines_ci(v):
    """Case-folded set of lines; internal whitespace collapsed and any
    '||suffix' (added-date etc.) stripped, so two values that differ only in
    line order, spacing or capitalisation compare equal."""
    out = set()
    for ln in str(v or '').split('\n'):
        ln = re.sub(r'\s+', ' ', ln.split('||', 1)[0]).strip().lower()
        if ln:
            out.add(ln)
    return out


def _review_slug(s):
    return re.sub(r'[^a-z0-9]+', '', str(s or '').lower())


def _tw_handle_key(v):
    """Normalised Twitter/X handle for case-insensitive comparison: URL prefix
    and a leading '@' removed, lower-cased -- so 'OfficialLivePD',
    'officiallivepd' and 'http://twitter.com/OfficialLivePD' all match."""
    s = str(v or '').strip().lower()
    m = re.search(r'(?:twitter|x)\.com/@?([^/?#\s]+)', s)
    if m:
        s = m.group(1)
    return s.lstrip('@').strip('/')


def _kw_terms(v):
    """Case- and structure-insensitive fingerprint of a twitter_search_term_
    keywords value: the set of quoted phrases plus the set of bare/@/# tokens,
    ignoring capitalisation, the OR/or connector, parentheses, grouping and
    pipes. Two clauses with the same terms compare equal regardless of case or
    how the terms are parenthesised."""
    s = str(v or '').lower()
    phrases = frozenset(re.sub(r'\s+', ' ', p).strip()
                        for p in re.findall(r'"([^"]*)"', s))
    s = re.sub(r'"[^"]*"', ' ', s)
    words = frozenset(w for w in re.findall(r'[@#]?[a-z0-9]+', s) if w != 'or')
    return phrases, words


def _normalize_url_protocol(url):
    """Normalize http:// and https:// to https:// for comparison."""
    if not url:
        return url
    return str(url).replace('http://', 'https://')


def _url_equiv_key(url):
    """Scheme- and trailing-slash-insensitive key for URL comparison (Rule 2).
    http vs https, a leading 'www.' and any trailing '/' are not meaningful
    differences, so 'http://www.imdb.com/title/tt1/' and
    'https://imdb.com/title/tt1' produce the same key."""
    s = str(url or '').strip().lower()
    s = re.sub(r'^https?://', '', s)
    s = re.sub(r'^www\.', '', s)
    return s.rstrip('/')


def _review_compare(col, manual_raw, expected_raw, title='', cat='',
                    manual_genre='', sub_raw=''):
    """Column-aware comparison. Returns (ok, suggested_str).

    Rules from the reviewed-file feedback:
      * title_sub_category / brand_set  - extra values are fine; only flag
        when an expected value is missing (order-insensitive subset check).
      * twitter_search_terms / youtube_channel_username / -company - curated
        terms and channels are valid alternatives ("both values are
        correct"); never flag as Mismatch (gap only).
      * genre                           - must not be blank for Movies / TV /
        video games; any curated non-blank set is fine; suggestions trimmed
        to the top 3.
      * primary_genre                   - valid if it is any one of the values
        in the row's own genre column.
      * released_on                     - discovery may pick a same-named
        title; a well-formed manual date wins.
      * wikipedia_page                  - page slug must match the title and
        must not conflict with the title/sub-category (e.g. an
        '(American_football)' page for an Actor is still an error).
      * facebook_page                   - /p/, /php/, /people/ and profile.php
        URLs are never valid (always flagged in the manual value, even when no
        value was discovered); 'Fanpage', /pages/ and bare numeric-id URLs are
        additionally never offered as a suggestion.
      * instagram_user                  - 'Fanpage' handles are ignored.
      * imdb_id / metacritic /          - compared by identifier, not exact
        rottentomatoes                    string: http vs https and a trailing
        '/' are ignored, so those variants are not a mismatch (Rule 2).
    """
    c = str(col or '').strip().lower()
    mval = _review_norm(manual_raw)
    eval_ = _review_norm(expected_raw)
    sugg = str(expected_raw if expected_raw is not None else '')

    if c == 'genre' and sugg:          # top-3 genres only in suggestions
        sugg = '\n'.join([l.strip() for l in sugg.split('\n') if l.strip()][:3])

    # Facebook hygiene (Rule 5) applies regardless of any discovered value: a
    # /p/, /php/, /people/ or profile.php URL is never usable data and is always
    # flagged, even when discovery found nothing to compare against (so the
    # "expected empty -> pass" short-circuit below can't let it through).
    if c == 'facebook_page' and mval and _BAD_FB_MANUAL_RE.search(mval):
        good = [l for l in sorted(_review_lines_ci(eval_))
                if not _BAD_FB_SUGG_RE.search(l) and not _FANPAGE_RE.search(l)]
        return False, ('\n'.join(good) if good else sugg)

    # twitter_search_term_keywords: a bare #hashtag/@handle is never a keyword --
    # it belongs in twitter_search_terms. Flag it even when discovery found no
    # expected value to compare against (mirrors the ingest platform rule).
    if c == 'twitter_search_term_keywords' and mval and any(
            ln.strip().startswith(('#', '@'))
            for ln in str(manual_raw or '').split('\n') if ln.strip()):
        return False, sugg

    if not eval_ or mval == eval_:
        return True, sugg

    man_lines, exp_lines = _review_lines_ci(mval), _review_lines_ci(eval_)

    if c in ('title_sub_category', 'brand_set'):
        return exp_lines <= man_lines, sugg

    # imdb_id / metacritic / rottentomatoes: compared by identifier, not exact
    # string (Rule 2). http vs https and a trailing '/' are not meaningful, so a
    # curated URL differing only in scheme or trailing slash is NOT a mismatch
    # (e.g. http://www.imdb.com/title/tt14125350/ == https://www.imdb.com/title/tt14125350).
    if c in ('imdb_id', 'metacritic', 'rottentomatoes'):
        return _url_equiv_key(mval) == _url_equiv_key(eval_), sugg

    if c in ('twitter_search_terms', 'youtube_channel_username',
             'youtube_channel_company'):
        # reviewer: "both values are correct" -- curated terms/channels are
        # valid alternatives; only flag when the cell is empty (gap)
        return bool(mval), sugg

    if c == 'twitter_handle':
        # capitalisation is not meaningful: OfficialLivePD == officiallivepd
        return _tw_handle_key(mval) == _tw_handle_key(eval_), sugg

    if c == 'twitter_search_term_keywords':
        # same terms in any case or grouping are equivalent ("both correct");
        # only a genuinely different term set (or an empty cell) is flagged
        return bool(mval) and _kw_terms(mval) == _kw_terms(eval_), sugg

    if c == 'network':
        # network blank is allowed (Box Office Mojo may list no distributor).
        # Accept a manual value that normalises to the same LF label as the
        # discovered one (e.g. "Walt Disney Studios Motion Pictures" -> "Disney").
        # A parent/umbrella that cannot resolve to the expected label stays flagged.
        if not mval:
            return True, sugg
        mlabel = _review_norm(_ref_normalize_network(str(manual_raw or '').strip()))
        return mlabel.lower() == eval_.lower(), sugg

    if c == 'companies':
        # DAR rows must be "Pristine Brand"; regular rows accept any non-blank
        # company (a real distributor name or the literal "Unknown").
        if str(title).strip().lower().endswith('- dar'):
            return mval.strip().lower() == 'pristine brand', 'Pristine Brand'
        return bool(mval), sugg

    if c == 'genre':
        return bool(mval), sugg

    if c == 'primary_genre':
        return bool(mval) and mval.strip().lower() in _review_lines_ci(manual_genre), sugg

    if c == 'released_on':
        return bool(re.match(r'^\d{4}-\d{2}-\d{2}', mval)), sugg

    if c == 'wikipedia_page':
        if not mval:
            return False, sugg
        base = title[:-6].strip() if str(title).endswith(' - DAR') else str(title)
        tslug = _review_slug(base)
        ttoks = set(re.findall(r'[a-z0-9]+', base.lower()))
        ctx = set(re.findall(r'[a-z0-9]+', (str(cat) + ' ' + str(sub_raw)).lower()))
        for ln in mval.split('\n'):
            slug = ln.rsplit('/', 1)[-1]
            if 'disambiguation' in slug.lower():
                continue
            # slug matches the title, or is a name-variant of it
            # (e.g. Kelsey_Asbille for 'Kelsey Asbille Chow')
            stoks = set(re.findall(r'[a-z0-9]+', re.sub(r'\([^)]*\)', '', slug).lower()))
            if not ((tslug and tslug in _review_slug(slug))
                    or (stoks and stoks <= ttoks)):
                continue
            par = re.search(r'\(([^)]*)\)', slug)
            if not par:
                return True, sugg
            ptoks = set(re.findall(r'[a-z0-9]+', par.group(1).lower()))
            if not ctx or (ptoks & ctx):
                return True, sugg
        return False, sugg

    if c == 'facebook_page':
        good = [l for l in sorted(exp_lines)
                if not _BAD_FB_SUGG_RE.search(l) and not _FANPAGE_RE.search(l)]
        if not mval:
            return (not good), ('\n'.join(good) if good else sugg)
        if any(_BAD_FB_MANUAL_RE.search(l) for l in man_lines):
            return False, '\n'.join(good)
        return True, sugg

    if c == 'instagram_user':
        good = [l for l in sorted(exp_lines) if not _FANPAGE_RE.search(l)]
        if not mval:
            return (not good), ('\n'.join(good) if good else sugg)
        if _FANPAGE_RE.search(mval):
            return False, '\n'.join(good)
        return True, sugg

    return False, sugg
# ==================================================================================


# categories handled by the original four review branches
_TFX_LEGACY_CATS = {'movies', 'tv shows', 'talent', 'video game'}


def _tfx_schema_for_row(r, cat):
    """Return 'beauty'/'beverages'/'sports'/'general' when the row belongs to one
    of the four NEW ingest schemas, else None (row keeps its legacy handling).
    Works even when title_category is blank, via sub_category/brand_set signals."""
    if not TFX_OK:
        return None
    c = str(cat or '').strip()
    if c.lower() in _TFX_LEGACY_CATS or 'game' in c.lower():
        return None
    key = _tfx_detect(r)
    if key:
        return key
    # category is present and belongs to the General master list
    if c and c in _TFX_MASTER:
        return 'general'
    return None


def build_review(src, auto_fetch=True, progress=None):
    """Review an uploaded manual workbook. Returns (xlsx_bytes, summary)."""
    df = _read_upload(src)
    lower_cols = {c.lower(): c for c in df.columns}
    records = df.to_dict('records')
    findings, fills = [], {}
    rows_reviewed = cells_checked = 0
    total = len(records)

    for i, r in enumerate(records):
        t = str(r.get(lower_cols.get('title', 'title'), '') or '').strip()
        if not t:
            if progress:
                progress(i + 1, total)
            continue
        rows_reviewed += 1
        cat = str(r.get(lower_cols.get('title_category', ''), '') or '')

        # ---- NEW SCHEMAS: Beauty / Beverages / Sports Teams / General ----
        tfx_key = _tfx_schema_for_row(r, cat)
        if tfx_key:
            fcat, fsub = _tfx_fill(r)
            cat_col = lower_cols.get('title_category')
            sub_col = lower_cols.get('title_sub_category')
            # backfill a MISSING category (auto-understood from the row) as a Gap
            if cat_col is not None:
                cells_checked += 1
                if not str(r.get(cat_col) or '').strip() and fcat:
                    findings.append(dict(row=i + 2, title=t, column=cat_col,
                                         status='Gap', current='', suggested=fcat))
                    fills[(i, cat_col)] = 'Gap'
                    r[cat_col] = fcat   # validate the rest against the filled value
            # backfill a MISSING sub-category the same way
            if sub_col is not None:
                cells_checked += 1
                if not str(r.get(sub_col) or '').strip() and fsub:
                    findings.append(dict(row=i + 2, title=t, column=sub_col,
                                         status='Gap', current='', suggested=fsub))
                    fills[(i, sub_col)] = 'Gap'
                    r[sub_col] = fsub
            # dropdown / template-logic validation
            try:
                tfx_findings = _tfx_validate(r, tfx_key, _TFX_RULES)
            except Exception as _e:
                logging.warning(f"tfx validate failed on row {i + 2}: {_e}")
                tfx_findings = []
            n_rules = len(_TFX_RULES.get('schemas', {}).get(tfx_key, {}).get('rules', []))
            cells_checked += max(n_rules - 2, 0)  # category+sub already counted
            for fd in tfx_findings:
                src_col = lower_cols.get(str(fd.get('field', '')).lower())
                if not src_col:
                    continue
                if fills.get((i, src_col)):
                    continue  # already flagged by the backfill above
                status = 'Gap' if fd.get('status') == 'gap' else 'Mismatch'
                findings.append(dict(
                    row=i + 2, title=t, column=src_col, status=status,
                    current=str(r.get(src_col) if r.get(src_col) is not None else ''),
                    suggested=str(fd.get('expected') or '')))
                fills[(i, src_col)] = status
            if progress:
                progress(i + 1, total)
            continue
        # -------------------------------------------------------------------

        is_talent = 'talent' in cat.lower()
        is_game = 'game' in cat.lower()
        is_movie = (not is_talent) and (not is_game) and 'tv' not in cat.lower()
        sub = _sub_parts(r.get(lower_cols.get('title_sub_category', '')))

        if is_game:
            meta = dict(fetch_game(t) or {}) if auto_fetch else {}
            # manual sub lines fill discovery gaps (developer / platforms)
            if not meta.get('developer') and sub.get('Developer'):
                meta['developer'] = sub['Developer']
            if not meta.get('platforms'):
                plats = [l.split(' - ', 1)[1] for l in
                         str(r.get(lower_cols.get('title_sub_category', ''), '') or '').split('\n')
                         if l.startswith('Platform - ')]
                if plats:
                    meta['platforms'] = plats
            if not meta.get('network'):
                meta['network'] = str(r.get(lower_cols.get('network', ''), '') or '').strip()
            g = str(r.get(lower_cols.get('genre', ''), '') or '').strip()
            if not meta.get('genre') and g:
                meta['genre'] = g
            expected = make_row(t, False, '', meta, game=True)
            for col in GAME_COLUMNS:
                if col in REVIEW_SKIP_COLS or col.lower() not in lower_cols:
                    continue
                src_col = lower_cols[col.lower()]
                mval = _review_norm(r.get(src_col))
                cells_checked += 1
                ok, sugg = _review_compare(
                    col, r.get(src_col), expected.get(col), title=t, cat=cat,
                    manual_genre=r.get(lower_cols.get('genre', ''), ''),
                    sub_raw=r.get(lower_cols.get('title_sub_category', ''), ''))
                if ok:
                    continue
                status = 'Gap' if not mval else 'Mismatch'
                findings.append(dict(
                    row=i + 2, title=t, column=src_col, status=status,
                    current=str(r.get(src_col) if r.get(src_col) is not None else ''),
                    suggested=sugg))
                fills[(i, src_col)] = status
            if progress:
                progress(i + 1, total)
            continue

        if is_talent:
            meta = dict(fetch_person(t) or {}) if auto_fetch else {}
            # manual sub-category lines fill classification gaps
            if not meta.get('gender') and sub.get('Gender'):
                meta['gender'] = 'Gender - ' + sub['Gender']
            if sub.get('Talent Type') or sub.get('Talent Subtype'):
                lines = [x for x in (
                    ('Talent Subtype - ' + sub['Talent Subtype']) if sub.get('Talent Subtype') else '',
                    ('Gender - ' + sub['Gender']) if sub.get('Gender') else '',
                    ('Talent Type - ' + sub['Talent Type']) if sub.get('Talent Type') else '') if x]
                if not (meta.get('occupations') or meta.get('sports')):
                    meta['title_sub_category'] = '\n'.join(lines)
            expected = make_row(t, False, '', meta, talent=True)
            for col in TALENT_COLUMNS:
                if col in REVIEW_SKIP_COLS or col.lower() not in lower_cols:
                    continue
                src_col = lower_cols[col.lower()]
                mval = _review_norm(r.get(src_col))
                cells_checked += 1
                ok, sugg = _review_compare(
                    col, r.get(src_col), expected.get(col), title=t, cat=cat,
                    manual_genre=r.get(lower_cols.get('genre', ''), ''),
                    sub_raw=r.get(lower_cols.get('title_sub_category', ''), ''))
                if ok:
                    continue
                status = 'Gap' if not mval else 'Mismatch'
                findings.append(dict(
                    row=i + 2, title=t, column=src_col, status=status,
                    current=str(r.get(src_col) if r.get(src_col) is not None else ''),
                    suggested=sugg))
                fills[(i, src_col)] = status
            if progress:
                progress(i + 1, total)
            continue

        # soft hints from the manual row: fill discovery gaps, never override
        hints = {}
        if is_movie:
            if sub.get('Release'):
                hints['release_scale'] = sub['Release']
        else:
            if sub.get('Program Type'):
                hints['program_type'] = sub['Program Type']
        if sub.get('Language Type'):
            hints['original_language'] = 'en' if sub['Language Type'] == 'English' else 'xx'
        rel = str(r.get(lower_cols.get('released_on', ''), '') or '').strip()
        if rel and rel.lower() != 'nan':
            hints['released_on'] = rel[:10]

        meta = {}
        if auto_fetch:
            rel_year = hints.get('released_on', '')  # check the release date first
            tt = re.search(r'tt\d{5,}', str(r.get(lower_cols.get('imdb_id', ''), '') or ''))
            if tt:
                meta = dict(fetch_metadata_by_tt(tt.group(0), is_movie, t, year_hint=rel_year) or {})
            else:
                meta = dict(fetch_metadata(t, is_movie, year_hint=rel_year) or {})
        for k, v in hints.items():
            if meta.get(k) in (None, ''):
                meta[k] = v
        # date-first IMDb: surface the resolver's year note as a review finding
        _imdb_note = meta.pop('_imdb_year_note', '')
        if _imdb_note:
            findings.append(dict(
                row=i + 2, title=t, column='imdb_id', status='Mismatch',
                current=str(r.get(lower_cols.get('imdb_id', ''), '') or ''),
                suggested=_imdb_note))

        exp_net = str(meta.get('network') or
                      r.get(lower_cols.get('network', ''), '') or '').strip()
        if exp_net and not meta.get('network'):
            meta['network'] = exp_net
        expected = make_row(t, is_movie, exp_net, meta)

        for col in (COLUMNS if is_movie else TV_COLUMNS):
            if col in REVIEW_SKIP_COLS or col.lower() not in lower_cols:
                continue
            src_col = lower_cols[col.lower()]
            mval = _review_norm(r.get(src_col))
            cells_checked += 1
            ok, sugg = _review_compare(
                col, r.get(src_col), expected.get(col), title=t, cat=cat,
                manual_genre=r.get(lower_cols.get('genre', ''), ''),
                sub_raw=r.get(lower_cols.get('title_sub_category', ''), ''))
            if ok:
                continue
            status = 'Gap' if not mval else 'Mismatch'
            findings.append(dict(
                row=i + 2, title=t, column=src_col, status=status,
                current=str(r.get(src_col) if r.get(src_col) is not None else ''),
                suggested=sugg))
            fills[(i, src_col)] = status
        if progress:
            progress(i + 1, total)

    # ---------------- build the output workbook ----------------
    import openpyxl as _oxl
    from openpyxl.styles import PatternFill, Font, Alignment
    from openpyxl.utils import get_column_letter

    RED = PatternFill('solid', start_color='FFFFC7CE')      # mismatch
    AMBER = PatternFill('solid', start_color='FFFFEB9C')    # gap
    HDR = PatternFill('solid', start_color='FF1F2A44')
    HDR_FONT = Font(color='FFFFFFFF', bold=True)

    wb = _oxl.Workbook()

    # Summary
    ws = wb.active
    ws.title = 'Summary'
    gaps = sum(1 for f in findings if f['status'] == 'Gap')
    mism = len(findings) - gaps
    ws.append(['Manual File Review — Findings Summary'])
    ws['A1'].font = Font(bold=True, size=14)
    ws.append([])
    for k, v in [('Reviewed at', datetime.now().strftime('%Y-%m-%d %H:%M')),
                 ('Auto-discovery', 'ON' if auto_fetch else 'OFF'),
                 ('Rows reviewed', rows_reviewed),
                 ('Cells checked', cells_checked),
                 ('Cells OK', cells_checked - len(findings)),
                 ('Gaps (empty, value suggested)', gaps),
                 ('Mismatches (differs from expected)', mism)]:
        ws.append([k, v])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
    ws.append([])
    ws.append(['Legend'])
    ws.cell(ws.max_row, 1).font = Font(bold=True)
    ws.append(['Amber cell', 'Gap — the tool found a value your file is missing'])
    ws.cell(ws.max_row, 1).fill = AMBER
    ws.append(['Red cell', 'Mismatch — differs from template/discovered value (see Findings)'])
    ws.cell(ws.max_row, 1).fill = RED
    ws.append(['Reviewed sheet', "Each record has two rows: 'INGESTED' (suggested corrections applied) "
               "and 'FROM DB' (original file values) for side-by-side comparison"])
    ws.cell(ws.max_row, 1).font = Font(bold=True)
    from collections import Counter as _Counter
    by_col = _Counter(f['column'] for f in findings)
    if by_col:
        ws.append([])
        ws.append(['Findings by column'])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
        for col, n in by_col.most_common():
            ws.append([col, n])
    ws.column_dimensions['A'].width = 36
    ws.column_dimensions['B'].width = 64

    # Reviewed copy with highlights.
    # For every reviewed record we now emit TWO adjacent rows so the suggested
    # (to-be-ingested) data and the original data can be compared in one sheet:
    #   record_type = 'INGESTED' -> the record with all suggested changes
    #                               applied (correct value where flagged,
    #                               otherwise the original file value)
    #   record_type = 'FROM DB'  -> the exact values from the uploaded file
    # INGESTED is written first, FROM DB directly below it. Existing red/amber
    # color coding is kept, applied to the flagged cells on both rows so the
    # difference is easy to spot.
    ws2 = wb.create_sheet('Reviewed')
    cols = list(df.columns)

    # Per-cell original + suggested values, captured at compare time. Using the
    # findings list (rather than the possibly-mutated `records`) guarantees the
    # 'FROM DB' row shows the true original value even for backfilled gaps.
    currents_by_cell, suggs_by_cell = {}, {}
    for f in findings:
        key = (f['row'] - 2, f['column'])
        currents_by_cell[key] = f['current']
        suggs_by_cell[key] = f['suggested']

    # Ensure there is a record_type column to label the two rows.
    rt_col = lower_cols.get('record_type')
    if rt_col is None:
        rt_col = 'record_type'
        cols = [rt_col] + cols

    def _clean(v):
        return '' if (v is None or str(v) == 'nan') else v

    ws2.append(cols)
    for c in range(1, len(cols) + 1):
        cell = ws2.cell(1, c)
        cell.fill, cell.font = HDR, HDR_FONT

    out_row = 1
    for i, r in enumerate(records):
        # --- INGESTED row (first): original values + suggested corrections ---
        out_row += 1
        ing_vals = []
        for c in cols:
            if c == rt_col:
                ing_vals.append('INGESTED')
            elif (i, c) in suggs_by_cell and str(suggs_by_cell[(i, c)]) != '':
                ing_vals.append(_clean(suggs_by_cell[(i, c)]))
            elif (i, c) in currents_by_cell:
                ing_vals.append(_clean(currents_by_cell[(i, c)]))
            else:
                ing_vals.append(_clean(r.get(c)))
        ws2.append(ing_vals)
        for j, c in enumerate(cols, start=1):
            st = fills.get((i, c))
            if st:
                ws2.cell(out_row, j).fill = RED if st == 'Mismatch' else AMBER

        # --- FROM DB row (second): exact original file values ---
        out_row += 1
        db_vals = []
        for c in cols:
            if c == rt_col:
                db_vals.append('FROM DB')
            elif (i, c) in currents_by_cell:
                db_vals.append(_clean(currents_by_cell[(i, c)]))
            else:
                db_vals.append(_clean(r.get(c)))
        ws2.append(db_vals)
        for j, c in enumerate(cols, start=1):
            st = fills.get((i, c))
            if st:
                ws2.cell(out_row, j).fill = RED if st == 'Mismatch' else AMBER
    ws2.freeze_panes = 'A2'

    # Findings detail
    ws3 = wb.create_sheet('Findings')
    ws3.append(['Row', 'Title', 'Column', 'Type', 'Current Value', 'Suggested Value'])
    for c in range(1, 7):
        cell = ws3.cell(1, c)
        cell.fill, cell.font = HDR, HDR_FONT
    for f in findings:
        ws3.append([f['row'], f['title'], f['column'], f['status'],
                    f['current'], f['suggested']])
        ws3.cell(ws3.max_row, 4).fill = RED if f['status'] == 'Mismatch' else AMBER
        for c in (5, 6):
            ws3.cell(ws3.max_row, c).alignment = Alignment(wrap_text=True, vertical='top')
    widths = [6, 34, 26, 11, 60, 60]
    for c, w in enumerate(widths, start=1):
        ws3.column_dimensions[get_column_letter(c)].width = w
    ws3.freeze_panes = 'A2'
    ws3.auto_filter.ref = f"A1:F{max(ws3.max_row, 1)}"

    out = BytesIO()
    wb.save(out)
    summary = {'rows': rows_reviewed, 'cells_checked': cells_checked,
               'gaps': gaps, 'mismatches': mism, 'ok': cells_checked - len(findings)}
    return out.getvalue(), summary


@app.route('/review')
def review_page():
    warm_upcoming()
    return render_template('review.html')


@app.route('/api/review_async', methods=['POST'])
def review_async():
    """Kick off a manual-file review in the background; returns a job id."""
    _prune_jobs()
    if not request.files.get('file'):
        return jsonify({'error': 'Please upload the manually prepared .xlsx or .csv file.'}), 400
    f = request.files['file']
    payload = {
        'bytes': f.read(), 'filename': f.filename,
        'auto_fetch': request.form.get('autoFetch', 'true').lower() != 'false',
    }
    jid = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        _JOBS[jid] = {'status': 'running', 'done': 0, 'total': 0, 'error': None,
                      'file': None, 'filename': None, 'rows': None,
                      'summary': None, 'created': time.time()}
    threading.Thread(target=_run_generation, args=(jid, 'review', payload),
                     daemon=True).start()
    return jsonify({'job_id': jid})


# ============================ background jobs =============================
# In-memory job store for full-file generation. Runs in a daemon thread so long
# auto-discovery runs don't hit the request timeout. IMPORTANT: run gunicorn with
# a SINGLE worker + threads so this store is shared, e.g.:
#   web: gunicorn app:app --workers 1 --threads 8 --timeout 120
_JOBS = {}
_JOBS_LOCK = threading.Lock()
_JOB_TTL = 1800  # seconds to keep a finished job's file in memory


def _job_set(jid, **kw):
    with _JOBS_LOCK:
        if jid in _JOBS:
            _JOBS[jid].update(kw)


def _prune_jobs():
    now = time.time()
    with _JOBS_LOCK:
        for k in [k for k, v in _JOBS.items() if now - v.get('created', now) > _JOB_TTL]:
            _JOBS.pop(k, None)


def _run_generation(jid, kind, payload):
    try:
        def prog(done, total):
            _job_set(jid, done=done, total=total)

        if kind == 'review':
            data, summary = build_review((payload['bytes'], payload['filename']),
                                         payload['auto_fetch'], progress=prog)
            _job_set(jid, status='done', file=data, rows=summary['rows'],
                     summary=summary,
                     filename=f"Reviewed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
            return
        max_titles = PREVIEW_MAX_TITLES if payload.get('preview') else None
        if kind == 'file':
            rows = build_rows_from_upload(
                (payload['bytes'], payload['filename']),
                payload['include_dar'], payload['auto_fetch'], progress=prog,
                max_titles=max_titles,
                default_kind=payload.get('title_type', 'movie'),
                default_profession=payload.get('talent_profession', ''))
        else:
            rows = build_rows_from_titles(payload['data'], max_titles=max_titles,
                                          progress=prog)

        if not rows:
            _job_set(jid, status='error', error='No titles provided')
            return
        if payload.get('preview'):
            _job_set(jid, status='done',
                     preview=_preview_payload(rows, payload.get('preview_limited', False)),
                     rows=len(rows))
            return
        # how many rows actually got social/discovery data -- surfaces
        # rate-limit problems instead of silently exporting blank socials
        _soc = ('twitter_handle', 'instagram_user', 'facebook_page',
                'youtube_channel_username', 'wikipedia_page', 'tiktok_user')
        enriched = sum(1 for r in rows
                       if any(str(r.get(c) or '').strip() for c in _soc))
        out = _rows_to_workbook(rows)
        _job_set(jid, status='done', file=out.getvalue(), rows=len(rows),
                 enriched=enriched,
                 filename=f"Titles_Export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
    except Exception as e:  # noqa: BLE001
        logging.error(f"generation job {jid} failed: {e}")
        _job_set(jid, status='error', error=str(e))


@app.route('/api/generate_async', methods=['POST'])
def generate_async():
    """Kick off full-file generation in the background; returns a job id."""
    _prune_jobs()
    jid = uuid.uuid4().hex[:12]
    if request.files.get('file'):
        f = request.files['file']
        payload = {
            'bytes': f.read(), 'filename': f.filename,
            'include_dar': request.form.get('includeDar', 'true').lower() != 'false',
            'auto_fetch': request.form.get('autoFetch', 'false').lower() == 'true',
            'title_type': _norm_kind(request.form.get('titleType')),
            'talent_profession': request.form.get('talentProfession', ''),
        }
        kind = 'file'
    else:
        payload = {'data': request.get_json(silent=True) or {}}
        kind = 'titles'
    with _JOBS_LOCK:
        _JOBS[jid] = {'status': 'running', 'done': 0, 'total': 0, 'error': None,
                      'file': None, 'filename': None, 'rows': None, 'created': time.time()}
    threading.Thread(target=_run_generation, args=(jid, kind, payload), daemon=True).start()
    return jsonify({'job_id': jid})


@app.route('/api/preview_async', methods=['POST'])
def preview_async():
    """Kick off a preview (first PREVIEW_MAX_TITLES titles, incl. auto-discover
    enrichment) in the background; returns a job id. Poll /api/job/<jid> --
    when done the job carries a 'preview' payload. This keeps long
    auto-discovery lookups out of the HTTP request, which the platform
    kills after ~30s (the old 502 'server timed out' error)."""
    _prune_jobs()
    jid = uuid.uuid4().hex[:12]
    if request.files.get('file'):
        f = request.files['file']
        payload = {
            'bytes': f.read(), 'filename': f.filename,
            'include_dar': request.form.get('includeDar', 'true').lower() != 'false',
            'auto_fetch': request.form.get('autoFetch', 'false').lower() == 'true',
            'title_type': _norm_kind(request.form.get('titleType')),
            'talent_profession': request.form.get('talentProfession', ''),
            'preview': True, 'preview_limited': True,
        }
        kind = 'file'
    else:
        data = request.get_json(silent=True) or {}
        n_src = len([t for t in data.get('titles', []) if t and t.strip()])
        payload = {'data': data, 'preview': True,
                   'preview_limited': n_src > PREVIEW_MAX_TITLES}
        kind = 'titles'
    with _JOBS_LOCK:
        _JOBS[jid] = {'status': 'running', 'done': 0, 'total': 0, 'error': None,
                      'file': None, 'filename': None, 'rows': None, 'created': time.time()}
    threading.Thread(target=_run_generation, args=(jid, kind, payload), daemon=True).start()
    return jsonify({'job_id': jid})


@app.route('/api/job/<jid>')
def job_status(jid):
    with _JOBS_LOCK:
        j = _JOBS.get(jid)
        if not j:
            return jsonify({'error': 'Unknown or expired job'}), 404
        eta = None
        if j['status'] == 'running' and j.get('done') and j.get('total'):
            elapsed = time.time() - j.get('created', time.time())
            eta = max(0, int(elapsed / j['done'] * (j['total'] - j['done'])))
        return jsonify({'status': j['status'], 'done': j['done'], 'total': j['total'],
                        'error': j['error'], 'rows': j.get('rows'),
                        'summary': j.get('summary'), 'eta_seconds': eta,
                        'preview': j.get('preview'),
                        'enriched': j.get('enriched')})


@app.route('/api/job/<jid>/download')
def job_download(jid):
    with _JOBS_LOCK:
        j = _JOBS.get(jid)
        if not j or j['status'] != 'done' or not j['file']:
            return jsonify({'error': 'File not ready'}), 404
        data, fn = j['file'], j['filename']
    return send_file(BytesIO(data),
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name=fn)


if __name__ == '__main__':
    app.run(debug=True)
