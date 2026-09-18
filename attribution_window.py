"""
attribution_window.py
---------------------
The attribution-window rule, in ONE place.

Ops rule (Sep 2026): a franchise reuses its social accounts. The @Avengers
handles carried four films; when a new instalment's trailer drops on accounts
an EARLIER title already used, there is no way to tell which title's activity
is which. The attribution window is the date from which that account's
activity belongs to the new title. It opens ONE CALENDAR MONTH before the new
instalment's OFFICIAL trailer and is written as a '|YYYY-MM-DD' suffix on the
handle / URL:

    The Hunger Games: Sunrise on the Reaping   released 2026-11-20
    official trailer                           2026-04-13

    facebook_page     http://www.facebook.com/TheHungerGamesMovie|2026-03-13
    twitter_handle    TheHungerGames|2026-03-13
    instagram_user    thehungergames|2026-03-13
    tiktok_user       hungergamesofficial|2026-03-13

Scope: Movies only, DAR rows only, ONLY the four columns above, and ONLY when
the row QUALIFIES -- its accounts were already used by a previously released
title. A standalone film, or the first film of a franchise, has nothing to
separate and carries no window. Qualification is established by, in order:

  1. an explicit attribution_window / is_sequel column on the sheet;
  2. discovery: a TMDB collection sibling released earlier that shares one of
     this title's handles (metadata key 'attribution_shared_handle');
  3. the batch itself: another row in the same file, released earlier, that
     carries the same handle.

When none of the three can answer, qualification is UNKNOWN and no window is
written -- the rule never guesses a date onto a film that may not need one.

This module is imported by all three sections -- Generator and Review (app.py)
and Validator (validator.py) -- so the rule cannot drift between them. It
deliberately depends on nothing but the standard library: validator.py is
dependency-light by design and app.py imports validator.py, so the shared code
has to sit below both.
"""

import calendar
import os
import re
from datetime import date

# Set ATTRIBUTION_WINDOW=0 to switch the rule off everywhere without a code change.
ENABLED = os.getenv('ATTRIBUTION_WINDOW', '1').strip().lower() \
    not in ('0', 'false', 'no', 'off')

# how far before the official trailer the window opens, in calendar months
MONTHS = 1

# the only columns that ever carry the '|date' suffix
COLUMNS = ('facebook_page', 'twitter_handle', 'instagram_user', 'tiktok_user')

# accepted spellings of the trailer-date field (metadata key or sheet column)
TRAILER_DATE_KEYS = ('trailer_released_on', 'trailer_release_date',
                     'trailer_date', 'official_trailer_date')

SUFFIX_RE = re.compile(r'\|\s*(\d{4}-\d{2}-\d{2})\s*$')
# a '|<something>' that is NOT a well-formed ISO date (e.g. '|2026-3-13')
MALFORMED_SUFFIX_RE = re.compile(r'\|\s*[\d][\d\-/.]*\s*$')
_ISO_RE = re.compile(r'\s*(\d{4})-(\d{1,2})-(\d{1,2})')

# ---------------------------------------------------------------- DAR titles
# A title carrying a '- DAR' suffix is a DAR row. Real files carry '- DAR',
# ' -DAR', '  -  DAR', ' - Dar' and en/em dashes, so the match is lenient about
# spacing, case and dash character. All three sections use THIS regex, so a
# title classified as DAR by the Generator is classified as DAR by the
# Validator too.
DAR_SUFFIX_RE = re.compile(r'[\s ]*[-‐-―][\s ]*DAR\b[\s ]*',
                           re.IGNORECASE)


def is_dar_title(title):
    """True when a title carries a '- DAR' suffix, in any spacing/case/dash."""
    return bool(DAR_SUFFIX_RE.search(str(title or '')))


def strip_dar_suffix(title):
    """Remove a trailing '- DAR' suffix, in any spacing/case/dash form."""
    return DAR_SUFFIX_RE.sub('', str(title or '')).strip()


# --------------------------------------------------------------------- dates
def iso_date(v):
    """'YYYY-MM-DD' from a date cell/string, '' when it is not a real date.
    Accepts the datetime objects pandas/openpyxl hand back ('2026-04-13
    00:00:00') as well as plain ISO text."""
    s = str(v if v is not None else '').strip()
    if not s or s.lower() in ('nan', 'none', 'nat'):
        return ''
    m = _ISO_RE.match(s)
    if not m:
        return ''
    try:
        return date(*(int(x) for x in m.groups())).strftime('%Y-%m-%d')
    except ValueError:
        return ''


def trailer_date_from(meta):
    """The official trailer date carried by a metadata dict or a sheet row,
    under any of TRAILER_DATE_KEYS (matched case-insensitively, with ' ' and
    '-' treated as '_'). '' when none is present."""
    try:
        items = list(meta.items())
    except AttributeError:
        return ''
    found = {}
    for k, v in items:
        lk = re.sub(r'[\s-]+', '_', str(k).strip().lower())
        if lk in TRAILER_DATE_KEYS and lk not in found:
            found[lk] = v
    for k in TRAILER_DATE_KEYS:          # first key in preference order wins
        d = iso_date(found.get(k))
        if d:
            return d
    return ''


def window_date(trailer_date):
    """Attribution-window date for a trailer release: ONE CALENDAR MONTH
    earlier, same day of month -- 2026-04-13 -> 2026-03-13. A day the earlier
    month does not have clamps to its last day (2026-03-31 -> 2026-02-28)."""
    iso = iso_date(trailer_date)
    if not iso:
        return ''
    y, mo, d = int(iso[:4]), int(iso[5:7]), int(iso[8:10])
    mo -= MONTHS
    while mo < 1:
        mo += 12
        y -= 1
    return '%04d-%02d-%02d' % (y, mo, min(d, calendar.monthrange(y, mo)[1]))


# ------------------------------------------------------------- social cells
def lines(v):
    """Non-empty trimmed lines of a social cell."""
    return [ln.strip() for ln in
            str(v if v is not None else '').replace('\r\n', '\n').split('\n')
            if ln.strip() and ln.strip().lower() not in ('nan', 'none')]


def strip_window(value):
    """The value with any trailing '|YYYY-MM-DD' attribution date removed."""
    return '\n'.join(SUFFIX_RE.sub('', ln).strip() for ln in lines(value))


def stamp(value, window):
    """Every line of a social cell re-stamped with `window` (an existing date is
    replaced, not doubled). Curated handles are preserved as-is."""
    if not window:
        return str(value if value is not None else '')
    return '\n'.join('%s|%s' % (ln, window) for ln in lines(strip_window(value)))


def date_of(value):
    """The attribution date a social cell already carries. '' when a line has
    none or the lines disagree -- either way the cell needs re-stamping."""
    dates = set()
    for ln in lines(value):
        m = SUFFIX_RE.search(ln)
        if not m:
            return ''
        dates.add(m.group(1))
    return dates.pop() if len(dates) == 1 else ''


def unstamped_lines(value):
    """Lines that carry no well-formed '|YYYY-MM-DD' suffix at all."""
    return [ln for ln in lines(value) if not SUFFIX_RE.search(ln)]


# ------------------------------------------------------------- qualification
# Columns an analyst can use to force the answer either way. A date value is
# treated as "yes, and here is the window"; a plain yes/no just answers the
# question.
QUALIFY_KEYS = ('attribution_window', 'is_sequel', 'shares_handles')
# set by discovery (metadata_fetcher) when a TMDB collection sibling released
# earlier shares one of this title's handles
DISCOVERY_KEY = 'attribution_shared_handle'

_YES = ('y', 'yes', 't', 'true', '1', 'shared', 'sequel')
_NO = ('n', 'no', 'f', 'false', '0', 'standalone', 'original')


def _lower_map(meta):
    try:
        items = list(meta.items())
    except AttributeError:
        return {}
    return {re.sub(r'[\s-]+', '_', str(k).strip().lower()): v for k, v in items}


def qualifies(meta):
    """Does this row need an attribution window?

    True  -- its accounts were already used by an earlier title
    False -- established that they were not
    None  -- unknown; the caller must not write a window
    """
    lm = _lower_map(meta)
    for k in QUALIFY_KEYS:
        if k not in lm:
            continue
        v = lm[k]
        if v is None or str(v).strip() == '' or str(v).strip().lower() in ('nan', 'none'):
            continue
        if iso_date(v):                      # a date answers 'yes' outright
            return True
        sv = str(v).strip().lower()
        if sv in _YES:
            return True
        if sv in _NO:
            return False
    if DISCOVERY_KEY in lm and lm[DISCOVERY_KEY] is not None:
        return bool(lm[DISCOVERY_KEY])
    return None


def handle_key(value):
    """Identity of a social account, for comparing rows: no URL, no '@', no
    attribution window, lower-cased. '' when the cell holds nothing usable."""
    s = str(value if value is not None else '').strip()
    if not s or s.lower() in ('nan', 'none'):
        return ''
    s = SUFFIX_RE.sub('', s).strip()
    s = re.sub(r'^https?://', '', s, flags=re.I)
    s = re.sub(r'^www\.', '', s, flags=re.I)
    s = re.sub(r'^(facebook|twitter|x|instagram|tiktok)\.com/', '', s, flags=re.I)
    s = s.split('?')[0].strip('/@').strip()
    return s.lower()


def row_handle_keys(row, columns=COLUMNS):
    """Every account identity a row carries, as {column: {key, ...}}."""
    out = {}
    for col in columns:
        keys = {handle_key(ln) for ln in lines(_lower_map(row).get(col, ''))}
        keys.discard('')
        if keys:
            out[col] = keys
    return out


def batch_qualification(rows, released_key='released_on'):
    """Which rows in a batch share an account with an EARLIER-released row.

    Returns {index: True} for rows whose handles a previously released title
    already used. Rows with no shared handle, and rows we cannot order by
    release date, are simply absent -- absent means "unknown", never "no".
    """
    dated = []
    for i, r in enumerate(rows):
        lm = _lower_map(r)
        rel = iso_date(lm.get(released_key)) or iso_date(lm.get('street_date'))
        keys = set()
        for ks in row_handle_keys(r).values():
            keys |= ks
        dated.append((i, rel, keys, strip_dar_suffix(lm.get('title', '')).lower()))

    out = {}
    for i, rel, keys, title in dated:
        if not keys or not rel:
            continue
        for j, rel2, keys2, title2 in dated:
            if j == i or not rel2 or not keys2:
                continue
            if title2 == title:          # the same film's own base/DAR twin
                continue
            if rel2 < rel and (keys & keys2):
                out[i] = True
                break
    return out


def apply_to_row(row, window, columns=COLUMNS):
    """Stamp the window onto a row's social columns, in place. Empty cells are
    left empty -- the rule adds a date, never a handle."""
    if not window:
        return row
    for col in columns:
        if str(row.get(col) or '').strip():
            row[col] = stamp(row.get(col), window)
    return row


def window_for(metadata, is_movie, is_dar, qualified=None):
    """The window a row should carry, '' when the rule does not apply.

    It does not apply unless the row is a Movies DAR row, the rule is on, the
    row QUALIFIES (its accounts were already used by an earlier title), and a
    trailer date is known. `qualified` lets a caller supply an answer it worked
    out from the batch; otherwise the metadata is asked.
    """
    if not (ENABLED and is_movie and is_dar):
        return ''
    q = qualifies(metadata) if qualified is None else qualified
    if q is not True:
        return ''
    return window_date(trailer_date_from(metadata))
