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

API_KEY = (os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY') or '').strip()
MODEL = os.getenv('GEMINI_MODEL', 'gemini-flash-latest').strip()
MODE = os.getenv('GEMINI_MODE', 'consolidated').strip().lower()
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
    return bool(SDK_OK and API_KEY)


def status():
    return {
        'available': available(),
        'sdk_installed': SDK_OK,
        'api_key_set': bool(API_KEY),
        'model': MODEL,
        'mode': MODE if MODE in ('consolidated', 'per_platform') else 'consolidated',
        'max_requests_per_run': MAX_REQUESTS,
    }


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

def resolve(name, context=''):
    """Resolve one entity to the seven handle fields.

    ``name`` is the title as-is; the ' - DAR' suffix is ignored exactly as the
    sheet prompt instructs.  Returns ``{field: value}`` with '' for anything
    unconfirmed, and ``{}`` when the feature is unavailable.
    """
    if not available():
        return {}
    clean = re.sub(r'\s*[-–—]\s*DAR\s*$', '', str(name or ''), flags=re.I).strip()
    if not clean:
        return {}
    ctx = str(context or '').strip() or 'Unknown'
    key = (clean.casefold(), ctx.casefold(), MODE)

    with _CACHE_LOCK:
        if key in _CACHE:
            _bump(cached=1)
            return dict(_CACHE[key])

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

    with _CACHE_LOCK:
        _CACHE[key] = dict(out)
    return out


def resolve_many(items, progress=None):
    """Resolve ``[(name, context), ...]`` concurrently.

    Returns ``{(name, context): {field: value}}``.  Order-independent; one
    failing entity never affects the others.
    """
    items = list(dict.fromkeys(items))
    if not items or not available():
        return {}
    results = {}
    lock = threading.Lock()
    state = {'done': 0}
    total = len(items)

    def _one(pair):
        name, ctx = pair
        try:
            val = resolve(name, ctx)
        except Exception as e:  # noqa: BLE001
            log.warning("gemini resolve failed for %r: %s", name, e)
            val = {}
        with lock:
            results[pair] = val
            state['done'] += 1
            done = state['done']
        if progress:
            try:
                progress(done, total)
            except Exception:
                pass

    if WORKERS == 1 or total == 1:
        for p in items:
            _one(p)
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(WORKERS, total)) as ex:
            list(ex.map(_one, items))
    return results
