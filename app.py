from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd
from io import BytesIO
from datetime import datetime
import logging

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
logging.basicConfig(level=logging.INFO)

# Define all 37 columns in EXACT order
COLUMNS = [
    'record_type', 'brand_id', 'title', 'title_created_date', 'title_category',
    'title_sub_category', 'genre', 'primary_genre', 'iso_mic', 'stock_exchange',
    'ticker_symbol', 'companies', 'brand_set', 'composite_brand_set', 'active',
    'released_on', 'domestic_opening_weekend_box_office', 'domestic_opening_weekend_screens',
    'domestic_opening_weekend_rank', 'street_date', 'network', 'facebook_page',
    'facebook_verified', 'twitter_handle', 'twitter_verified', 'instagram_user',
    'youtube_channel_username', 'youtube_channel_company', 'tiktok_user', 'linkedin_page',
    'threads_page', 'pinterest_user_username', 'pinterest_board', 'wikipedia_page',
    'rottentomatoes', 'imdb_id', 'metacritic'
]

def create_row(title, is_movie, network="", metadata=None):
    """Create a data row for a title - ALL 37 COLUMNS POPULATED"""
    is_dar = " - DAR" in title
    clean_title = title.replace(" - DAR", "").strip()
    title_category = "Movies" if is_movie else "TV Shows"

    if metadata is None:
        metadata = {}

    # FIXED: companies = network for non-DAR, Pristine Brand for DAR
    if is_dar:
        companies = "Pristine Brand"
        if is_movie:
            brand_set = "LF // Film - Majors + Independents\nPristine DAR Brands"
        else:
            brand_set = "Pristine DAR Brands"
    else:
        companies = network if network else "Unknown"
        brand_set = "Competitive View"

    # Create complete row with ALL columns
    row = {
        'record_type': 'INGESTED',
        'brand_id': metadata.get('brand_id', ''),
        'title': title,
        'title_created_date': metadata.get('title_created_date', datetime.now().strftime('%Y-%m-%d')),
        'title_category': title_category,
        'title_sub_category': metadata.get('title_sub_category', 'Release - Limited\nStudio - Independent'),
        'genre': metadata.get('genre', ''),
        'primary_genre': metadata.get('primary_genre', ''),
        'iso_mic': metadata.get('iso_mic', ''),
        'stock_exchange': metadata.get('stock_exchange', ''),
        'ticker_symbol': metadata.get('ticker_symbol', ''),
        'companies': companies,  # FIXED
        'brand_set': brand_set,
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
        'metacritic': metadata.get('metacritic', '')
    }

    return row

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/preview', methods=['POST'])
def preview_data():
    try:
        data = request.json
        titles = data.get('titles', [])
        include_dar = data.get('includeDar', True)

        if not titles:
            return jsonify({'error': 'No titles provided'}), 400

        rows = []
        for title in titles[:5]:
            title = title.strip()
            if not title:
                continue

            is_movie = data.get('titles_type', {}).get(title, 'movie') == 'movie'
            network = data.get('networks', {}).get(title, '')
            metadata = data.get('metadata', {}).get(title, {})

            rows.append(create_row(title, is_movie, network, metadata))

            if include_dar:
                rows.append(create_row(f"{title} - DAR", is_movie, network, metadata))

        df = pd.DataFrame(rows)
        df = df[COLUMNS]

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
        data = request.json
        titles = data.get('titles', [])
        include_dar = data.get('includeDar', True)

        if not titles:
            return jsonify({'error': 'No titles provided'}), 400

        rows = []
        for title in titles:
            title = title.strip()
            if not title:
                continue

            is_movie = data.get('titles_type', {}).get(title, 'movie') == 'movie'
            network = data.get('networks', {}).get(title, '')
            metadata = data.get('metadata', {}).get(title, {})

            rows.append(create_row(title, is_movie, network, metadata))

            if include_dar:
                rows.append(create_row(f"{title} - DAR", is_movie, network, metadata))

        df = pd.DataFrame(rows)
        df = df[COLUMNS]

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
