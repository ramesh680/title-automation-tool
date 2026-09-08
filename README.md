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
None required for basic deployment.

The optional **Gemini comparison columns** feature (see below) reads:

| Variable | Default | Purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` | *(unset)* | Enables the feature. Without it the toggle reports itself unavailable and nothing else changes. |
| `GEMINI_MODEL` | `gemini-flash-latest` | Pin an explicit model (e.g. `gemini-3.7-flash`) if you want stable behaviour across runs. |
| `GEMINI_MODE` | `consolidated` | `consolidated` = one grounded request per title for all 7 platforms. `per_platform` = one request per platform, exactly as the Ops sheet did. |
| `GEMINI_WORKERS` | `4` | Concurrent grounded requests. |
| `GEMINI_MAX_REQUESTS` | `400` | Hard ceiling on grounded requests per run, so one large upload cannot drain the monthly search allowance. `0` = no cap. |
| `GEMINI_TIMEOUT_MS` | `60000` | Per-request timeout. |

## File Structure

```
.
├── app.py                 # Flask backend
├── gemini_resolver.py     # Optional Gemini-grounded handle resolver (comparison only)
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
- **Add Gemini comparison columns**: See below

## Gemini Comparison Columns

A benchmarking feature: it asks Gemini the *same question* the Ops team's
`=GEMINI()` sheet formulas asked (the NYFW SS27 brand sheet, columns
U/W/Y/Z/AB/AG/AI), then puts its answers next to the tool's own so the two
handle sources can be scored against each other.

**It never changes an ingest column.** The existing resolver (Wikidata / IMDb /
Rotten Tomatoes / Metacritic) remains the only thing that writes
`instagram_user` and friends. Gemini's answers land on two extra sheets:

- **Gemini Compare** — one row per generated row, with a column triple per
  platform:

  | title | instagram_user | instagram_user_gemini | instagram_user_match |
  | --- | --- | --- | --- |
  | Grace Ling - DAR | gracelingofficial | gracelingofficial | match |
  | Jane Wade - DAR | | janewade_ | gemini only |

  `_match` is one of `match`, `mismatch`, `existing only`, `gemini only`,
  `both blank`. Comparison is done on canonical forms, so
  `http://www.facebook.com/x` and `https://facebook.com/x/` count as a match,
  as do `MagdaButrym` and `@magdabutrym`.

- **Gemini Summary** — per-field tallies plus `agreement_when_both_filled`,
  which is the number the exercise exists to produce.

Covered fields: `facebook_page`, `twitter_handle`, `instagram_user`,
`youtube_channel_username`, `tiktok_user`, `wikipedia_page`, `imdb_id`.

### Three things this does differently from the sheet

1. **One request per title, not seven.** Each of the sheet's seven prompts
   already carried the return-format rules for *all* platforms; only the first
   sentence differed. `consolidated` mode asks once and reads a JSON object
   back — same information, 1/7th the grounded requests. `GEMINI_MODE=per_platform`
   restores the sheet's exact 7-call behaviour for A/B purposes.
2. **Answers are validated.** The sheet run put prose in a data column
   (`"I do not have enough information to answer the query..."` in Wiederhoeft's
   IMDb cell). Every answer is coerced into the column's expected shape —
   handle charset and length limits, platform-specific URL hosts, `tt`/`nm` IDs —
   and anything that fails becomes blank.
3. **One answer per entity.** The sheet gave the same entity different answers
   on its DAR and non-DAR rows (Wiederhoeft: `wiederhoeft_` vs `wiederhoeft` on
   Twitter). Resolution is cached per normalised name, so twins always agree and
   a DAR run costs the same as a non-DAR one.

### Cost

Grounding is the cost driver, not tokens: 5,000 free Google Search requests per
month across Gemini 3.x models, then $14 per 1,000. In `consolidated` mode that
is one request per title (~5,000 titles/month free, ~$0.014/title after);
`per_platform` is seven (~710 titles/month free, ~$0.10/title after). Every run
reports its request count and estimated search cost in the preview panel and via
`GET /api/gemini_status`.

### Caveat worth knowing before trusting the numbers

Gemini returns handles it has not verified exist (`giovannaflor3s`,
`hikarinoyamii` in the source sheet run). Validation checks *shape*, not
existence. The tool's own path already drops dead Instagram handles
(`metadata_fetcher._instagram_alive`), so a `mismatch` is not automatically a
Gemini error — treat the comparison as a candidate generator, not a verdict.

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
