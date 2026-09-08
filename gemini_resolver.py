"""Gemini-grounded social handle resolver.

This is a faithful port of the ``=GEMINI("...")`` sheet formulas the Ops team
used on the NYFW SS27 brand sheet (columns U/W/Y/Z/AB/AG/AI).  Those
"formulas" were not scrapers -- each one was a single natural-language prompt
handed to Gemini with Google Search grounding.  The prompt text below is the
sheet's wording, unchanged, so the app and the sheet ask the model the exact
same question.

Two modes:

``consolidated`` (default)
    One grounded request per entity returns all seven platforms as JSON.
    Every one of the sheet's seven prompts already carried the return-format
    rules for *all* platforms -- only the first sentence differed -- so this
    asks for identical information at 1/7th the requests.

``per_platform``
    One grounded request per entity per platform, byte-for-byte the sheet's
    behaviour.  Kept so the consolidation itself can be A/B'd.

Everything fails soft: no API key, no SDK, or an API error leaves the caller
with empty values and the rest of TitleForge behaves exactly as before.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# configuration (all env-driven; nothing here needs a code change to tune)
# --------------------------------------------------------------------------

# Where the answers come from:
#   'api'        -> the Gemini Developer API (metered: grounded search requests)
#   'appsscript' -> an Apps Script web app that evaluates the native Sheets
#                   =GEMINI() function, so the work is covered by the Workspace
#                   subscription instead of API quota.
#   'sheet'      -> the three-phase Apps Script flow: TitleForge writes the
#                   formulas as text, a person activates them with the workbook
#                   open (=GEMINI() only evaluates in an interactive session),
#                   then TitleForge reads the values back. No API quota.
SOURCE = os.getenv('GEMINI_SOURCE', 'api').strip().lower()
if SOURCE not in ('api', 'appsscript', 'sheet'):
    SOURCE = 'api'

API_KEY = (os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY') or '').strip()
MODEL = os.getenv('GEMINI_MODEL', 'gemini-flash-latest').strip()
MODE = os.getenv('GEMINI_MODE', 'consolidated').strip().lower()

# Apps Script bridge settings (source='appsscript')
SCRIPT_URL = os.getenv('GEMINI_SCRIPT_URL', '').strip()
SCRIPT_TOKEN = os.getenv('GEMINI_SCRIPT_TOKEN', '').strip()
# entities per POST; the script caps at 40 and Sheets slows down past ~20
SCRIPT_BATCH = max(1, min(40, int(os.getenv('GEMINI_SCRIPT_BATCH', '15') or 15)))
SCRIPT_TIMEOUT_S = max(30, int(os.getenv('GEMINI_SCRIPT_TIMEOUT_S', '300') or 300))
WORKERS = max(1, int(os.getenv('GEMINI_WORKERS', '4') or 4))
TIMEOUT_MS = max(5000, int(os.getenv('GEMINI_TIMEOUT_MS', '60000') or 60000))
# hard ceiling on grounded requests per generation run -- stops a 5,000-title
# upload from quietly burning through the monthly search allowance
MAX_REQUESTS = max(0, int(os.getenv('GEMINI_MAX_REQUESTS', '400') or 400))

PLATFORMS = [
    ('facebook_page', 'Facebook'),
    ('twitter_handle', 'Twitter/X'),
    ('instagram_user', 'Instagram'),
    ('youtube_channel_username', 'YouTube'),
    ('tiktok_user', 'TikTok'),
    ('wikipedia_page', 'Wikipedia'),
    ('imdb_id', 'IMDb'),
]
FIELDS = [f for f, _ in PLATFORMS]
_LABEL = dict(PLATFORMS)

try:  # the SDK is optional -- the feature just stays off without it
    from google import genai as _genai
    from google.genai import types as _gtypes
    SDK_OK = True
except Exception as _e:  # pragma: no cover - import guard
    _genai = None
    _gtypes = None
    SDK_OK = False
    log.info("google-genai SDK unavailable (%s); Gemini comparison disabled", _e)


def available():
    """True when a Gemini comparison run can actually be attempted."""
    if SOURCE in ('appsscript', 'sheet'):
        return bool(SCRIPT_URL and SCRIPT_TOKEN)
    return bool(SDK_OK and API_KEY)


def interactive():
    """True when the source needs a person to activate formulas mid-flow, so
    the UI must offer the push / activate / pull steps instead of one button."""
    return SOURCE == 'sheet'


def status():
    st = {
        'available': available(),
        'source': SOURCE,
        'max_requests_per_run': MAX_REQUESTS,
    }
    if SOURCE in ('appsscript', 'sheet'):
        st.update({
            'interactive': interactive(),
            'script_url_set': bool(SCRIPT_URL),
            'script_token_set': bool(SCRIPT_TOKEN),
            'batch_size': SCRIPT_BATCH,
            # the Workspace subscription covers this; there is no per-call charge
            'metered': False,
        })
    else:
        st.update({
            'sdk_installed': SDK_OK,
            'api_key_set': bool(API_KEY),
            'model': MODEL,
            'mode': MODE if MODE in ('consolidated', 'per_platform') else 'consolidated',
            'metered': True,
        })
    return st


# --------------------------------------------------------------------------
# prompt -- the sheet's wording, verbatim
# --------------------------------------------------------------------------

# The shared middle block of every =GEMINI() formula in the source sheet.
_RULES = (
    "Return only the appropriate identifier or full URL, depending on the "
    "platform. For Facebook, return the full official Facebook profile/page "
    "URL. For Twitter/X, return the official username or profile URL. For "
    "Instagram, return the official username only. For YouTube, return the "
    "official channel username or channel URL. For TikTok, return the "
    "official username only. For Wikipedia, return the full official "
    "Wikipedia page URL. For IMDb, return the IMDb ID or full IMDb title/name "
    "URL. Use only the genuine official or verified account. Never return a "
    "fan, parody, tribute, news, aggregator, unofficial or unrelated account. "
    "If no official account/page can be confidently confirmed, return nothing."
)

_PER_PLATFORM_PROMPT = (
    "Using Google Search, find the official verified {platform} profile or "
    "official page for the entity below. Name: {name} (Note: please ignore "
    "the ' - DAR' suffix if present). Category/Context: {context}. "
    + _RULES +
    " Return only the requested value on one line. Do not provide explanations."
)

_CONSOLIDATED_PROMPT = (
    "Using Google Search, find the official verified profiles or official "
    "pages for the entity below on every one of these platforms: Facebook, "
    "Twitter/X, Instagram, YouTube, TikTok, Wikipedia, IMDb. Name: {name} "
    "(Note: please ignore the ' - DAR' suffix if present). "
    "Category/Context: {context}. "
    + _RULES +
    " Judge each platform independently: a confirmed account on one platform "
    "is never evidence for a handle on another. "
    'Return only a single JSON object with exactly these keys: "facebook", '
    '"twitter", "instagram", "youtube", "tiktok", "wikipedia", "imdb". '
    "Use an empty string for any platform you cannot confidently confirm. "
    "Do not provide explanations, notes or any text outside the JSON object."
)

_JSON_KEYS = {
    'facebook': 'facebook_page',
    'twitter': 'twitter_handle',
    'instagram': 'instagram_user',
    'youtube': 'youtube_channel_username',
    'tiktok': 'tiktok_user',
    'wikipedia': 'wikipedia_page',
    'imdb': 'imdb_id',
}


# --------------------------------------------------------------------------
# output validation
# --------------------------------------------------------------------------

# The sheet run shows the model ignoring "return nothing" and answering in
# prose instead ("I do not have enough information to answer the query..."),
# which lands non-data in a data column.  Anything that smells like a refusal
# or a sentence is coerced to blank.
_REFUSAL_RE = re.compile(
    r"\b(i (?:do|don't|do not|am|cannot|can't|could not|couldn't)\b"
    r"|i'm sorry|sorry\b|unable to|not enough information|no official"
    r"|could not (?:find|confirm)|couldn't (?:find|confirm)|unavailable"
    r"|does not (?:appear|seem) to|no (?:verified|confirmed|such)\b"
    r"|not applicable|n/?a$|none$|null$|unknown$)",
    re.I,
)
_HANDLE_RE = {
    'twitter_handle': re.compile(r'^[A-Za-z0-9_]{1,15}$'),
    'instagram_user': re.compile(r'^[A-Za-z0-9._]{1,30}$'),
    'tiktok_user': re.compile(r'^[A-Za-z0-9._]{1,24}$'),
}


def _first_line(v):
    return str(v or '').replace('\r', '\n').split('\n')[0].strip()


def _strip_wrappers(v):
    s = _first_line(v)
    s = re.sub(r'^```(?:json)?|```$', '', s).strip()
    return s.strip().strip('"').strip("'").strip().rstrip('.,;')


def _handle_from(v, host_pat):
    """Pull a bare username out of either a handle or a profile URL."""
    s = _strip_wrappers(v).lstrip('@')
    m = re.search(host_pat + r'/@?([A-Za-z0-9._\-]+)', s, re.I)
    if m:
        s = m.group(1)
    elif '/' in s or ' ' in s:
        return ''
    return s.strip('/').strip()


def sanitize(field, value):
    """Coerce one model answer into the column's expected shape, or ''.

    Never raises -- an unparseable answer is simply not a value.
    """
    s = _strip_wrappers(value)
    if not s or _REFUSAL_RE.search(s):
        return ''
    # a real identifier never contains a space; a sentence always does
    if ' ' in s and not s.lower().startswith('http'):
        return ''
    if len(s) > 300:
        return ''

    if field == 'facebook_page':
        m = re.search(r'(?:https?://)?(?:[a-z-]+\.)?facebook\.com/([^\s?#]+)', s, re.I)
        if not m:
            return ''
        slug = m.group(1).strip('/')
        if not slug or slug.lower() in ('profile.php', 'pages'):
            return ''
        return f"https://www.facebook.com/{slug}"

    if field == 'twitter_handle':
        h = _handle_from(s, r'(?:twitter|x)\.com')
        return h if _HANDLE_RE['twitter_handle'].match(h or '') else ''

    if field == 'instagram_user':
        h = _handle_from(s, r'instagram\.com')
        h = (h or '').lower()
        return h if _HANDLE_RE['instagram_user'].match(h) else ''

    if field == 'tiktok_user':
        h = _handle_from(s, r'tiktok\.com')
        h = (h or '').lower()
        return h if _HANDLE_RE['tiktok_user'].match(h) else ''

    if field == 'youtube_channel_username':
        m = re.search(r'youtube\.com/(channel/UC[A-Za-z0-9_\-]{20,}|@[A-Za-z0-9._\-]+'
                      r'|(?:c|user)/[A-Za-z0-9._\-]+)', s, re.I)
        if m:
            return f"https://www.youtube.com/{m.group(1)}"
        if re.match(r'^UC[A-Za-z0-9_\-]{20,}$', s):
            return f"https://www.youtube.com/channel/{s}"
        h = s.lstrip('@')
        if re.match(r'^[A-Za-z0-9._\-]{3,100}$', h):
            return f"https://www.youtube.com/@{h}"
        return ''

    if field == 'wikipedia_page':
        m = re.search(r'(?:https?://)?([a-z]{2,3}(?:-[a-z]+)?)\.(?:m\.)?wikipedia\.org/wiki/([^\s?#]+)',
                      s, re.I)
        if not m:
            return ''
        return f"https://{m.group(1).lower()}.wikipedia.org/wiki/{m.group(2)}"

    if field == 'imdb_id':
        m = re.search(r'\b(tt\d{6,10}|nm\d{6,10}|co\d{6,10})\b', s, re.I)
        return m.group(1).lower() if m else ''

    return s


# --------------------------------------------------------------------------
# client + call accounting
# --------------------------------------------------------------------------

_client = None
_client_lock = threading.Lock()
_stats_lock = threading.Lock()
_STATS = {'requests': 0, 'grounded': 0, 'errors': 0, 'cached': 0,
          'input_tokens': 0, 'output_tokens': 0, 'capped': 0}
# entity-level cache: the sheet gave the same entity different answers on its
# DAR and non-DAR rows (Wiederhoeft: twitter 'wiederhoeft_' vs 'wiederhoeft').
# Resolving once per normalised name removes that inconsistency and halves the
# request count on a run that emits DAR twins.
_CACHE = {}
_CACHE_LOCK = threading.Lock()


def _get_client():
    global _client
    with _client_lock:
        if _client is None:
            _client = _genai.Client(api_key=API_KEY,
                                    http_options={'timeout': TIMEOUT_MS})
        return _client


def stats():
    with _stats_lock:
        s = dict(_STATS)
    # 5,000 grounded search requests/month are free across Gemini 3.x, then
    # $14 per 1,000 -- so the request count *is* the cost.
    s['est_search_cost_usd'] = round(s['grounded'] * 0.014, 4)
    return s


def reset_stats():
    with _stats_lock:
        for k in _STATS:
            _STATS[k] = 0


def clear_cache():
    with _CACHE_LOCK:
        _CACHE.clear()


def _bump(**kw):
    with _stats_lock:
        for k, v in kw.items():
            _STATS[k] = _STATS.get(k, 0) + v


def _budget_left():
    if not MAX_REQUESTS:
        return True
    with _stats_lock:
        return _STATS['requests'] < MAX_REQUESTS


def _generate(prompt):
    """Raw grounded SDK call. Returns the response object, or None on failure.

    This is the only function that touches the network -- tests stub it.
    """
    try:
        cfg = _gtypes.GenerateContentConfig(
            tools=[_gtypes.Tool(google_search=_gtypes.GoogleSearch())],
            temperature=0.0,
        )
        return _get_client().models.generate_content(
            model=MODEL, contents=prompt, config=cfg)
    except Exception as e:  # noqa: BLE001 -- fail soft per entity
        log.warning("gemini call failed: %s", e)
        return None


def _ask(prompt):
    """One accounted grounded call. Returns the answer text, or ''."""
    if not _budget_left():
        _bump(capped=1)
        return ''
    _bump(requests=1)
    resp = _generate(prompt)
    if resp is None:
        _bump(errors=1)
        return ''
    try:
        um = getattr(resp, 'usage_metadata', None)
        if um:
            _bump(input_tokens=int(getattr(um, 'prompt_token_count', 0) or 0),
                  output_tokens=int(getattr(um, 'candidates_token_count', 0) or 0))
        cands = getattr(resp, 'candidates', None) or []
        if cands and getattr(cands[0], 'grounding_metadata', None):
            _bump(grounded=1)
    except Exception:  # pragma: no cover - accounting must never break a run
        pass
    return getattr(resp, 'text', '') or ''


def _parse_json_answer(text):
    """Lenient JSON extraction -- grounded replies often arrive fenced."""
    s = str(text or '').strip()
    s = re.sub(r'^```(?:json)?\s*|\s*```$', '', s, flags=re.S).strip()
    if not s:
        return {}
    try:
        obj = json.loads(s)
    except Exception:
        m = re.search(r'\{.*\}', s, re.S)
        if not m:
            return {}
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return {}
    return obj if isinstance(obj, dict) else {}


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def _clean_name(name):
    """The entity name as the sheet prompt wants it: no ' - DAR' suffix."""
    return re.sub(r'\s*[-–—]\s*DAR\s*$', '', str(name or ''), flags=re.I).strip()


def _cache_key(clean, ctx):
    return (clean.casefold(), ctx.casefold(), SOURCE, MODE)


# --------------------------------------------------------------------------
# backend: Apps Script bridge (native Sheets =GEMINI(), Workspace-covered)
# --------------------------------------------------------------------------

def _resolve_batch_script(pairs):
    """POST one batch of (clean_name, ctx) to the Apps Script web app.

    Returns ``{(clean, ctx): {field: value}}`` for whatever came back; a failed
    batch yields an empty dict rather than raising, so one bad batch cannot
    lose a whole run.
    """
    if not pairs:
        return {}
    import requests

    payload = {
        'token': SCRIPT_TOKEN,
        'entities': [{'name': n, 'context': c} for n, c in pairs],
    }
    _bump(requests=1)
    try:
        resp = requests.post(SCRIPT_URL, json=payload,
                             timeout=SCRIPT_TIMEOUT_S,
                             allow_redirects=True)
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:  # noqa: BLE001 -- fail soft per batch
        _bump(errors=1)
        log.warning("gemini apps-script batch failed: %s", e)
        return {}

    if isinstance(body, dict) and body.get('error'):
        _bump(errors=1)
        log.warning("gemini apps-script returned error: %s (%s)",
                    body.get('error'), body.get('detail', ''))
        return {}

    rows = (body or {}).get('results') or []
    # index the reply by casefolded name so ordering differences cannot mismatch
    by_name = {}
    for row in rows:
        if isinstance(row, dict):
            by_name[str(row.get('name', '')).casefold()] = row

    out = {}
    for n, c in pairs:
        row = by_name.get(n.casefold(), {})
        vals = {f: '' for f in FIELDS}
        for jkey, field in _JSON_KEYS.items():
            vals[field] = sanitize(field, row.get(jkey, ''))
        out[(n, c)] = vals
    return out


# --------------------------------------------------------------------------
# backend: comparison-sheet flow (source='sheet')
#
# Three phases, because =GEMINI() will not evaluate headlessly:
#   push     -> create a tab holding existing handles + formulas AS TEXT
#   activate -> convert them to live formulas while a person has the tab open
#   pull     -> read the computed values back
# --------------------------------------------------------------------------

def _script_call(action, **body):
    """One POST to the Apps Script web app. Returns the parsed body.

    Raises ScriptError with a readable message -- callers here are user-facing
    endpoints, so a failure needs to be reportable rather than swallowed.
    """
    if not (SCRIPT_URL and SCRIPT_TOKEN):
        raise ScriptError('GEMINI_SCRIPT_URL / GEMINI_SCRIPT_TOKEN are not set')
    import requests

    payload = dict(body)
    payload['action'] = action
    payload['token'] = SCRIPT_TOKEN
    _bump(requests=1)
    try:
        resp = requests.post(SCRIPT_URL, json=payload,
                             timeout=SCRIPT_TIMEOUT_S, allow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        _bump(errors=1)
        raise ScriptError('could not reach the comparison sheet: %s' % e)
    if isinstance(data, dict) and data.get('error'):
        _bump(errors=1)
        detail = data.get('detail') or ''
        raise ScriptError(('%s %s' % (data['error'], detail)).strip())
    return data or {}


class ScriptError(RuntimeError):
    """The Apps Script bridge could not do what was asked."""


def sheet_push(rows):
    """rows = [{title, title_type, context, existing:{field: value}}, ...]

    Returns {batch, url, rows, gemini_cells}: the tab that was created and a
    link straight to it.
    """
    payload = []
    for r in rows:
        payload.append({
            'title': str(r.get('title') or ''),
            'title_type': str(r.get('title_type') or ''),
            'context': str(r.get('context') or 'Unknown'),
            'existing': {f: str(r.get('existing', {}).get(f) or '') for f in FIELDS},
        })
    return _script_call('push', rows=payload)


def sheet_activate(batch):
    """Convert the batch's formula text into live formulas. The workbook must
    be open in someone's browser or nothing will compute."""
    return _script_call('activate', batch=batch)


def sheet_status(batch):
    return _script_call('status', batch=batch).get('status', {})


def sheet_pull(batch):
    """Read a batch back. Returns (status, [{title, title_type, context,
    existing:{...}, <field>: gemini_value}]) with every Gemini value passed
    through the same sanitiser the API path uses."""
    data = _script_call('pull', batch=batch)
    out = []
    for row in data.get('results') or []:
        rec = {
            'title': row.get('title', ''),
            'title_type': row.get('title_type', ''),
            'context': row.get('context', ''),
            'existing': {f: str((row.get('existing') or {}).get(f) or '')
                         for f in FIELDS},
        }
        for f in FIELDS:
            rec[f] = sanitize(f, row.get(f, ''))
        out.append(rec)
    return data.get('status', {}), out


# --------------------------------------------------------------------------
# backend: Gemini Developer API
# --------------------------------------------------------------------------

def _resolve_one_api(clean, ctx):
    out = {f: '' for f in FIELDS}
    if MODE == 'per_platform':
        for field, label in PLATFORMS:
            raw = _ask(_PER_PLATFORM_PROMPT.format(
                platform=label, name=clean, context=ctx))
            out[field] = sanitize(field, raw)
    else:
        obj = _parse_json_answer(_ask(_CONSOLIDATED_PROMPT.format(
            name=clean, context=ctx)))
        for jkey, field in _JSON_KEYS.items():
            out[field] = sanitize(field, obj.get(jkey, ''))
    return out


# --------------------------------------------------------------------------
# public API -- identical shape whichever backend is configured
# --------------------------------------------------------------------------

def resolve(name, context=''):
    """Resolve one entity to the seven handle fields.

    ``name`` is the title as-is; the ' - DAR' suffix is ignored exactly as the
    sheet prompt instructs.  Returns ``{field: value}`` with '' for anything
    unconfirmed, and ``{}`` when the feature is unavailable.
    """
    if not available():
        return {}
    clean = _clean_name(name)
    if not clean:
        return {}
    ctx = str(context or '').strip() or 'Unknown'
    key = _cache_key(clean, ctx)

    with _CACHE_LOCK:
        if key in _CACHE:
            _bump(cached=1)
            return dict(_CACHE[key])

    if SOURCE == 'sheet':
        # nothing to return synchronously: this source needs push/activate/pull
        return {}
    if SOURCE == 'appsscript':
        got = _resolve_batch_script([(clean, ctx)])
        out = got.get((clean, ctx), {f: '' for f in FIELDS})
    else:
        out = _resolve_one_api(clean, ctx)

    with _CACHE_LOCK:
        _CACHE[key] = dict(out)
    return out


def resolve_many(items, progress=None):
    """Resolve ``[(name, context), ...]``.

    Returns ``{(name, context): {field: value}}`` keyed by the ORIGINAL pairs
    the caller passed, so titles keep their ' - DAR' suffix on the way back.

    The API source resolves entities concurrently, one request each. The Apps
    Script source posts them in batches, because a single execution can fill a
    whole block of =GEMINI() formulas in one pass -- far fewer round trips and
    far less waiting than one request per title.

    One failing entity or batch never affects the others.
    """
    items = list(dict.fromkeys(items))
    if not items or not available() or SOURCE == 'sheet':
        return {}

    total = len(items)
    results = {}
    lock = threading.Lock()
    state = {'done': 0}

    def _tick(n=1):
        with lock:
            state['done'] += n
            done = state['done']
        if progress:
            try:
                progress(done, total)
            except Exception:
                pass

    # normalise once, and remember which originals map to each cleaned pair --
    # 'X' and 'X - DAR' collapse to one lookup and share its answer
    norm = {}
    for pair in items:
        name, ctx = pair
        clean = _clean_name(name)
        c = str(ctx or '').strip() or 'Unknown'
        norm.setdefault((clean, c), []).append(pair)

    todo = []
    for key_pair, originals in norm.items():
        ck = _cache_key(*key_pair)
        with _CACHE_LOCK:
            hit = _CACHE.get(ck)
        if hit is not None:
            _bump(cached=1)
            for p in originals:
                results[p] = dict(hit)
            _tick(len(originals))
        elif key_pair[0]:
            todo.append(key_pair)
        else:
            for p in originals:
                results[p] = {}
            _tick(len(originals))

    def _store(key_pair, vals):
        with _CACHE_LOCK:
            _CACHE[_cache_key(*key_pair)] = dict(vals)
        for p in norm[key_pair]:
            results[p] = dict(vals)
        _tick(len(norm[key_pair]))

    if SOURCE == 'appsscript':
        batches = [todo[i:i + SCRIPT_BATCH]
                   for i in range(0, len(todo), SCRIPT_BATCH)]

        def _one_batch(batch):
            try:
                got = _resolve_batch_script(batch)
            except Exception as e:  # noqa: BLE001
                log.warning("gemini batch failed: %s", e)
                got = {}
            for kp in batch:
                _store(kp, got.get(kp, {f: '' for f in FIELDS}))

        if WORKERS == 1 or len(batches) <= 1:
            for b in batches:
                _one_batch(b)
        else:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(WORKERS, len(batches))) as ex:
                list(ex.map(_one_batch, batches))
    else:
        def _one(key_pair):
            try:
                vals = _resolve_one_api(*key_pair)
            except Exception as e:  # noqa: BLE001
                log.warning("gemini resolve failed for %r: %s", key_pair[0], e)
                vals = {f: '' for f in FIELDS}
            _store(key_pair, vals)

        if WORKERS == 1 or len(todo) <= 1:
            for kp in todo:
                _one(kp)
        else:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(WORKERS, len(todo))) as ex:
                list(ex.map(_one, todo))

    return results
