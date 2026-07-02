from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd
from io import BytesIO
import requests
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime
import logging

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

logging.basicConfig(level=logging.INFO)

# Define all columns
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

def search_title_data(title, is_movie=True):
    """Search for title data from IMDb and other sources"""
    try:
        clean_title = title.replace(" - DAR", "").strip()
        imdb_data = search_imdb(clean_title, is_movie)
        return imdb_data
    except Exception as e:
        logging.error(f"Error searching data for {title}: {str(e)}")
        return {}

def search_imdb(title, is_movie=True):
    """Search IMDb for title data"""
    try:
        return {
            'imdb_id': '',
            'rottentomatoes': '',
            'wikipedia_page': '',
            'network': '',
            'genre': '',
            'primary_genre': '',
            'released_on': '',
            'companies': 'Unknown',
            'title_sub_category': 'Release - Limited\nStudio - Independent',
            'facebook_page': '',
            'twitter_handle': '',
            'youtube_channel_username': '',
            'youtube_channel_company': ''
        }
    except Exception as e:
        logging.error(f"IMDb search error: {str(e)}")
        return {}

def create_row(title, is_movie, base_data=None):
    """Create a data row for a title"""
    is_dar = " - DAR" in title
    clean_title = title.replace(" - DAR", "").strip()
    title_category = "Movies" if is_movie else "TV Shows"
    
    if base_data is None:
        base_data = search_title_data(title, is_movie)
    
    # Determine brand_set and companies based on version
    if is_dar:
        companies = "Pristine Brand"
        if is_movie:
            brand_set = "LF // Film - Majors + Independents\nPristine DAR Brands"
        else:
            brand_set = "Pristine DAR Brands"
    else:
        companies = "Unknown"
        brand_set = "Competitive View"
    
    row = {
        'record_type': 'INGESTED',
        'brand_id': '',
        'title': title,
        'title_created_date': datetime.now().strftime('%Y-%m-%d'),
        'title_category': title_category,
        'title_sub_category': base_data.get('title_sub_category', 'Release - Limited\nStudio - Independent'),
        'genre': base_data.get('genre', ''),
        'primary_genre': base_data.get('primary_genre', ''),
        'iso_mic': '',
        'stock_exchange': '',
        'ticker_symbol': '',
        'companies': companies,
        'brand_set': brand_set,
        'composite_brand_set': '',
        'active': True,
        'released_on': base_data.get('released_on', ''),
        'domestic_opening_weekend_box_office': '',
        'domestic_opening_weekend_screens': '',
        'domestic_opening_weekend_rank': '',
        'street_date': '',
        'network': base_data.get('network', ''),
        'facebook_page': base_data.get('facebook_page', ''),
        'facebook_verified': '',
        'twitter_handle': base_data.get('twitter_handle', ''),
        'twitter_verified': '',
        'instagram_user': '',
        'youtube_channel_username': base_data.get('youtube_channel_username', ''),
        'youtube_channel_company': base_data.get('youtube_channel_company', ''),
        'tiktok_user': '',
        'linkedin_page': '',
        'threads_page': '',
        'pinterest_user_username': '',
        'pinterest_board': '',
        'wikipedia_page': base_data.get('wikipedia_page', ''),
        'rottentomatoes': base_data.get('rottentomatoes', ''),
        'imdb_id': base_data.get('imdb_id', ''),
        'metacritic': ''
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
            
            rows.append(create_row(title, is_movie))
            
            if include_dar:
                rows.append(create_row(f"{title} - DAR", is_movie))
        
        df = pd.DataFrame(rows)
        
        return jsonify({
            'total_rows': len(df),
            'preview': df.head(4).to_dict('records'),
            'columns': list(df.columns)
        })
    
    except Exception as e:
        logging.error(f"Error previewing data: {str(e)}")
        return jsonify({'error': str(e)}), 500

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
            
            rows.append(create_row(title, is_movie))
            
            if include_dar:
                rows.append(create_row(f"{title} - DAR", is_movie))
        
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
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
