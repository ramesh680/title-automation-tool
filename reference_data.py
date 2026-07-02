"""
reference_data.py
-----------------
ListenFirst internal reference tables, learned from the manually-prepared
BrandDefinition report. These encode conventions that are NOT available from any
public API:

  * NETWORK_TO_COMPANIES  : LF network label -> parent "companies" value
  * NETWORK_TO_YOUTUBE    : LF network label -> distributor YouTube channel
  * NETWORK_TO_MANAGER    : LF network label -> url_managers team
  * NETWORK_LABEL         : raw distributor (IMDb/BOM/TMDB) -> LF network label
  * GENRE_FIX             : IMDb/TMDB genre token -> LF genre token

Extend these as new distributors appear. Keys are matched case-insensitively.
"""

# raw distributor name (as returned by BOM / IMDb / TMDB) -> LF network label
NETWORK_LABEL = {
    "lionsgate": "Lionsgate / Summit",
    "summit entertainment": "Lionsgate / Summit",
    "lionsgate films": "Lionsgate / Summit",
    "columbia pictures": "Sony / Columbia",
    "sony pictures releasing": "Sony / Columbia",
    "sony pictures": "Sony / Columbia",
    "sony pictures entertainment": "Sony / Columbia",
    "sony pictures classics": "Sony Classics",
    "20th century fox": "20th Century Studios",
    "20th century studios": "20th Century Studios",
    "walt disney studios motion pictures": "Disney",
    "walt disney pictures": "Disney",
    "amazon mgm studios": "Amazon MGM Studios",
    "amazon studios": "Amazon MGM Studios",
    "warner bros.": "Warner Bros.",
    "warner bros. pictures": "Warner Bros.",
    "pbs": "PBS network",
}

# LF network label -> parent company ("companies" column). Absent -> "Unknown".
NETWORK_TO_COMPANIES = {
    "20th Century Studios": "Walt Disney Pictures",
    "Amazon MGM Studios": "Amazon Studios",
    "Disney": "Walt Disney Pictures",
    "Lionsgate / Summit": "Lionsgate",
    "Neon": "Neon",
    "Sony / Columbia": "Sony Pictures",
    "Sony Classics": "Sony Pictures",
    "Warner Bros.": "Warner Bros. Pictures",
}

# LF network label -> distributor YouTube channel (youtube_channel_company)
NETWORK_TO_YOUTUBE = {
    "20th Century Studios": "http://www.youtube.com/user/FoxMovies",
    "Amazon MGM Studios": "http://www.youtube.com/channel/UCf5CjDJvsFvtVIhkfmKAwAA",
    "Atlas Distribution": "http://www.youtube.com/channel/UCMLA_XtSbnfjXHL2An8zfGg",
    "Aura Entertainment": "http://www.youtube.com/@AuraEntFilms",
    "Big World Pictures": "http://www.youtube.com/channel/UCx1mHWMsCO96ungWSwS5Udg",
    "Blue Fox": "http://www.youtube.com/channel/UCmHYPCM_h8Tw9JkI3UnrCvA",
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
    "Warner Bros.": "http://www.youtube.com/user/WarnerBrosPictures",
    "Watermelon Pictures": "http://www.youtube.com/@watermelonpicturesco",
    "Well Go USA": "http://www.youtube.com/user/wellgousa",
}

# LF network label -> url_managers team (3rd pipe field)
NETWORK_TO_MANAGER = {
    "20th Century Studios": "Disney Insights & Analytics + Disney Theatrical Research + Disney Ad Sales",
    "Disney": "Disney Insights & Analytics + Disney Theatrical Research + Disney Ad Sales",
    "Amazon MGM Studios": "Amazon PV Enterprise",
    "Lionsgate / Summit": "Lionsgate",
    "Neon": "Neon",
    "Sony / Columbia": "Sony Enterprise",
    "Sony Classics": "Sony Enterprise",
    "Warner Bros.": "Warner Bros.",
}

# IMDb / TMDB genre token -> LF genre token
GENRE_FIX = {
    "Sci-Fi": "Sci Fi",
    "Science Fiction": "Sci Fi",
    "Film-Noir": "Film Noir",
    "Rom-Com": "Romance",
}


def _ci_get(mapping, key):
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


def normalize_network(raw):
    """Map a raw distributor name to the LF network label (else pass through)."""
    if not raw:
        return raw
    return _ci_get(NETWORK_LABEL, raw) or str(raw).strip()


def companies_for(network):
    """Parent company for a network label, or '' if not a known parent."""
    return _ci_get(NETWORK_TO_COMPANIES, network) or ""


def youtube_for(network):
    return _ci_get(NETWORK_TO_YOUTUBE, network) or ""


def manager_for(network):
    return _ci_get(NETWORK_TO_MANAGER, network) or ""


def normalize_genres(genre_multiline):
    """Apply GENRE_FIX token substitutions to a newline-joined genre string."""
    if not genre_multiline:
        return genre_multiline, ""
    parts = [p.strip() for p in str(genre_multiline).split("\n") if p.strip()]
    fixed = []
    for p in parts:
        fixed.append(GENRE_FIX.get(p, p))
    # de-dupe preserving order (Sci-Fi + Science Fiction can both map to Sci Fi)
    seen = set()
    uniq = [g for g in fixed if not (g in seen or seen.add(g))]
    return "\n".join(uniq), (uniq[0] if uniq else "")


# LF network label -> title_sub_category (only networks that are CONSISTENT in the
# manual file; networks whose scale varies per-title are omitted and fall back to
# the "Release - Limited / Studio - Independent" default). Learned from the manual
# report -- validate/extend from your master reference.
NETWORK_TO_SUBCATEGORY = {
    "Disney": "Release - Wide\nStudio - Major",
    "Warner Bros.": "Release - Limited\nStudio - Major",
    "Sony / Columbia": "Release - Wide\nStudio - Independent",
    "Amazon MGM Studios": "Release - Wide\nStudio - Independent",
    "AMC Network": "Release - Wide\nStudio - Independent",
}


def subcategory_for(network):
    return _ci_get(NETWORK_TO_SUBCATEGORY, network) or ""
