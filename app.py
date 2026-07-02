from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd
from io import BytesIO
from datetime import datetime
import logging

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


def create_row(title, is_movie, network="", metadata=None):
    """Create a data row for a title - ALL 42 COLUMNS POPULATED.

    Any value present in `metadata` overrides the computed default, so an
    uploaded row's social-media channels (and every other field) are preserved.
    """
    is_dar = " - DAR" in title
    title_category = "Movies" if is_movie else "TV Shows"

    if metadata is None:
        metadata = {}

    # companies = network for non-DAR, Pristine Brand for DAR
    if is_dar:
        companies = "Pristine Brand"
        if is_movie:
            brand_set = "LF // Film - Majors + Independents\nPristine DAR Brands"
        else:
            brand_set = "Pristine DAR Brands"
    else:
        companies = network if network else "Unknown"
        brand_set = "Competitive View"

    row = {
        'record_type': metadata.get('record_type', 'INGESTED'),
        'brand_id': metadata.get('brand_id', ''),
        'title': title,
        'title_created_date': metadata.get('title_created_date', datetime.now().strftime('%Y-%m-%d')),
        'title_category': metadata.get('title_category', title_category),
        'title_sub_category': metadata.get('title_sub_category', 'Release - Limited\nStudio - Independent'),
        'genre': metadata.get('genre', ''),
        'primary_genre': metadata.get('primary_genre', ''),
        'iso_mic': metadata.get('iso_mic', ''),
        'stock_exchange': metadata.get('stock_exchange', ''),
        'ticker_symbol': metadata.get('ticker_symbol', ''),
        'companies': metadata.get('companies', companies),
        'brand_set': metadata.get('brand_set', brand_set),
        'composite_brand_set': metadata.get('composite_brand_set', ''),
        'active': metadata.get('active', True),
        'released_on': metadata.get('released_on', ''),
        'domestic_opening_weekend_box_office': metadata.get('domestic_opening_weekend_box_office', ''),
        'domestic_opening_weekend_screens': metadata.get('domestic_opening_weekend_screens', ''),
        'domestic_opening_weekend_rank': metadata.get('domestic_opening_weekend_rank', ''),
        'street_date': metadata.get('street_date', ''),
        'network': metadata.get('network', network),
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
        # previously missing columns
        'twitter_search_terms': metadata.get('twitter_search_terms', ''),
        'instagram_business_hashtags': metadata.get('instagram_business_hashtags', ''),
        'twitter_search_term_keywords': metadata.get('twitter_search_term_keywords', ''),
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
    # Normalise column names and blank out NaNs
    df.columns = [str(c).strip() for c in df.columns]
    df = df.where(pd.notnull(df), '')
    return df


def build_rows_from_upload(file_storage, include_dar):
    """Turn an uploaded file into fully-populated rows.

    * Full-schema files (already containing social channels / record_type) are
      passed through with every column preserved -- this is what carries the
      social-media channels from the test run into the output.
    * Simple title-list files (just a `title` column, optionally `type`) are
      expanded with defaults and optional DAR duplicates, like manual entry.
    """
    df = _read_upload(file_storage)
    lower_cols = {c.lower(): c for c in df.columns}

    has_full_schema = any(col in lower_cols for col in SOCIAL_COLUMNS + ['record_type', 'brand_id'])

    rows = []
    if has_full_schema:
        # Preserve everything: reindex to the canonical 42 columns.
        # Existing columns (incl. all social channels) keep their values;
        # any of the 42 that are absent are added as blanks.
        rename_map = {lower_cols[c.lower()]: c for c in COLUMNS if c.lower() in lower_cols}
        df = df.rename(columns=rename_map)
        df = df.reindex(columns=COLUMNS)
        df = df.where(pd.notnull(df), '')
        # Fill sensible defaults only where blank
        df['record_type'] = df['record_type'].replace('', 'INGESTED')
        rows = df.to_dict('records')
    else:
        # Title-list mode
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
            rows.append(create_row(title, is_movie, network))
            if include_dar and ' - DAR' not in title:
                rows.append(create_row(f"{title} - DAR", is_movie, network))
    return rows


def build_rows_from_titles(data):
    """Build rows from a manual titles payload (JSON)."""
    titles = data.get('titles', [])
    include_dar = data.get('includeDar', True)
    rows = []
    for title in titles:
        title = title.strip()
        if not title:
            continue
        is_movie = data.get('titles_type', {}).get(title, 'movie') == 'movie'
        network = data.get('networks', {}).get(title, '')
        metadata = data.get('metadata', {}).get(title, {})
        rows.append(create_row(title, is_movie, network, metadata))
        if include_dar and ' - DAR' not in title:
            rows.append(create_row(f"{title} - DAR", is_movie, network, metadata))
    return rows


def collect_rows(limit=None):
    """Collect rows from either an uploaded file or a JSON titles payload."""
    if request.files.get('file'):
        include_dar = request.form.get('includeDar', 'true').lower() != 'false'
        rows = build_rows_from_upload(request.files['file'], include_dar)
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
