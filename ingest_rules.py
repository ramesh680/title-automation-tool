"""
ingest_rules.py
---------------
The deterministic ingest rules that BOTH the Review and the Validator apply.

These are the rules that need no lookups: the shapes the ingest templates
demand of a row -- which columns a publication never carries, what a
'TRUE|<url>' verified cell looks like, that a LinkedIn cell holds a slug and
not a URL, that a DAR row's search terms end '|DAR|DAR', and so on. They were
written for the Review (app.py) and the Validator had no equivalent, so a
Publisher file that the Review covered in findings passed the Validator clean.

They live here so there is ONE implementation. app.py imports these names and
uses them exactly as before; validator.py runs the same functions and maps
their findings onto its own fail/warn severities (Mismatch -> fail,
Gap -> warn).

Anything that needs the network -- comparing a row against a discovered IMDb
id, network label or handle -- deliberately stays in app.py: the Validator is
offline by design, and only the rules above can be honestly enforced there.

Stdlib only, on purpose: validator.py is dependency-light and app.py imports
validator.py, so this has to sit below both.
"""

import re

from attribution_window import is_dar_title as _is_dar_title
from attribution_window import strip_dar_suffix as _strip_dar_suffix

# the General master Title Category list, used to tell a publication apart from
# another brand category. titleforge_ingest_ext is stdlib-only and standalone,
# so this module stays self-contained; fail soft if it is absent.
try:
    from titleforge_ingest_ext import GENERAL_TITLE_CATEGORIES as _TFX_MASTER
except Exception:
    _TFX_MASTER = []



def _review_norm(v):
    """Comparison form: trimmed lines, scheme-insensitive URLs, no blanks."""
    s = str(v if v is not None else '').strip()
    if s.lower() in ('nan', 'none'):
        return ''
    s = s.replace('\r\n', '\n')
    s = '\n'.join(ln.strip() for ln in s.split('\n') if ln.strip())
    return re.sub(r'^https://', 'http://', s, flags=re.M)


_FANPAGE_RE = re.compile(r'fan[\s_-]?(page|club)|fanpage', re.I)
# never valid in a manual value (Rule 5): /p/, /php/ and /people/ path URLs and
# profile.php URLs are not real page URLs, so such a Facebook value is not
# usable data.
_BAD_FB_MANUAL_RE = re.compile(
    r'facebook\.com/(?:p|php|people)/|facebook\.com/profile\.php', re.I)



def _url_equiv_key(url):
    """Scheme- and trailing-slash-insensitive key for URL comparison (Rule 2).
    http vs https, a leading 'www.' and any trailing '/' are not meaningful
    differences, so 'http://www.imdb.com/title/tt1/' and
    'https://imdb.com/title/tt1' produce the same key."""
    s = str(url or '').strip().lower()
    s = re.sub(r'^https?://', '', s)
    s = re.sub(r'^www\.', '', s)
    return s.rstrip('/')



def _cat_is_publisher(cat):
    """True when a title_category is the PUBLISHERS schema ('Publishers', and
    the variants Ops type: 'Publisher', 'Publishing'). 'Video Game Publishers'
    is a Video Game category, NOT a publishing brand, so it is excluded and
    keeps its existing game handling."""
    s = str(cat or '').strip().lower()
    if 'game' in s:
        return False
    return 'publish' in s



PUBLICATION_TYPES = (
    'Advertising & Marketing', 'Art & Culture', 'Business & Finance',
    'Celebrity', 'Craft & DIY', 'Entertainment', 'Family & Parenting',
    'Fashion & Beauty', 'Film & TV', 'Food & Beverage', 'General Interest',
    'Health & Wellness', 'Home & Renovation', 'Interior Design & Architecture',
    'Lifestyle', 'Memes', 'Military', 'Music', 'News & Politics', 'Newspaper',
    'Photography', 'Science', 'Sports', 'Tech', 'Travel', 'Video Games',
    'Wedding',
)
_PUB_TYPE_PREFIX = 'Publication Type - '
_PUB_TYPES_LOWER = {t.lower(): t for t in PUBLICATION_TYPES}

PUBLISHER_REQUIRED_BRAND_SETS = ('LF // Publishing', 'Pristine DAR Brands')

# columns a publishing brand never carries
PUBLISHER_BLANK_COLS = (
    'genre', 'primary_genre', 'composite_brand_set', 'released_on',
    'domestic_opening_weekend_box_office', 'domestic_opening_weekend_screens',
    'domestic_opening_weekend_rank', 'street_date', 'rottentomatoes',
    'imdb_id', 'metacritic', 'instagram_business_hashtags',
)
PUB_LEAVE_BLANK = '«leave blank for Publishers»'
PUB_CONFIRM_TYPE = '«CONFIRM Publication Type - X»'



# a LinkedIn cell holds a slug ('fansided.com|DAR' is valid) -- only a real URL
# is wrong there
_PUB_LI_URL_RE = re.compile(r'^https?://|linkedin\.com', re.I)
# verified cells are multi-line: one 'TRUE|url' / 'FALSE|url' per account
_PUB_VERIFIED_RE = re.compile(r'^(TRUE|FALSE)\|(https?://\S+)$', re.I)
# a YouTube cell may carry a '|label' suffix ('.../user/CNN|cnn breaking news')
_PUB_YT_RE = re.compile(r'^https?://(www\.)?youtube\.com/(@[\w.\-]+|user/[\w.\-]+'
                        r'|channel/[\w.\-]+|c/[\w.\-]+)/?(\|.*)?$', re.I)
_PUB_WIKI_RE = re.compile(r'^https?://en\.wikipedia\.org/wiki/\S+$', re.I)
_PUB_FB_URL_RE = re.compile(r'^https?://(www\.)?facebook\.com/\S+$', re.I)



def _pub_lines(v):
    """Non-empty trimmed lines of a multi-line cell."""
    return [ln.strip() for ln in
            str(v if v is not None else '').replace('\r\n', '\n').split('\n')
            if ln.strip() and ln.strip().lower() not in ('nan', 'none')]



def _pub_val(r, lower_cols, col):
    """The row's value for a logical column name ('' when the column is absent)."""
    src = lower_cols.get(col)
    return '' if src is None else str(r.get(src) if r.get(src) is not None else '')



def _pub_handle(v):
    """Bare handle from a handle cell or a social URL."""
    s = str(v or '').strip()
    if '/' in s:
        s = [p for p in s.rstrip('/').split('/') if p][-1]
    return s.split('?', 1)[0].strip().lstrip('@').strip()



def _pub_dar_label(v, n=1):
    """Append the '|DAR' label(s) a Publisher cell needs, keeping any value."""
    s = str(v or '').strip()
    return s + '|DAR' * n if s else s



def _pub_search_term_pair(title, handle):
    """The two search-term lines every publisher DAR row carries."""
    name = re.sub(r'[^a-z0-9]', '', _strip_dar_suffix(title).lower())
    out = []
    h = _pub_handle(handle)
    if h:
        out.append(f'@{h.lower()}|DAR|DAR')
    if name:
        out.append(f'#{name}|DAR|DAR')
    return out



def publisher_review_findings(r, lower_cols, title, cat, meta=None):
    """Rule-based review of ONE Publisher row against the Publishers ingest
    rules above. Returns [{'column','status','current','suggested'}, ...] with
    'column' the file's own column name. Independent of auto-discovery: these
    are template rules, so they hold with discovery switched off."""
    meta = meta or {}
    out = []

    def add(col, status, current, suggested):
        src = lower_cols.get(col)
        if src is None:                     # column not in the uploaded file
            return
        cur_s = '' if current is None else str(current)
        sug_s = '' if suggested is None else str(suggested)
        # never emit a no-op finding (suggestion identical to what is there)
        if sug_s and _review_norm(cur_s) == _review_norm(sug_s):
            return
        out.append(dict(column=src, status=status, current=cur_s,
                        suggested=sug_s))

    def val(col):
        return _pub_val(r, lower_cols, col)

    def flag(col, expected, blank_is_gap=True):
        """Flag a cell against a single expected value."""
        cur = val(col).strip()
        if _review_norm(cur) == _review_norm(expected):
            return
        add(col, 'Gap' if (not cur and blank_is_gap) else 'Mismatch',
            cur, expected)

    # ---- title: publisher rows are DAR rows, one per publication ----
    t_raw = str(title or '').strip()
    if t_raw and not _is_dar_title(t_raw):
        add('title', 'Mismatch', t_raw, f'{_strip_dar_suffix(t_raw)} - DAR')

    # ---- title_category ----
    cat_s = str(cat or '').strip()
    if not cat_s:
        add('title_category', 'Gap', '', 'Publishers')
    elif not (_cat_is_publisher(cat_s) or cat_s in (_TFX_MASTER or ())):
        # not 'Publishers' and not one of the approved master categories
        add('title_category', 'Mismatch', cat_s, 'Publishers')

    # ---- title_sub_category: 'Publication Type - X' from the dropdown ----
    sub_lines = _pub_lines(val('title_sub_category'))
    if not sub_lines:
        sugg = str(meta.get('title_sub_category') or '').strip() or PUB_CONFIRM_TYPE
        add('title_sub_category', 'Gap', '', sugg)
    else:
        bad = []
        for ln in sub_lines:
            if not ln.lower().startswith(_PUB_TYPE_PREFIX.lower()):
                bad.append(f'{ln}  ->  {_PUB_TYPE_PREFIX}{ln}'
                           if ln.lower() in _PUB_TYPES_LOWER
                           else f'{ln}  ->  {PUB_CONFIRM_TYPE}')
                continue
            typ = ln[len(_PUB_TYPE_PREFIX):].strip()
            if typ.lower() not in _PUB_TYPES_LOWER:
                near = [t for t in PUBLICATION_TYPES
                        if typ.lower() in t.lower() or t.lower() in typ.lower()]
                bad.append(f'{ln}  ->  ' + (
                    _PUB_TYPE_PREFIX + near[0] if near else
                    'not in the Publication Type dropdown'))
            elif typ != _PUB_TYPES_LOWER[typ.lower()]:
                bad.append(f'{ln}  ->  {_PUB_TYPE_PREFIX}'
                           f'{_PUB_TYPES_LOWER[typ.lower()]}')
        if bad:
            add('title_sub_category', 'Mismatch', val('title_sub_category'),
                '\n'.join(bad))

    # ---- companies / brand_set ----
    flag('companies', 'Pristine Brand')
    bs_cur = val('brand_set')
    bs_lines = {ln.lower() for ln in _pub_lines(bs_cur)}
    missing = [b for b in PUBLISHER_REQUIRED_BRAND_SETS
               if b.lower() not in bs_lines]
    if missing:
        add('brand_set', 'Gap' if not bs_cur.strip() else 'Mismatch', bs_cur,
            _merge_brand_set(bs_cur, '\n'.join(missing)))

    # ---- columns a publication never carries ----
    for col in PUBLISHER_BLANK_COLS:
        cur = val(col).strip()
        if cur and cur.lower() not in ('nan', 'none'):
            add(col, 'Mismatch', cur, PUB_LEAVE_BLANK)

    # ---- handles: bare handles, one per line -- no '@', no URL ----
    # (twitter_handle is mixed case in the platform; instagram is lowercased)
    for col in ('twitter_handle', 'instagram_user', 'tiktok_user'):
        cur = val(col).strip()
        if not cur:
            continue
        want, changed = [], False
        for ln in _pub_lines(cur):
            h = _pub_handle(ln)
            if col == 'instagram_user':
                h = h.lower()
            if h and h != ln:
                changed = True
            if h and h.lower() not in [w.lower() for w in want]:
                want.append(h)
        if changed and want:
            add(col, 'Mismatch', cur, '\n'.join(want))

    # ---- facebook_page: a facebook.com URL per line ----
    fb_page = val('facebook_page').strip()
    fb_page_lines = _pub_lines(fb_page)
    if fb_page_lines and not all(_PUB_FB_URL_RE.match(ln) for ln in fb_page_lines):
        add('facebook_page', 'Mismatch', fb_page,
            '\n'.join('http://www.facebook.com/' + _pub_handle(ln)
                      for ln in fb_page_lines))
    elif fb_page_lines and _BAD_FB_MANUAL_RE.search(fb_page):
        add('facebook_page', 'Mismatch', fb_page,
            str(meta.get('facebook_page') or ''))

    # ---- *_verified: one 'TRUE|<url>' / 'FALSE|<url>' per account, and the
    # URL must be one of the accounts in facebook_page / twitter_handle ----
    def _verified(ver_col, acct_col, acct_urls, key_of, fill_from_url=None):
        """acct_urls: the account URLs the row itself carries (facebook_page
        lines / twitter.com URLs built from twitter_handle). key_of() reduces a
        URL to the identity that must match."""
        acct_keys = {key_of(u) for u in acct_urls if u}
        ver = val(ver_col).strip()
        if not ver:
            return
        matched, bad_shape, orphan = [], [], []
        for ln in _pub_lines(ver):
            m = _PUB_VERIFIED_RE.match(ln)
            if not m:
                bad_shape.append(ln)
                continue
            matched.append(m)
            if acct_keys and key_of(m.group(2)) not in acct_keys:
                orphan.append(m)
        if bad_shape:
            # a verified cell is 'TRUE|<url>' / 'FALSE|<url>' -- rebuild the
            # malformed lines, keeping the URL when the line already has one
            fixed = []
            for ln in _pub_lines(ver):
                if _PUB_VERIFIED_RE.match(ln):
                    fixed.append(ln)
                    continue
                url = ln.split('|')[-1].strip()
                if not url.lower().startswith('http'):
                    url = f'http://{url}' if '.' in url else '<account url>'
                fixed.append(f'TRUE|{url}')
            add(ver_col, 'Mismatch', ver, '\n'.join(fixed))
            return
        if orphan and acct_urls:
            # a verified URL that is none of the row's own accounts: suggest the
            # verified cell rebuilt from the accounts the row actually carries
            flag_of = {key_of(u): m.group(1).upper()
                       for u, m in zip(acct_urls, matched)}
            add(ver_col, 'Mismatch', ver, '\n'.join(
                f'{flag_of.get(key_of(u), matched[0].group(1).upper())}|{u}'
                for u in acct_urls))
        elif matched and not acct_urls and fill_from_url:
            # the verified cell holds the account the empty cell should carry
            add(acct_col, 'Gap', '', '\n'.join(
                dict.fromkeys(fill_from_url(m.group(2)) for m in matched)))

    _verified('facebook_verified', 'facebook_page', fb_page_lines,
              _url_equiv_key, fill_from_url=lambda u: u)

    tw_handle = val('twitter_handle').strip()
    _verified('twitter_verified', 'twitter_handle',
              [f'http://twitter.com/{_pub_handle(ln)}'
               for ln in _pub_lines(tw_handle) if _pub_handle(ln)],
              lambda u: _pub_handle(u).lower(),
              fill_from_url=lambda u: _pub_handle(u))

    # ---- YouTube: full channel URL (an optional '|label' suffix is fine) ----
    for col in ('youtube_channel_username', 'youtube_channel_company'):
        cur = val(col)
        bad = [ln for ln in _pub_lines(cur) if not _PUB_YT_RE.match(ln)]
        if bad:
            add(col, 'Mismatch', cur,
                '\n'.join('http://www.youtube.com/@' + _pub_handle(ln.split('|')[0])
                          for ln in bad))

    # ---- Wikipedia: en.wikipedia.org/wiki/... ----
    wiki = val('wikipedia_page')
    for ln in _pub_lines(wiki):
        if not _PUB_WIKI_RE.match(ln):
            add('wikipedia_page', 'Mismatch', wiki,
                str(meta.get('wikipedia_page') or
                    'http://en.wikipedia.org/wiki/' +
                    _strip_dar_suffix(t_raw).replace(' ', '_')))
            break

    # ---- LinkedIn: 'slug|DAR' -- a company slug with a label, not a URL ----
    li = val('linkedin_page').strip()
    if li:
        fixed, changed = [], False
        for ln in _pub_lines(li):
            slug, _, label = ln.partition('|')
            if _PUB_LI_URL_RE.search(slug):
                slug, changed = _pub_handle(slug), True
            if not label:
                label, changed = 'DAR', True
            fixed.append(f'{slug}|{label}')
        if changed:
            add('linkedin_page', 'Mismatch', li, '\n'.join(fixed))

    # ---- Pinterest: 'slug|DAR|DAR' / 'Board|DAR|DAR' lines ----
    for col in ('pinterest_user_username', 'pinterest_board'):
        cur = val(col).strip()
        if not cur:
            continue
        fixed, changed = [], False
        for ln in _pub_lines(cur):
            n = len(ln.split('|'))
            if n < 3:
                fixed.append(_pub_dar_label(ln.split('|')[0], 3 - n))
                changed = True
            else:
                fixed.append(ln)
        if changed:
            add(col, 'Mismatch', cur, '\n'.join(fixed))

    # ---- twitter_search_terms: every line 'term|LABEL|LABEL', and a DAR row
    # carries at least one '@handle|DAR|DAR' and one '#term|DAR|DAR' ----
    st_cur = val('twitter_search_terms')
    st_lines = _pub_lines(st_cur)
    want_pair = _pub_search_term_pair(t_raw, tw_handle)
    if not st_lines:
        add('twitter_search_terms', 'Gap', '', '\n'.join(want_pair))
    else:
        shape_bad = [ln for ln in st_lines if len(ln.split('|')) != 3]
        fixed = []
        for ln in st_lines:
            n = len(ln.split('|'))
            ln = _pub_dar_label(ln, 3 - n) if n < 3 else ln
            if ln.lower() not in [f.lower() for f in fixed]:
                fixed.append(ln)
        low = [ln.lower() for ln in fixed]
        missing = []
        for mark in ('@', '#'):
            if any(l.startswith(mark) and l.endswith('|dar|dar') for l in low):
                continue
            missing += [ln for ln in want_pair
                        if ln.startswith(mark) and ln.lower() not in low]
        if shape_bad or missing:
            add('twitter_search_terms', 'Mismatch', st_cur,
                '\n'.join(fixed + missing))

    # ---- twitter_search_term_keywords: same 'term|LABEL|LABEL' shape ----
    kw_cur = val('twitter_search_term_keywords')
    kw_lines = _pub_lines(kw_cur)
    if kw_lines and any(len(ln.split('|')) != 3 for ln in kw_lines):
        fixed = []
        for ln in kw_lines:
            n = len(ln.split('|'))
            fixed.append(_pub_dar_label(ln, 3 - n) if n < 3 else ln)
        add('twitter_search_term_keywords', 'Mismatch', kw_cur, '\n'.join(fixed))

    return out



def _merge_brand_set(manual_raw, expected_raw):
    """Merge the required brand set(s) into the file's existing brand_set value
    WITHOUT removing anything already there.

    Returns the manual lines (original order preserved) with any required line
    that is not already present appended to the end. Comparison ignores case,
    internal spacing and any '||suffix' (added-date etc.) so an existing brand
    set is never duplicated. Used so the Review never drops brand sets a curator
    put in the file -- it only tops up the ones the ingest template requires."""
    def _key(ln):
        return re.sub(r'\s+', ' ', ln.split('||', 1)[0]).strip().lower()
    manual_lines = [ln.strip() for ln in
                    str(manual_raw if manual_raw is not None else '')
                    .replace('\r\n', '\n').split('\n') if ln.strip()]
    have = {_key(ln) for ln in manual_lines}
    for ln in str(expected_raw if expected_raw is not None else '') \
            .replace('\r\n', '\n').split('\n'):
        ln = ln.strip()
        k = _key(ln)
        if k and k not in have:
            manual_lines.append(ln)
            have.add(k)
    return '\n'.join(manual_lines)


# Publisher content signals, used when the title_category cell is blank.
_PUB_BRAND_SET_RE = re.compile(r'lf\s*//\s*publishing', re.I)
_PUB_SUB_RE = re.compile(r'^\s*publication\s+type\s*-', re.I | re.M)


def _row_is_publisher(r, cat, lower_cols):
    """True when a manual row belongs to the Publishers schema.

    A Publishers title_category decides it outright. Otherwise the row's own
    content decides, because a publishing brand does not always carry the
    'Publishers' category -- in the BrandDefinitionReport export publications
    also sit under Media / TV Network / Music and Entertainment. Those rows are
    still publisher rows: they carry a 'Publication Type - X' sub-category and
    the 'LF // Publishing' brand set. Detecting them here stops them being
    reviewed as Movies (blank category) or as General brands (Media etc.)."""
    def _v(name):
        col = lower_cols.get(name)
        return str(r.get(col) or '') if col else ''

    if _cat_is_publisher(cat):
        return True
    # 'Video Game Publishers' is a Video Game category -- never a publication
    if 'game' in str(cat or '').lower():
        return False
    return bool(_PUB_SUB_RE.search(_v('title_sub_category'))
                or _PUB_BRAND_SET_RE.search(_v('brand_set')))
