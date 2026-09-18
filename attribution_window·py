"""
attribution_window.py
---------------------
The attribution-window rule, in ONE place.

Ops rule (Sep 2026): when a movie drops a second version of its trailer on the
same Facebook / Instagram / TikTok / X accounts the first one ran on, the DAR
row's social cells carry an "attribution window" -- the date from which that
account's activity is attributed to the title. The window opens ONE CALENDAR
MONTH before the OFFICIAL trailer's release and is written as a '|YYYY-MM-DD'
suffix on the handle / URL:

    The Hunger Games: Sunrise on the Reaping   released 2026-11-20
    official trailer                           2026-04-13

    facebook_page     http://www.facebook.com/TheHungerGamesMovie|2026-03-13
    twitter_handle    TheHungerGames|2026-03-13
    instagram_user    thehungergames|2026-03-13
    tiktok_user       hungergamesofficial|2026-03-13

Scope: Movies only, DAR rows only, and ONLY the four columns above.

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


def apply_to_row(row, window, columns=COLUMNS):
    """Stamp the window onto a row's social columns, in place. Empty cells are
    left empty -- the rule adds a date, never a handle."""
    if not window:
        return row
    for col in columns:
        if str(row.get(col) or '').strip():
            row[col] = stamp(row.get(col), window)
    return row


def window_for(metadata, is_movie, is_dar):
    """The window a row should carry, '' when the rule does not apply (not a
    Movies DAR row, no trailer date known, or the rule is switched off)."""
    if not (ENABLED and is_movie and is_dar):
        return ''
    return window_date(trailer_date_from(metadata))
