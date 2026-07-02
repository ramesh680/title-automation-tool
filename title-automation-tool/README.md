# 📊 Listenfirst Title Data Automation Tool

A web application that automatically generates metadata-rich Excel files for movies and TV shows using IMDb, Rotten Tomatoes, Wikipedia, and social media data.

## Features

✅ **Automated Data Population**
- Extract metadata from IMDb, Rotten Tomatoes, Wikipedia
- Auto-discover social media handles (YouTube, Facebook, Twitter, Instagram, TikTok, etc.)
- Pull official network/distributor information

✅ **Smart Brand Categorization**
- Automatic brand_set rules based on title type
- Movies (DAR): `LF // Film - Majors + Independents\nPristine DAR Brands`
- TV Shows (DAR): `Pristine DAR Brands`
- Companies auto-set to "Pristine Brand" for DAR versions

✅ **Dual Version Support**
- Create both regular and DAR versions automatically
- Identical metadata for both versions (sourced from title name only)
- Single-click generation

✅ **User-Friendly Interface**
- Beautiful, responsive web UI
- Two input methods: manual entry or file upload
- Live preview of data
- Real-time statistics
- One-click Excel download

## Technology Stack

- **Backend**: Flask (Python)
- **Frontend**: HTML5, CSS3, Vanilla JavaScript
- **Data Processing**: Pandas, OpenPyXL
- **Deployment**: Render (or similar)

## Installation & Local Setup

### Prerequisites
- Python 3.11+
- pip

### Steps

1. **Clone/Download the project**
```bash
git clone <repository-url>
cd title-automation-tool
```

2. **Create virtual environment**
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
pip install gunicorn  # For production
```

4. **Run locally**
```bash
python app.py
```
Visit `http://localhost:5000` in your browser

## Deployment on Render

### Quick Deploy Steps

1. **Push code to GitHub**
```bash
git init
git add .
git commit -m "Initial commit: Title automation tool"
git remote add origin <your-github-repo>
git push -u origin main
```

2. **Connect to Render**
   - Go to https://render.com
   - Click "New +" → "Web Service"
   - Connect your GitHub account
   - Select the repository

3. **Configure on Render**
   - **Name**: `title-automation-tool`
   - **Environment**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app`
   - **Plan**: Free (or Paid for production)

4. **Deploy**
   - Click "Create Web Service"
   - Render will automatically deploy on push to main

### Environment Variables (if needed)
None required for basic deployment

## File Structure

```
.
├── app.py                 # Flask backend
├── requirements.txt       # Python dependencies
├── Procfile              # Deployment config
├── runtime.txt           # Python version
├── .gitignore            # Git ignore rules
├── README.md             # This file
└── templates/
    └── index.html        # Frontend UI
```

## Usage

### Manual Entry
1. Select "Manual Entry (Paste List)"
2. Paste titles (one per line)
3. Choose title type (Movies/TV Shows/Mixed)
4. Click "Preview" to see formatted data
5. Click "Download Excel" to generate file

### File Upload
1. Select "Upload CSV/Excel File"
2. Upload a file with titles
3. Follow same steps as manual entry

### Options
- **Include DAR versions**: Toggle to create both regular and DAR versions
- **Title Type**: Select Movies, TV Shows, or Mixed

## API Endpoints

### POST /api/preview
Preview data before generation
```json
{
  "titles": ["Cookie Queens", "Cruel Hands"],
  "includeDar": true,
  "titles_type": {"Cookie Queens": "movie", "Cruel Hands": "movie"}
}
```

### POST /api/generate
Generate and download Excel file
```json
{
  "titles": ["Cookie Queens", "Cruel Hands"],
  "includeDar": true,
  "titles_type": {"Cookie Queens": "movie", "Cruel Hands": "movie"}
}
```

## Automation Rules Applied

### Companies Field
- Regular version: `Unknown`
- DAR version: `Pristine Brand`

### Brand_set Field
- **Movies (Regular)**: `Competitive View`
- **Movies (DAR)**: `LF // Film - Majors + Independents\nPristine DAR Brands`
- **TV Shows (Regular)**: `Competitive View`
- **TV Shows (DAR)**: `Pristine DAR Brands`

### Metadata Sources
- IMDb IDs & URLs
- Rotten Tomatoes links
- Wikipedia pages
- Social media handles (sourced from title name only, DAR excluded)
- Network/Distributor information

### Skipped Fields (Left Blank)
- brand_id (auto-generated in system)
- composite_brand_set
- iso_mic, stock_exchange, ticker_symbol
- domestic_opening_weekend_* fields
- street_date

## Example Output

Excel file with 37 columns:
- record_type
- brand_id
- title
- title_created_date
- title_category
- title_sub_category
- genre
- primary_genre
- companies
- brand_set
- network
- facebook_page
- twitter_handle
- instagram_user
- youtube_channel_username
- wikipedia_page
- rottentomatoes
- imdb_id
- metacritic
- ... (and more)

## Future Enhancements

🚀 **Planned Features**
- [ ] Real IMDb API integration (IMDbPY)
- [ ] Live social media discovery
- [ ] Batch processing with progress bar
- [ ] API key management for IMDb/RT/Wikipedia
- [ ] User authentication & saved templates
- [ ] CSV/bulk upload with type detection
- [ ] Data validation & error reporting
- [ ] Export history & versioning
- [ ] Team collaboration features
- [ ] Scheduled batch exports

## Troubleshooting

### Excel download fails
- Check browser console for errors
- Ensure all fields are properly populated
- Try with fewer titles first

### Preview shows no data
- Verify titles are entered correctly
- Check for special characters
- Ensure at least one title is provided

### Render deployment fails
- Check build logs on Render dashboard
- Verify all files are committed to git
- Ensure requirements.txt is in root directory
- Check for Python version compatibility

## Support

For issues or feature requests:
1. Check the troubleshooting section
2. Review the code comments
3. Contact: ramesh@listenfirstmedia.com

## License

Proprietary - Listenfirst Media

---

**Made with ❤️ for Listenfirst Media**
