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
    from metadata_fetcher import fetch_metadata
except Exception:  # keep the app running even if the module is missing
    def fetch_metadata(title, is_movie=True):
        return {}

import json
import base64

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
    # --- previously missing columns (AL -> AP) ---
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


def generate_search_terms(clean_title, network, year, is_dar):
    """Generate twitter_search_terms (AL) and twitter_search_term_keywords (AN)
    following the exact pattern observed in the Test_Run export.
    """
    label = "DAR" if is_dar else "Operations - Core Title"
    t_hash = _alnum(clean_title)
    n_hash = _alnum(network)

    # twitter_search_terms
    lines = [f"#{t_hash}|{label}|{label}"]
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


# company / network -> manager-team lookup. POPULATED FROM repo logic (media-tools-hub).
# Keys are lowercased network or companies names. Left empty until that mapping is wired in;
# while empty, url_managers stays blank (no incorrect output).
URL_MANAGER_MAP = {}


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


def create_row(title, is_movie, network="", metadata=None):
    """Create a data row for a title - ALL 42 COLUMNS POPULATED.

    Any value present in `metadata` overrides the computed default, so an
    uploaded row's channels (and every other field) are preserved.
    `companies` follows the effective network; search-term columns are
    generated when not already supplied.
    """
    metadata = metadata or {}
    is_dar = " - DAR" in title
    clean_title = re.sub(r"\s*-\s*DAR\s*$", "", title, flags=re.IGNORECASE).strip()
    title_category = "Movies" if is_movie else "TV Shows"

    # Effective network = discovered/explicit network, else the passed arg
    eff_network = (str(metadata.get('network') or network or '')).strip()

    if is_dar:
        companies = "Pristine Brand"
        if is_movie:
            brand_set = "LF // Film - Majors + Independents\nPristine DAR Brands"
        else:
            brand_set = "Pristine DAR Brands"
    else:
        companies = eff_network if eff_network else "Unknown"
        brand_set = "Competitive View"
        # Wide theatrical releases carry an extra brand_set line
        _sub = str(metadata.get('title_sub_category') or 'Release - Limited\nStudio - Independent')
        if 'release - wide' in _sub.lower():
            brand_set = "Competitive View\n[Data Feed] Film - Wide Release + Custom Requests"

    # Release year for search-term generation
    rel = str(metadata.get('released_on') or metadata.get('title_created_date') or '')
    year = rel[:4] if rel[:4].isdigit() else ''

    gen_terms, gen_keywords = generate_search_terms(clean_title, eff_network, year, is_dar)

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
        'title_sub_category': mv('title_sub_category', 'Release - Limited\nStudio - Independent'),
        'genre': metadata.get('genre', ''),
        'primary_genre': metadata.get('primary_genre', ''),
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
    discovered = fetch_metadata(title, is_movie) or {}
    merged = dict(discovered)
    for k, v in (base_meta or {}).items():
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
        rename_map = {lower_cols[c.lower()]: c for c in COLUMNS if c.lower() in lower_cols}
        df = df.rename(columns=rename_map)
        df = df.reindex(columns=COLUMNS)
        df = df.where(pd.notnull(df), '')
        df['record_type'] = df['record_type'].replace('', 'INGESTED')
        records = df.to_dict('records')
        if max_titles:
            records = records[:max_titles]
        total = len(records)
        for i, r in enumerate(records):
            if auto_fetch:
                r = _merge_meta(r, str(r.get('title', '')), True,
                                is_movie=('tv' not in str(r.get('title_category', '')).lower()))
            rows.append(r)
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
            rows.append(create_row(title, is_movie, network, meta))
            if include_dar and ' - DAR' not in title:
                rows.append(create_row(f"{title} - DAR", is_movie, network, meta))
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
        rows.append(create_row(title, is_movie, network, metadata))
        if include_dar and ' - DAR' not in title:
            rows.append(create_row(f"{title} - DAR", is_movie, network, metadata))
        if progress:
            progress(i + 1, total)
    return rows


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


@app.route('/api/preview', methods=['POST'])
def preview_data():
    try:
        rows = collect_rows(preview=True)
        if not rows:
            return jsonify({'error': 'No titles provided'}), 400
        df = pd.DataFrame(rows)
        df = df.reindex(columns=COLUMNS).where(lambda x: pd.notnull(x), '')
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
        df = pd.DataFrame(rows)
        df = df.reindex(columns=COLUMNS).where(lambda x: pd.notnull(x), '')
        output = BytesIO()
        df.to_excel(output, sheet_name='Sheet1', index=False)
        output.seek(0)
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
        df = pd.DataFrame(rows).reindex(columns=COLUMNS).where(lambda x: pd.notnull(x), '')
        out = BytesIO()
        df.to_excel(out, sheet_name='Sheet1', index=False)
        out.seek(0)
        _job_set(jid, status='done', file=out.getvalue(), rows=len(df),
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
