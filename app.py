from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd
from io import BytesIO
from datetime import datetime
import re
import time
import uuid
import threading
import logging

try:
    from metadata_fetcher import fetch_metadata, fetch_metadata_by_tt
except Exception:  # keep the app running even if the module is missing
    def fetch_metadata(title, is_movie=True):
        return {}

    def fetch_metadata_by_tt(tt, is_movie=True, title=""):
        return {}

import json
import base64

import types as _types

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
    "Warner Bros.": "Language Type - English\nRelease - Wide\nStudio - Major",
    "Sony / Columbia": "Release - Wide\nStudio - Independent",
    "Amazon MGM Studios": "Release - Wide\nStudio - Independent",
    "AMC Network": "Release - Wide\nStudio - Independent",
    "Neon": "Language Type - English\nRelease - Wide\nStudio - Independent",
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
    """Lowercase and strip everything except letters/digits (for hashtags)."""
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


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
      - the title's OWN channel URL alone on the first line (if it has one)
      - then '<network channel>|<title variant>' for each title variant
    """
    lines = []
    own = (own_channel or "").strip()
    if own and own != (company_channel or "").strip():
        lines.append(own)
    if company_channel:
        lines.extend(f"{company_channel}|{v}" for v in _title_variants(clean_title))
    return "\n".join(lines)


def generate_search_terms(clean_title, network, year, is_dar, twitter_handle=""):
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

    # twitter_search_term_keywords
    inner = []
    if network:
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


def _tv_keywords_and_reddit(title, network, year, program_type, is_dar):
    """twitter_search_term_keywords + reddit_search_terms.
    Base rows of Specials use the short 'tonight' pattern; DAR rows and
    everything else use the full pattern (per the manual Ops file)."""
    info = _tv_net(network)
    title = _strip_disambiguator(title)
    tail = "DAR|DAR|2021-01-01" if is_dar else "Operations - Core Title|Operations - Core Title"
    if program_type == "Special" and not is_dar:
        inner = f'tonight OR watch OR tv OR show OR program OR "{network}"'
        kw = f'("{title}")({inner})|{tail}'
    else:
        clause = info.get("clause")
        if not clause and network:
            clause = f'"{network}" OR @{_camel(network).lower()} OR #{_camel(network)}'
        parts = [p for p in (clause, f'"{year}"' if year else "", _TV_KEYWORD_TAIL) if p]
        inner = " OR ".join(parts)
        kw = f'("{title}") ({inner})|{tail}'
    # reddit: same content, ' | ' separators, '+' dropped, no Ops label
    r_inner = re.sub(r'\s+(?:OR|or)\s+', ' | ', inner)
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

    # ---- title_sub_category (4 lines) ----
    ptype = str(metadata.get('program_type') or '').strip() or "Series"
    tier = info.get("tier") or "Streaming"
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
        for extra in (info.get("corp"), info.get("extras")):
            if extra:
                brand_set += "\n" + extra
    else:
        companies = info.get("companies") or "Unknown"
        brand_set = "Competitive View"

    # ---- genre / primary ----
    _genre = metadata.get('genre', '')
    if REF is not None and _genre:
        _genre, _ = REF.normalize_genres(_genre)
    _primary = str(metadata.get('primary_genre') or '').strip()
    if not _primary:
        _primary = _tv_primary_genre([g for g in str(_genre).split("\n") if g])

    # ---- youtube / search terms ----
    _yt = str(metadata.get('youtube_channel_username') or '').strip()
    if not _yt and info.get("yt"):
        _yt = _tv_youtube_username(info["yt"], clean_title)
    rel = str(metadata.get('released_on') or '')
    year = rel[:4] if rel[:4].isdigit() else ''
    gen_terms = _tv_search_terms(clean_title, eff_network, is_dar)
    gen_kw, gen_reddit = _tv_keywords_and_reddit(clean_title, eff_network, year,
                                                 ptype, is_dar)

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
        'ticker_symbol': mv('ticker_symbol', info.get("ticker", "")),
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


def make_row(title, is_movie, network="", metadata=None):
    """Dispatch: movies use the 42-col schema, TV the 39-col BrandIngest."""
    if is_movie:
        return create_row(title, is_movie, network, metadata)
    return create_tv_row(title, network, metadata)


def create_row(title, is_movie, network="", metadata=None):
    """Create a data row for a title - ALL 42 COLUMNS POPULATED.

    Any value present in `metadata` overrides the computed default, so an
    uploaded row's channels (and every other field) are preserved.
    """
    metadata = metadata or {}
    is_dar = " - DAR" in title
    clean_title = re.sub(r"\s*-\s*DAR\s*$", "", title, flags=re.IGNORECASE).strip()
    title_category = "Movies" if is_movie else "TV Shows"

    # Effective network = discovered/explicit network, else the passed arg;
    # normalise raw distributor -> LF network label (e.g. "Lionsgate" -> "Lionsgate / Summit").
    eff_network = (str(metadata.get('network') or network or '')).strip()
    if REF is not None and eff_network:
        eff_network = REF.normalize_network(eff_network)

    # sub-category is shared by the base title AND its DAR twin
    _sub_explicit = str(metadata.get('title_sub_category') or '').strip()
    _sub = _sub_explicit
    if not _sub and REF is not None:
        _sub = REF.subcategory_for(eff_network)
    if not _sub:
        _sub = 'Release - Limited\nStudio - Independent'
    # the upcoming-release-movies calendar knows the actual Wide/Limited scale;
    # it overrides the per-network default (but never an explicit upload value)
    _scale = str(metadata.get('release_scale') or '').strip().title()
    if _scale in ('Wide', 'Limited') and not _sub_explicit:
        _sub = re.sub(r'Release - (Wide|Limited)', 'Release - ' + _scale, _sub)
    is_wide = 'release - wide' in _sub.lower()

    # curated PARENT company of the network (e.g. Warner Bros. -> Warner Bros. Pictures)
    parent = REF.companies_for(eff_network) if (REF is not None and eff_network) else ""

    if is_dar:
        companies = "Pristine Brand"
        if is_movie:
            brand_set = "LF // Film - Majors + Independents\nPristine DAR Brands"
        else:
            brand_set = "Pristine DAR Brands"
        # major-studio DAR rows also carry the corporate roll-up brand sets
        rollup = REF.dar_rollup_for(parent) if (REF is not None and parent) else ""
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
        clean_title, eff_network, year, is_dar,
        twitter_handle=str(metadata.get('twitter_handle') or ''))

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
    if not _yt_company and REF is not None and eff_network:
        _yt_company = REF.youtube_for(eff_network)
    _yt_username = str(metadata.get('youtube_channel_username') or '').strip()
    if not _yt_username:
        _yt_username = build_youtube_username(
            _yt_company, clean_title,
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
    tt = re.search(r"tt\d{5,}", str(base.get('imdb_id') or base.get('imdb_url') or ''))
    if tt:
        discovered = fetch_metadata_by_tt(tt.group(0), is_movie, title) or {}
    else:
        discovered = fetch_metadata(title, is_movie) or {}
    merged = dict(discovered)
    for k, v in base.items():
        if v not in (None, ''):
            merged[k] = v
    return merged


def build_rows_from_upload(src, include_dar, auto_fetch=False, max_titles=None, progress=None):
    """Turn an uploaded file into fully-populated rows.

    max_titles caps titles processed BEFORE lookups (keeps Preview fast).
    progress(done, total) is called after each source title (for job progress).
    """
    df = _read_upload(src)
    lower_cols = {c.lower(): c for c in df.columns}
    has_full_schema = any(col in lower_cols for col in SOCIAL_COLUMNS + ['record_type', 'brand_id'])

    rows = []
    if has_full_schema:
        all_cols = list(dict.fromkeys(COLUMNS + TV_COLUMNS))
        rename_map = {lower_cols[c.lower()]: c for c in all_cols if c.lower() in lower_cols}
        df = df.rename(columns=rename_map)
        df = df.where(pd.notnull(df), '')
        records = df.to_dict('records')
        if max_titles:
            records = records[:max_titles]
        total = len(records)
        for i, r in enumerate(records):
            t = str(r.get('title', '')).strip()
            if not t:
                if progress:
                    progress(i + 1, total)
                continue
            is_movie_r = 'tv' not in str(r.get('title_category', '')).lower()
            if auto_fetch:
                r = _merge_meta(r, t, True, is_movie=is_movie_r)
            # route through make_row so derived fields (network label, youtube
            # lines, brand sets, search terms) are computed consistently; explicit
            # values from the upload always win inside the row builders
            rows.append(make_row(t, is_movie_r, str(r.get('network') or ''), r))
            if progress:
                progress(i + 1, total)
    else:
        title_col = lower_cols.get('title') or df.columns[0]
        type_col = lower_cols.get('type') or lower_cols.get('title_category')
        network_col = lower_cols.get('network')
        specs = []
        for _, r in df.iterrows():
            title = str(r[title_col]).strip()
            if not title:
                continue
            if max_titles and len(specs) >= max_titles:
                break
            is_movie = True
            if type_col:
                is_movie = 'tv' not in str(r[type_col]).strip().lower()
            network = str(r[network_col]).strip() if network_col else ''
            specs.append((title, is_movie, network))
        total = len(specs)
        for i, (title, is_movie, network) in enumerate(specs):
            meta = _merge_meta({}, title, auto_fetch, is_movie=is_movie)
            rows.append(make_row(title, is_movie, network, meta))
            if include_dar and ' - DAR' not in title:
                rows.append(make_row(f"{title} - DAR", is_movie, network, meta))
            if progress:
                progress(i + 1, total)
    return rows


def build_rows_from_titles(data, max_titles=None, progress=None):
    """Build rows from a manual titles payload (JSON)."""
    titles = [t.strip() for t in data.get('titles', []) if t and t.strip()]
    if max_titles:
        titles = titles[:max_titles]
    include_dar = data.get('includeDar', True)
    auto_fetch = bool(data.get('autoFetch', False))
    total = len(titles)
    rows = []
    for i, title in enumerate(titles):
        is_movie = data.get('titles_type', {}).get(title, 'movie') == 'movie'
        network = data.get('networks', {}).get(title, '')
        metadata = _merge_meta(data.get('metadata', {}).get(title, {}), title, auto_fetch, is_movie=is_movie)
        rows.append(make_row(title, is_movie, network, metadata))
        if include_dar and ' - DAR' not in title:
            rows.append(make_row(f"{title} - DAR", is_movie, network, metadata))
        if progress:
            progress(i + 1, total)
    return rows


def _is_tv_row(r):
    return str(r.get('title_category', '')).lower() == 'tv shows'


def _rows_to_workbook(rows):
    """Write rows to an xlsx BytesIO. Movies use the 42-col schema, TV the
    39-col BrandIngest schema; mixed runs get one sheet per schema."""
    movies = [r for r in rows if not _is_tv_row(r)]
    tv = [r for r in rows if _is_tv_row(r)]
    if movies and tv:
        sheets = [('Movies', movies, COLUMNS), ('TV Shows', tv, TV_COLUMNS)]
    elif tv:
        sheets = [('BrandIngest', tv, TV_COLUMNS)]
    else:
        sheets = [('Sheet1', movies, COLUMNS)]
    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as xw:
        for name, rws, cols in sheets:
            df = pd.DataFrame(rws).reindex(columns=cols)
            df = df.where(pd.notnull(df), '')
            df.to_excel(xw, sheet_name=name, index=False)
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
        rows = build_rows_from_upload(request.files['file'], include_dar, auto_fetch,
                                      max_titles=max_titles)
    else:
        data = request.get_json(silent=True) or {}
        rows = build_rows_from_titles(data, max_titles=max_titles)
    return rows


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/lookup')
def api_lookup():
    """Debug helper: /api/lookup?title=Animal+Friends&type=movie[&tt=tt1234567]
    Shows exactly what auto-discovery finds for one title, plus the row that
    would be generated from it. Use this to verify enrichment after a deploy."""
    title = request.args.get('title', '').strip()
    tt = request.args.get('tt', '').strip()
    is_movie = request.args.get('type', 'movie').lower() != 'tv'
    if not (title or tt):
        return jsonify({'error': 'pass ?title= or ?tt='}), 400
    if tt:
        meta = fetch_metadata_by_tt(tt, is_movie, title)
    else:
        meta = fetch_metadata(title, is_movie)
    row = make_row(title or tt, is_movie, '', dict(meta))
    return jsonify({'discovered': meta, 'row': row})


@app.route('/api/preview', methods=['POST'])
def preview_data():
    try:
        rows = collect_rows(preview=True)
        if not rows:
            return jsonify({'error': 'No titles provided'}), 400
        # preview shows the schema of the first title's category
        cols = TV_COLUMNS if _is_tv_row(rows[0]) else COLUMNS
        same = [r for r in rows if _is_tv_row(r) == _is_tv_row(rows[0])]
        df = pd.DataFrame(same)
        df = df.reindex(columns=cols).where(lambda x: pd.notnull(x), '')
        # figure out whether the source had more titles than we sampled
        if request.files.get('file'):
            preview_limited = True
        else:
            src = len((request.get_json(silent=True) or {}).get('titles', []))
            preview_limited = src > PREVIEW_MAX_TITLES
        return jsonify({
            'total_rows': len(df),
            'preview': df.head(4).to_dict('records'),
            'columns': list(df.columns),
            'preview_limited': preview_limited,
        })
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

        if kind == 'file':
            rows = build_rows_from_upload(
                (payload['bytes'], payload['filename']),
                payload['include_dar'], payload['auto_fetch'], progress=prog)
        else:
            rows = build_rows_from_titles(payload['data'], progress=prog)

        if not rows:
            _job_set(jid, status='error', error='No titles provided')
            return
        out = _rows_to_workbook(rows)
        _job_set(jid, status='done', file=out.getvalue(), rows=len(rows),
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


@app.route('/api/job/<jid>')
def job_status(jid):
    with _JOBS_LOCK:
        j = _JOBS.get(jid)
        if not j:
            return jsonify({'error': 'Unknown or expired job'}), 404
        return jsonify({'status': j['status'], 'done': j['done'], 'total': j['total'],
                        'error': j['error'], 'rows': j.get('rows')})


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
