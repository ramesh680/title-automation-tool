"""Movies review rules (Oct 2026 rebuild).

Why this exists
---------------
The reviewed file of 2026-10-13 (18 Movies rows) produced 28 findings and none
of them was a real error, while the real problems in the file went unflagged.
Root causes:

  * the release-date column was not the anchor: discovery's own year (an RT /
    festival 2025 date) overrode the file's 2026 release, so keyword "2026" was
    called wrong and a bare-slug RT page (the 2020 'Falling') was suggested;
  * a Movies row was checked against TV pages (metacritic.com/tv/human/);
  * generic single-word social guesses ('human', 'prahaar') were raised as Gaps
    even though no source listed them;
  * liveness noise ("could not be opened", "TikTok removed" for an EMPTY cell)
    was reported as a finding;
  * the same cell was reported two or three times (Verify + Gap).

Rules (Movies rows only)
------------------------
Everything is judged against the row ITSELF first -- title, released_on year,
network, the distributor clause in twitter_search_term_keywords, url_managers --
and discovery is only allowed to add a finding when it agrees with that anchor.

  1. Release-year anchor: the year in released_on is the truth. URL slugs that
     carry a year (RT _2025, Metacritic -2025, Wikipedia (2026_film)) may be off
     by at most 1 year (festival vs release); more is a Mismatch.
  2. Movies only: a /tv/ Metacritic or Rotten Tomatoes URL is a Mismatch.
  3. Social handles must belong to the FILM:
       - twitter / instagram / tiktok are bare handles (no URL, no '@');
         facebook_page is a full facebook.com URL;
       - a handle that is the distributor's / studio's / production company's
         account (matches network, companies, the @handle in the keyword
         clause, or reads like a company: ...studio, ...media, ...films) is a
         Mismatch on a film row;
       - a handle that merely doesn't spell out the title is left alone
         (abbreviations / franchise / campaign handles are legitimate).
  4. twitter_search_term_keywords: the year term must equal the released_on
     year; the distributor term must be the row's network; the title phrase is
     the row's title. Suggestions edit the file's OWN string (case preserved),
     never a lower-cased regenerated clause.
  5. url_managers (when the column exists): every platform|value line must
     reference a social account that is actually on the row.
  6. Discovery findings pass a precision gate: guessed / generic handles,
     liveness "couldn't open" notes, notes about empty cells, /tv/ or wrong-year
     URL suggestions, keyword re-generations and single-source Wide/Limited
     calls are dropped or downgraded; one finding per cell.
"""
import re
import unicodedata

YEAR_TOLERANCE = 1          # festival premiere vs release
SOCIAL_COLS = ('facebook_page', 'twitter_handle', 'instagram_user', 'tiktok_user')
_PLAT = {'facebook_page': 'facebook', 'twitter_handle': 'twitter',
         'instagram_user': 'instagram', 'tiktok_user': 'tiktok'}
# words that make a handle read as a company account rather than a film's
_COMPANY_WORDS = ('studio', 'studios', 'media', 'pictures', 'entertainment',
                  'productions', 'production', 'distribution', 'releasing',
                  'films', 'filmco', 'records', 'network', 'group', 'company',
                  'inc', 'llc', 'ltd')
# affixes a film account commonly wraps its title in
_FILM_AFFIX = ('officialmovie', 'themovie', 'thefilm', 'official', 'movie',
               'film', 'doc', 'documentary', 'the')


# ----------------------------------------------------------------- helpers
def _ascii(s):
    return unicodedata.normalize('NFKD', str(s or '')).encode(
        'ascii', 'ignore').decode('ascii')


def _alnum(s):
    return re.sub(r'[^a-z0-9]', '', _ascii(s).lower())


def _blank(v):
    s = str(v if v is not None else '').strip()
    return (not s) or s.lower() == 'nan'


def base_title(title):
    """Title without the ' - DAR' suffix."""
    t = str(title or '').strip()
    return re.sub(r'\s*-\s*DAR\s*$', '', t, flags=re.I).strip()


def clean_title(title):
    """Base title without a trailing '(YYYY)' disambiguator."""
    return re.sub(r'\s*\((?:19|20)\d{2}\)\s*$', '', base_title(title)).strip()


def is_dar(title):
    return bool(re.search(r'-\s*DAR\s*$', str(title or ''), re.I))


def anchor_year(row):
    """Year in the row's released_on column (the review's source of truth)."""
    m = re.match(r'\s*((?:19|20)\d{2})', str(row.get('released_on') or ''))
    if m:
        return int(m.group(1))
    m = re.search(r'\(((?:19|20)\d{2})\)\s*$', base_title(row.get('title')))
    return int(m.group(1)) if m else None


def strip_window(v):
    """'handle|2026-03-13' -> 'handle' (attribution-window suffix)."""
    return re.sub(r'\|\d{4}-\d{2}-\d{2}\s*$', '', str(v or '').strip())


def bare_handle(col, v):
    s = strip_window(v)
    if col == 'facebook_page':
        s = re.sub(r'^https?://(?:www\.|m\.|web\.)?facebook\.com/', '', s, flags=re.I)
    else:
        s = re.sub(r'^https?://(?:www\.)?(?:twitter|x|instagram|tiktok)\.com/@?', '',
                   s, flags=re.I)
    return s.split('?')[0].strip('/').lstrip('@').strip()


def url_slug_year(url):
    """Year carried in an RT / Metacritic / Wikipedia URL slug, else None."""
    u = str(url or '')
    for pat in (r'rottentomatoes\.com/m/[^/?#]*_((?:19|20)\d{2})\b',
                r'metacritic\.com/movie/[^/?#]*-((?:19|20)\d{2})\b',
                r'wikipedia\.org/wiki/[^/?#]*\(((?:19|20)\d{2})_film\)'):
        m = re.search(pat, u, re.I)
        if m:
            return int(m.group(1))
    return None


def _keyword_handles(kw):
    """@handles and #tags in the distributor clause of the row's keywords --
    LF's own record of which accounts belong to the distributor."""
    s = strip_window(kw)
    return {_alnum(x) for x in re.findall(r'[@#]([A-Za-z0-9_]+)', s)}


def company_keys(row):
    """Alnum keys that identify the distributor / studio on this row."""
    keys = set()
    for col in ('network', 'companies'):
        v = _alnum(row.get(col))
        if v and v not in ('unknown', 'pristinebrand'):
            keys.add(v)
    keys |= _keyword_handles(row.get('twitter_search_term_keywords'))
    # the company YouTube channel: '...youtube.com/@MaddockFilms|Title'
    for ln in str(row.get('youtube_channel_username') or '').split('\n'):
        if '|' in ln:
            m = re.search(r'youtube\.com/(?:@|user/|c/)([^/|?]+)', ln, re.I)
            if m:
                keys.add(_alnum(m.group(1)))
    # the title itself is never a company key
    keys.discard(_alnum(clean_title(row.get('title'))))
    return {k for k in keys if len(k) >= 3}


def handle_relevance(handle, row):
    """'title' | 'company' | 'generic' | 'unrelated' for a social handle."""
    h = _alnum(handle)
    if not h:
        return 'unrelated'
    t = _alnum(clean_title(row.get('title')))
    t_nothe = re.sub(r'^the', '', t)
    year = anchor_year(row)
    core = h
    if year:
        core = core.replace(str(year), '')
    changed = True
    while changed:
        changed = False
        for a in _FILM_AFFIX:
            if core.startswith(a) and len(core) > len(a) + 2:
                core, changed = core[len(a):], True
            if core.endswith(a) and len(core) > len(a) + 2:
                core, changed = core[:-len(a)], True
    for ck in company_keys(row):
        if h == ck or (len(ck) >= 5 and (ck in h or h in ck)):
            return 'company'
    if t and (t in h or (len(t_nothe) >= 4 and t_nothe in h)):
        # a one-word title used bare as the handle ('human' for 'Human') is a
        # dictionary word -- nothing shows it is the film's account
        if core == t and len(re.findall(r'[a-z0-9]+', _ascii(
                clean_title(row.get('title'))).lower())) == 1 and h == t:
            return 'generic'
        return 'title'
    # franchise account: the title's lead segment before ':' ('Avengers' for
    # 'Avengers: Endgame') -- valid on sequels (attribution-window rule)
    lead = _alnum(re.split(r'\s*[:\u2013\u2014]\s*|\s+-\s+', clean_title(row.get('title')))[0])
    if lead and lead != t and core == lead:
        return 'franchise'
    toks = [x for x in re.findall(r'[a-z0-9]+', _ascii(
        clean_title(row.get('title'))).lower()) if x not in ('the', 'a', 'an', 'of')]
    acro = ''.join(x[0] for x in toks)
    if len(acro) >= 3 and core == acro:
        return 'title'
    if any(w in h for w in _COMPANY_WORDS):
        return 'company'
    if toks and core in toks:
        return 'generic'     # one word lifted from a longer title ('prahaar')
    return 'unrelated'


def _f(col, status, current, suggested, reason):
    return dict(column=col, status=status, current=current,
                suggested=suggested, reason=reason)


# ------------------------------------------------------------ row rules
def check_social_cells(row):
    out = []
    for col in SOCIAL_COLS:
        raw = row.get(col)
        if _blank(raw):
            continue
        cur = str(raw)
        lines = [ln for ln in cur.split('\n') if ln.strip()]
        for ln in lines:
            h = bare_handle(col, ln)
            plat = _PLAT[col]
            # format
            if col == 'facebook_page':
                if not re.match(r'^https?://(?:www\.)?facebook\.com/', strip_window(ln), re.I):
                    out.append(_f(col, 'Mismatch', cur,
                                  cur.replace(strip_window(ln),
                                              'http://www.facebook.com/' + h),
                                  'facebook_page must be a full facebook.com URL.'))
                    break
            elif re.search(r'https?://|www\.|/|^@', strip_window(ln)):
                out.append(_f(col, 'Mismatch', cur, cur.replace(strip_window(ln), h),
                              '%s must be the bare handle (no URL, no @).' % col))
                break
            # ownership
            rel = handle_relevance(h, row)
            if rel == 'company':
                out.append(_f(col, 'Mismatch', cur, '',
                              "%s '%s' is the distributor/studio/production "
                              "company's account, not the film's. Remove it or "
                              "replace it with the film's own %s account."
                              % (plat.title(), h, plat.title())))
                break
            # A curated handle that merely doesn't spell out the title is NOT
            # flagged: abbreviations, franchise and campaign handles are all
            # legitimate, and flagging them is what buried real errors in noise.
    return out


def check_url_years(row):
    out = []
    year = anchor_year(row)
    for col in ('rottentomatoes', 'metacritic', 'wikipedia_page'):
        v = row.get(col)
        if _blank(v):
            continue
        s = str(v)
        if col in ('rottentomatoes', 'metacritic') and re.search(r'\.com/tv/', s, re.I):
            out.append(_f(col, 'Mismatch', s, '',
                          'Movies row carries a TV page (/tv/). Use the /m/ or '
                          '/movie/ page for the %s film.' % (year or '')))
            continue
        y = url_slug_year(s)
        if year and y and abs(y - year) > YEAR_TOLERANCE:
            out.append(_f(col, 'Mismatch', s, '',
                          'Page is dated %s but released_on is %s -- a same-named '
                          'title. Find the %s page.' % (y, year, year)))
    # cross-source: Wikipedia film year vs RT/MC year
    wy = url_slug_year(row.get('wikipedia_page'))
    for col in ('rottentomatoes', 'metacritic'):
        y = url_slug_year(row.get(col))
        if wy and y and abs(wy - y) > YEAR_TOLERANCE:
            out.append(_f(col, 'Verify', str(row.get(col)),
                          'Wikipedia is the %s film but %s is dated %s -- verify '
                          'both point to the same movie.' % (wy, col, y),
                          'cross-source year conflict'))
    return out


def _kw_phrase(kw):
    m = re.match(r'\s*\(\s*"([^"]+)"\s*\)', strip_window(kw))
    return m.group(1) if m else ''


def check_keywords(row):
    col = 'twitter_search_term_keywords'
    v = row.get(col)
    if _blank(v):
        return []
    cur = str(v)
    year = anchor_year(row)
    out, new, reasons = [], cur, []
    # year term(s)
    years = set(re.findall(r'"((?:19|20)\d{2})"', cur))
    if year and years and str(year) not in years:
        for y in years:
            new = new.replace('"%s"' % y, '"%s"' % year)
        reasons.append('year term %s should be %s (released_on)'
                       % ('/'.join(sorted(years)), year))
    # distributor term must be the row's network
    net = _alnum(row.get('network'))
    if net:
        clause = re.sub(r'^\s*\([^)]*\)', '', strip_window(cur))
        toks = {_alnum(x) for x in re.findall(r'"([^"]+)"', clause)} \
            | _keyword_handles(clause)
        if net not in toks and not any(net in t or t in net for t in toks if len(t) >= 4):
            out.append(_f(col, 'Verify', cur,
                          "Keyword clause does not name the row's network '%s' "
                          "-- confirm the distributor terms." % row.get('network'),
                          'distributor term missing'))
    # title phrase
    phrase = _kw_phrase(cur)
    want = base_title(row.get('title'))
    if phrase and _alnum(phrase) != _alnum(want):
        new = new.replace('"%s"' % phrase, '"%s"' % want, 1)
        reasons.append('title phrase should be "%s"' % want)
    # LF convention (confirmed Oct 2026): the phrase is the title exactly as in
    # the title column, '(YYYY)' disambiguator included -- "Falling (2026)".
    if new != cur:
        out.insert(0, _f(col, 'Mismatch', cur, new, '; '.join(reasons) + '.'))
    return out


def check_url_managers(row):
    col = 'url_managers'
    if col not in row or _blank(row.get(col)):
        return []
    cur = str(row[col])
    have = {_PLAT[c]: {_alnum(bare_handle(c, ln)) for ln in str(row.get(c) or '').split('\n')
                       if ln.strip()} for c in SOCIAL_COLS}
    bad = []
    for ln in cur.split('\n'):
        parts = ln.split('|')
        if len(parts) < 2 or parts[0].strip().lower() not in have:
            continue
        plat = parts[0].strip().lower()
        h = _alnum(re.sub(r'^https?://(?:www\.)?[^/]+/', '', parts[1]).strip('/@'))
        if h and h not in have[plat]:
            bad.append(ln)
    if bad:
        return [_f(col, 'Mismatch', cur,
                   '\n'.join(ln for ln in cur.split('\n') if ln not in bad),
                   'url_managers lines reference accounts not on the row: '
                   + '; '.join(bad))]
    return []


def movie_rule_findings(row):
    """Deterministic, LF-row-anchored findings for one Movies row
    (`row` keys lower-cased)."""
    return (check_url_years(row) + check_social_cells(row)
            + check_keywords(row) + check_url_managers(row))


# --------------------------------------------------- discovery precision gate
_GUESS_RE = re.compile(r"Social handles: .*? '([^']+)' was not listed by any source", re.I)
_NOISE_RE = re.compile(r'could not be opened to confirm|fails? open|throttl', re.I)
_REMOVED_RE = re.compile(r"Social handles: .*? '([^']+)' was removed", re.I)


def gate_discovery_findings(findings, row):
    """Filter discovery-based findings for one Movies row. Each finding is a
    dict with column/status/current/suggested (row/title kept as given)."""
    year = anchor_year(row)
    guessed = {_alnum(m.group(1)) for f in findings
               for m in [_GUESS_RE.search(str(f.get('suggested') or ''))] if m}
    kept = []
    for f in findings:
        col = str(f.get('column') or '').lower()
        st = f.get('status')
        sugg = str(f.get('suggested') or '')
        cur = '' if _blank(f.get('current')) else str(f.get('current'))
        if st == 'Verify':
            if _GUESS_RE.search(sugg) or _NOISE_RE.search(sugg):
                continue
            m = _REMOVED_RE.search(sugg)
            if m:
                # only meaningful when that dead handle is actually on the row
                if not cur or _alnum(m.group(1)) != _alnum(bare_handle(col, cur)):
                    continue
                f = dict(f, status='Mismatch', suggested='',
                         reason="%s does not exist / is suspended." % m.group(1))
            kept.append(f)
            continue
        # keyword findings come only from the row rules (case-preserving)
        if col == 'twitter_search_term_keywords':
            continue
        if col in SOCIAL_COLS:
            h = bare_handle(col, sugg)
            if cur and _alnum(h) == _alnum(bare_handle(col, cur)):
                kept.append(f)      # same account, only the window date differs
                continue
            if _alnum(h) in guessed or handle_relevance(h, row) not in ('title', 'franchise'):
                continue
            if st == 'Mismatch' and cur and handle_relevance(
                    bare_handle(col, cur), row) in ('title', 'franchise'):
                # both look like the film's: never overwrite a curated account
                f = dict(f, status='Verify',
                         suggested='Source lists %r; file has %r -- check which '
                                   'is the film\'s account.' % (h, bare_handle(col, cur)))
            kept.append(f)
            continue
        if col in ('rottentomatoes', 'metacritic', 'wikipedia_page'):
            if re.search(r'\.com/tv/', sugg, re.I):
                continue
            sy, cy = url_slug_year(sugg), url_slug_year(cur)
            if year and sy and abs(sy - year) > YEAR_TOLERANCE:
                continue
            if cur and cy and year and abs(cy - year) <= YEAR_TOLERANCE and not sy:
                continue   # bare slug replacing a year-correct page
            kept.append(f)
            continue
        if col == 'title_sub_category' and st == 'Mismatch':
            f = dict(f, status='Verify',
                     suggested='A source suggests %s -- confirm Wide/Limited on Rotten '
                               'Tomatoes, Metacritic and the distributor site.'
                               % sugg.replace('\n', ' / '))
        kept.append(f)
    return kept


_RANK = {'Mismatch': 3, 'Gap': 2, 'Verify': 1}


def merge_findings(rule_f, disc_f):
    """One finding per column: rule findings first, highest severity wins."""
    best = {}
    for f in list(rule_f) + list(disc_f):
        col = f['column']
        if col not in best or _RANK.get(f['status'], 0) > _RANK.get(best[col]['status'], 0):
            best[col] = f
    return list(best.values())
