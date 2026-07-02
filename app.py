from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd
from io import BytesIO
from datetime import datetime
import re
import logging

try:
    from metadata_fetcher import fetch_metadata
except Exception:  # keep the app running even if the module is missing
    def fetch_metadata(title):
        return {}

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
        'active': metadata.get('active', True),
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

    return row


def _read_upload(file_storage):
    """Read an uploaded CSV/XLSX into a DataFrame with clean string values."""
    filename = (file_storage.filename or '').lower()
    if filename.endswith('.csv'):
        df = pd.read_csv(file_storage)
    else:
        df = pd.read_excel(file_storage, engine='openpyxl')
    df.columns = [str(c).strip() for c in df.columns]
    df = df.where(pd.notnull(df), '')
    return df


def _merge_meta(base_meta, title, auto_fetch):
    """Overlay auto-discovered metadata under any explicit metadata.
    Explicit values always win; auto-discovery only fills missing/blank fields.
    """
    if not auto_fetch:
        return base_meta or {}
    discovered = fetch_metadata(title) or {}
    merged = dict(discovered)
    for k, v in (base_meta or {}).items():
        if v not in (None, ''):
            merged[k] = v
    return merged


def build_rows_from_upload(file_storage, include_dar, auto_fetch=False):
    """Turn an uploaded file into fully-populated rows."""
    df = _read_upload(file_storage)
    lower_cols = {c.lower(): c for c in df.columns}
    has_full_schema = any(col in lower_cols for col in SOCIAL_COLUMNS + ['record_type', 'brand_id'])

    rows = []
    if has_full_schema:
        rename_map = {lower_cols[c.lower()]: c for c in COLUMNS if c.lower() in lower_cols}
        df = df.rename(columns=rename_map)
        df = df.reindex(columns=COLUMNS)
        df = df.where(pd.notnull(df), '')
        df['record_type'] = df['record_type'].replace('', 'INGESTED')
        rows = df.to_dict('records')
        if auto_fetch:
            rows = [_merge_meta(r, str(r.get('title', '')), True) for r in rows]
    else:
        title_col = lower_cols.get('title') or df.columns[0]
        type_col = lower_cols.get('type') or lower_cols.get('title_category')
        network_col = lower_cols.get('network')
        for _, r in df.iterrows():
            title = str(r[title_col]).strip()
            if not title:
                continue
            is_movie = True
            if type_col:
                is_movie = 'tv' not in str(r[type_col]).strip().lower()
            network = str(r[network_col]).strip() if network_col else ''
            meta = _merge_meta({}, title, auto_fetch)
            rows.append(create_row(title, is_movie, network, meta))
            if include_dar and ' - DAR' not in title:
                rows.append(create_row(f"{title} - DAR", is_movie, network, meta))
    return rows


def build_rows_from_titles(data):
    """Build rows from a manual titles payload (JSON)."""
    titles = data.get('titles', [])
    include_dar = data.get('includeDar', True)
    auto_fetch = bool(data.get('autoFetch', False))
    rows = []
    for title in titles:
        title = title.strip()
        if not title:
            continue
        is_movie = data.get('titles_type', {}).get(title, 'movie') == 'movie'
        network = data.get('networks', {}).get(title, '')
        metadata = _merge_meta(data.get('metadata', {}).get(title, {}), title, auto_fetch)
        rows.append(create_row(title, is_movie, network, metadata))
        if include_dar and ' - DAR' not in title:
            rows.append(create_row(f"{title} - DAR", is_movie, network, metadata))
    return rows


def collect_rows(limit=None):
    """Collect rows from either an uploaded file or a JSON titles payload."""
    if request.files.get('file'):
        include_dar = request.form.get('includeDar', 'true').lower() != 'false'
        auto_fetch = request.form.get('autoFetch', 'false').lower() == 'true'
        rows = build_rows_from_upload(request.files['file'], include_dar, auto_fetch)
    else:
        data = request.get_json(silent=True) or {}
        rows = build_rows_from_titles(data)
    if limit:
        rows = rows[:limit]
    return rows


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/preview', methods=['POST'])
def preview_data():
    try:
        rows = collect_rows()
        if not rows:
            return jsonify({'error': 'No titles provided'}), 400
        df = pd.DataFrame(rows)
        df = df.reindex(columns=COLUMNS).where(lambda x: pd.notnull(x), '')
        return jsonify({
            'total_rows': len(df),
            'preview': df.head(4).to_dict('records'),
            'columns': list(df.columns)
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


if __name__ == '__main__':
    app.run(debug=True)
