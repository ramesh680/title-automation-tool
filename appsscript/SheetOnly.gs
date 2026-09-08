/**
 * TitleForge — Gemini handle comparison, run entirely inside Sheets
 * =================================================================
 * Zero API cost and zero server involvement: the answers come from the NATIVE
 * Google Sheets =GEMINI() function, covered by the Workspace subscription.
 *
 * Why it lives here and not on the server
 * ---------------------------------------
 * Two hard constraints, both verified rather than assumed:
 *   1. =GEMINI() never evaluates on its own. Sheets treats it as an "AI cell"
 *      and holds it unevaluated until a person selects the range and clicks
 *      "Generate and fill" on the chip that appears under the selection.
 *      No script can trigger that, which is why a formula written by a
 *      server-side execution sits unevaluated forever.
 *   2. This Workspace only allows Apps Script web apps to be shared with
 *      "Anyone within ListenFirst", so an external server cannot call in.
 * Between them, the work belongs in the sheet. Nothing here needs a secret,
 * an endpoint, or a deploy.
 *
 * How to use it
 * -------------
 *   1. Generate any export from TitleForge and paste one sheet of it here —
 *      the ingest sheet is fine, so is a "Gemini Compare" sheet. All this
 *      needs is a header row containing a "title" column.
 *   2. TitleForge Gemini → 1. Write Gemini formulas
 *   3. TitleForge Gemini → 2. Activate formulas
 *   4. Select the *_gemini columns, then click "Generate and fill" on the
 *      chip under the selection. This is the one step no script can do for
 *      you. Watch the cells fill in.
 *   5. TitleForge Gemini → 3. Score comparison
 */

var FIELDS = ['facebook_page', 'twitter_handle', 'instagram_user',
              'youtube_channel_username', 'tiktok_user', 'wikipedia_page', 'imdb_id'];
var LABELS = {
  facebook_page: 'Facebook',
  twitter_handle: 'Twitter/X',
  instagram_user: 'Instagram',
  youtube_channel_username: 'YouTube',
  tiktok_user: 'TikTok',
  wikipedia_page: 'Wikipedia',
  imdb_id: 'IMDb'
};
var GEM_SUFFIX = '_gemini';
var MATCH_SUFFIX = '_match';
var FORMULA_MARK = '=GEMINI(';

/** The shared rule block, verbatim from the Ops NYFW sheet's formulas. */
var RULES = 'Return only the appropriate identifier or full URL, depending on the '
  + 'platform. For Facebook, return the full official Facebook profile/page URL. '
  + 'For Twitter/X, return the official username or profile URL. For Instagram, '
  + 'return the official username only. For YouTube, return the official channel '
  + 'username or channel URL. For TikTok, return the official username only. For '
  + 'Wikipedia, return the full official Wikipedia page URL. For IMDb, return the '
  + 'IMDb ID or full IMDb title/name URL. Use only the genuine official or verified '
  + 'account. Never return a fan, parody, tribute, news, aggregator, unofficial or '
  + 'unrelated account. If no official account/page can be confidently confirmed, '
  + 'return nothing.';

function onOpen() {
  SpreadsheetApp.getUi().createMenu('TitleForge Gemini')
    .addItem('1. Write Gemini formulas (this tab)', 'writeFormulas')
    .addItem('2. Activate formulas (this tab)', 'activateFormulas')
    .addItem('3. Score comparison (this tab)', 'scoreComparison')
    .addSeparator()
    .addItem('Show progress', 'showProgress')
    .addItem('Revert formulas to text', 'revertToText')
    .addItem('Diagnose', 'debugConvert')
    .addToUi();
}

// ---------------------------------------------------------------------------
// sheet plumbing
// ---------------------------------------------------------------------------

/**
 * The tab the menu actions work on.
 *
 * Normally that is simply the tab in front of you. But a menu item can fire
 * with a different tab active than the one you are looking at (a sidebar or a
 * previous run can leave the "active" tab pointing elsewhere), which silently
 * makes every action a no-op. So: use the active tab when it looks like a
 * TitleForge export, otherwise fall back to the first tab that does.
 */
function sheet_() {
  var ss = SpreadsheetApp.getActive();
  var act = ss.getActiveSheet();
  if (headerMap_(act).title) return act;
  var sheets = ss.getSheets();
  for (var i = 0; i < sheets.length; i++) {
    if (headerMap_(sheets[i]).title) return sheets[i];
  }
  return act;
}

/**
 * The =GEMINI(...) text held by a cell, or '' when it holds something else.
 * Tolerates the several shapes a "formula parked as text" can come back as:
 * leading whitespace, a leading apostrophe, or the cell having already been
 * turned into a real formula (in which case getValues() gives the result and
 * only getFormula() gives the source).
 */
function formulaText_(value, formula) {
  var f = String(formula === null || formula === undefined ? '' : formula)
    .replace(/^[\s']+/, '');
  if (f.indexOf(FORMULA_MARK) === 0) return f;
  var v = String(value === null || value === undefined ? '' : value)
    .replace(/^[\s']+/, '');
  return v.indexOf(FORMULA_MARK) === 0 ? v : '';
}
function toast_(msg) { SpreadsheetApp.getActive().toast(msg, 'TitleForge Gemini', 8); }
function alert_(msg) { SpreadsheetApp.getUi().alert(msg); }

/** Header name -> 1-based column index, for the active tab. */
function headerMap_(sh) {
  var lastCol = sh.getLastColumn();
  if (lastCol < 1) return {};
  var hdr = sh.getRange(1, 1, 1, lastCol).getValues()[0];
  var map = {};
  for (var c = 0; c < hdr.length; c++) {
    var name = String(hdr[c] === null || hdr[c] === undefined ? '' : hdr[c]).trim();
    if (name && !(name in map)) map[name] = c + 1;
  }
  return map;
}

/** Find a column, appending it to the header row when it does not exist. */
function ensureColumn_(sh, map, name) {
  if (map[name]) return map[name];
  var col = sh.getLastColumn() + 1;
  sh.getRange(1, col).setValue(name).setFontWeight('bold');
  map[name] = col;
  return col;
}

/** Which of the seven fields this tab actually carries. */
function presentFields_(map) {
  var out = [];
  for (var i = 0; i < FIELDS.length; i++) {
    if (map[FIELDS[i]]) out.push(FIELDS[i]);
  }
  return out;
}

function requireTitles_(sh, map) {
  if (!map.title) {
    alert_('This tab has no "title" column in row 1.\n\n'
      + 'Paste a TitleForge export here first — the ingest sheet or a '
      + '"Gemini Compare" sheet both work.');
    return 0;
  }
  var rows = sh.getLastRow() - 1;
  if (rows < 1) {
    alert_('No data rows found under the header.');
    return 0;
  }
  return rows;
}

function cleanName_(v) {
  return String(v === null || v === undefined ? '' : v)
    .replace(/\s*[-–—]\s*DAR\s*$/i, '').trim();
}

/** Context for the prompt: an explicit column, else the category columns. */
function contextFor_(row, map) {
  var keys = ['context_sent_to_gemini', 'context', 'title_category'];
  for (var i = 0; i < keys.length; i++) {
    if (map[keys[i]]) {
      var v = String(row[map[keys[i]] - 1] || '').split('\n')[0].trim();
      if (v) {
        var sub = map.title_sub_category
          ? String(row[map.title_sub_category - 1] || '').split('\n')[0].trim() : '';
        if (sub && sub.toLowerCase() !== 'unknown' && sub.toLowerCase() !== v.toLowerCase()) {
          return v + ' / ' + sub;
        }
        return v;
      }
    }
  }
  return 'Unknown';
}

function buildPrompt_(field, name, context) {
  return 'Using Google Search, find the official verified ' + LABELS[field]
    + ' profile or official page for the entity below. Name: ' + name
    + " (Note: please ignore the ' - DAR' suffix if present). Category/Context: "
    + (context || 'Unknown') + '. ' + RULES
    + ' Return only the requested value on one line. Do not provide explanations.';
}

// ---------------------------------------------------------------------------
// step 1 — write the formulas as inert text
// ---------------------------------------------------------------------------

function writeFormulas() {
  var sh = sheet_();
  var map = headerMap_(sh);
  var n = requireTitles_(sh, map);
  if (!n) return;
  var fields = presentFields_(map);
  if (!fields.length) {
    alert_('None of the seven handle columns were found on this tab.\n\n'
      + 'Expected any of: ' + FIELDS.join(', '));
    return;
  }

  var data = sh.getRange(2, 1, n, sh.getLastColumn()).getValues();
  var written = 0;
  var skipped = 0;

  for (var f = 0; f < fields.length; f++) {
    var field = fields[f];
    var gcol = ensureColumn_(sh, map, field + GEM_SUFFIX);
    var existingGem = sh.getRange(2, gcol, n, 1).getValues();
    var out = [];
    for (var r = 0; r < n; r++) {
      var cur = String(existingGem[r][0] === null || existingGem[r][0] === undefined
        ? '' : existingGem[r][0]).trim();
      // never overwrite an answer that is already there
      if (cur && cur.indexOf(FORMULA_MARK) !== 0) {
        out.push([existingGem[r][0]]);
        skipped++;
        continue;
      }
      var name = cleanName_(data[r][map.title - 1]);
      if (!name) { out.push(['']); continue; }
      var p = buildPrompt_(field, name, contextFor_(data[r], map)).replace(/"/g, '""');
      out.push(['=GEMINI("' + p + '")']);
      written++;
    }
    var rng = sh.getRange(2, gcol, n, 1);
    rng.setNumberFormat('@');   // plain text: nothing evaluates yet
    rng.setValues(out);
  }
  SpreadsheetApp.flush();
  toast_(written + ' formula(s) written as text across ' + fields.length + ' platform(s)'
    + (skipped ? '; ' + skipped + ' cell(s) already had answers and were left alone' : '')
    + '. Next: "2. Activate formulas".');
}

// ---------------------------------------------------------------------------
// step 2 — make them live, which only works with this tab open
// ---------------------------------------------------------------------------

function activateFormulas() {
  var n = convert_(sheet_(), true);
  toast_(n
    ? n + ' formula(s) are now live. Next: select the *_gemini columns and '
        + 'click "Generate and fill" on the chip under the selection — Sheets '
        + 'will not compute them until you do.'
    : 'Nothing to activate — run "1. Write Gemini formulas" first.');
}

function revertToText() {
  var n = convert_(sheet_(), false);
  toast_(n + ' formula(s) reverted to text. Whatever had already computed is kept.');
}

function convert_(sh, toLive) {
  var map = headerMap_(sh);
  var n = sh.getLastRow() - 1;
  if (n < 1) return 0;
  var count = 0;
  for (var f = 0; f < FIELDS.length; f++) {
    var gcol = map[FIELDS[f] + GEM_SUFFIX];
    if (!gcol) continue;
    var rng = sh.getRange(2, gcol, n, 1);
    var vals = rng.getValues();
    var fxs = rng.getFormulas();
    var out = [];
    var any = false;
    for (var r = 0; r < n; r++) {
      var src = formulaText_(vals[r][0], fxs[r][0]);
      if (src) { any = true; count++; out.push([src]); }
      else out.push([vals[r][0]]);
    }
    if (!any) continue;
    if (toLive) {
      rng.setNumberFormat('General');
      rng.setFormulas(out);
    } else {
      rng.setNumberFormat('@');
      rng.setValues(out);
    }
  }
  SpreadsheetApp.flush();
  return count;
}

/**
 * Diagnostic: dumps what each tab and each gemini column actually contains.
 * Run it from the menu (or the editor) when a step reports "nothing to do"
 * but the cells plainly are not empty.
 */
function debugConvert() {
  var ss = SpreadsheetApp.getActive();
  var lines = [];
  lines.push('active tab : ' + ss.getActiveSheet().getName());
  lines.push('chosen tab : ' + sheet_().getName());
  var sheets = ss.getSheets();
  for (var i = 0; i < sheets.length; i++) {
    var sh = sheets[i];
    var map = headerMap_(sh);
    var names = [];
    for (var k in map) names.push(k);
    lines.push('');
    lines.push('--- ' + sh.getName() + '  rows=' + sh.getLastRow()
      + ' cols=' + sh.getLastColumn());
    lines.push('    headers: ' + names.join(' | '));
    for (var f = 0; f < FIELDS.length; f++) {
      var gcol = map[FIELDS[f] + GEM_SUFFIX];
      if (!gcol || sh.getLastRow() < 2) continue;
      var c = sh.getRange(2, gcol);
      var v = c.getValue();
      lines.push('    ' + FIELDS[f] + GEM_SUFFIX + ' col=' + gcol
        + ' fmt=' + c.getNumberFormat()
        + ' typeof=' + (typeof v)
        + ' detected=' + (formulaText_(v, c.getFormula()) ? 'YES' : 'no'));
      lines.push('        value  =' + JSON.stringify(String(v).slice(0, 60)));
      lines.push('        formula=' + JSON.stringify(String(c.getFormula()).slice(0, 60)));
    }
  }
  var out = lines.join('\n');
  Logger.log(out);
  try { SpreadsheetApp.getUi().alert(out); } catch (e) { /* no UI when run from editor */ }
  return out;
}

function showProgress() {
  var sh = sheet_();
  var map = headerMap_(sh);
  var n = sh.getLastRow() - 1;
  var cells = 0, filled = 0, text = 0, pending = 0;
  for (var f = 0; f < FIELDS.length; f++) {
    var gcol = map[FIELDS[f] + GEM_SUFFIX];
    if (!gcol || n < 1) continue;
    var vals = sh.getRange(2, gcol, n, 1).getValues();
    for (var r = 0; r < n; r++) {
      var s = String(vals[r][0] === null || vals[r][0] === undefined ? '' : vals[r][0]).trim();
      cells++;
      if (s.indexOf(FORMULA_MARK) === 0) text++;
      else if (s === '' || s.charAt(0) === '#') pending++;
      else filled++;
    }
  }
  alert_('Rows: ' + Math.max(0, n)
    + '\nGemini cells: ' + cells
    + '\nFilled: ' + filled
    + '\nStill text (not activated): ' + text
    + '\nComputing or blank: ' + pending
    + '\n\n' + (text > 0
        ? 'Run "2. Activate formulas".'
        : pending > 0
          ? 'Activated, but not generated yet. Select the *_gemini columns and '
            + 'click "Generate and fill" on the chip under the selection.'
          : cells ? 'Done. Run "3. Score comparison".' : 'Run "1. Write Gemini formulas" first.'));
}

// ---------------------------------------------------------------------------
// step 3 — score it, using the same rules the app uses
// ---------------------------------------------------------------------------

var REFUSAL = /\b(i (?:do|don't|do not|am|cannot|can't|could not|couldn't)\b|i'm sorry|sorry\b|unable to|not enough information|no official|could not (?:find|confirm)|couldn't (?:find|confirm)|unavailable|does not (?:appear|seem) to|no (?:verified|confirmed|such)\b|not applicable)/i;

/** Coerce one answer into the column's expected shape, or ''. */
function sanitize_(field, value) {
  var s = String(value === null || value === undefined ? '' : value)
    .split('\n')[0].trim().replace(/^"|"$/g, '').replace(/[.,;]$/, '');
  if (!s) return '';
  if (/^(n\/?a|none|null|unknown)$/i.test(s)) return '';
  if (REFUSAL.test(s)) return '';
  if (s.charAt(0) === '#') return '';
  if (s.indexOf(' ') >= 0 && !/^https?:/i.test(s)) return '';
  if (s.length > 300) return '';

  var m;
  if (field === 'facebook_page') {
    m = s.match(/(?:https?:\/\/)?(?:[a-z-]+\.)?facebook\.com\/([^\s?#]+)/i);
    if (!m) return '';
    var slug = m[1].replace(/\/+$/, '');
    if (!slug || /^(profile\.php|pages)$/i.test(slug)) return '';
    return 'https://www.facebook.com/' + slug;
  }
  if (field === 'twitter_handle') {
    var h = handleFrom_(s, /(?:twitter|x)\.com/i);
    return /^[A-Za-z0-9_]{1,15}$/.test(h) ? h : '';
  }
  if (field === 'instagram_user') {
    var hi = handleFrom_(s, /instagram\.com/i).toLowerCase();
    return /^[A-Za-z0-9._]{1,30}$/.test(hi) ? hi : '';
  }
  if (field === 'tiktok_user') {
    var ht = handleFrom_(s, /tiktok\.com/i).toLowerCase();
    return /^[A-Za-z0-9._]{1,24}$/.test(ht) ? ht : '';
  }
  if (field === 'youtube_channel_username') {
    m = s.match(/youtube\.com\/(channel\/UC[A-Za-z0-9_-]{20,}|@[A-Za-z0-9._-]+|(?:c|user)\/[A-Za-z0-9._-]+)/i);
    if (m) return 'https://www.youtube.com/' + m[1];
    if (/^UC[A-Za-z0-9_-]{20,}$/.test(s)) return 'https://www.youtube.com/channel/' + s;
    var hy = s.replace(/^@/, '');
    return /^[A-Za-z0-9._-]{3,100}$/.test(hy) ? 'https://www.youtube.com/@' + hy : '';
  }
  if (field === 'wikipedia_page') {
    m = s.match(/(?:https?:\/\/)?([a-z]{2,3}(?:-[a-z]+)?)\.(?:m\.)?wikipedia\.org\/wiki\/([^\s?#]+)/i);
    return m ? 'https://' + m[1].toLowerCase() + '.wikipedia.org/wiki/' + m[2] : '';
  }
  if (field === 'imdb_id') {
    m = s.match(/\b((?:tt|nm|co)\d{6,10})\b/i);
    return m ? m[1].toLowerCase() : '';
  }
  return s;
}

function handleFrom_(v, hostRe) {
  var s = String(v).replace(/^@/, '');
  var m = s.match(new RegExp(hostRe.source + '\\/@?([A-Za-z0-9._-]+)', 'i'));
  if (m) return m[1];
  if (s.indexOf('/') >= 0 || s.indexOf(' ') >= 0) return '';
  return s.replace(/\/+$/, '').trim();
}

/** Comparable forms of one cell; cells may hold several values. */
function keys_(field, value) {
  var parts = String(value === null || value === undefined ? '' : value).split(/[\n|]+/);
  var out = {};
  for (var i = 0; i < parts.length; i++) {
    var p = parts[i].trim();
    if (!p) continue;
    var k = sanitize_(field, p);
    if (k) out[k.toLowerCase()] = true;
  }
  return out;
}

function matchLabel_(field, existing, guess) {
  var haveE = String(existing || '').trim() !== '';
  var haveG = String(guess || '').trim() !== '';
  if (!haveE && !haveG) return 'both blank';
  if (haveE && !haveG) return 'existing only';
  if (haveG && !haveE) return 'gemini only';
  var ek = keys_(field, existing);
  var gk = keys_(field, guess);
  for (var k in gk) { if (ek[k]) return 'match'; }
  return 'mismatch';
}

function scoreComparison() {
  var sh = sheet_();
  var map = headerMap_(sh);
  var n = requireTitles_(sh, map);
  if (!n) return;

  var fields = [];
  for (var i = 0; i < FIELDS.length; i++) {
    if (map[FIELDS[i]] && map[FIELDS[i] + GEM_SUFFIX]) fields.push(FIELDS[i]);
  }
  if (!fields.length) {
    alert_('Nothing to score yet — run "1. Write Gemini formulas" and '
      + '"2. Activate formulas" first.');
    return;
  }

  var data = sh.getRange(2, 1, n, sh.getLastColumn()).getValues();
  var tally = {};
  var totals = { match: 0, mismatch: 0 };

  for (var f = 0; f < fields.length; f++) {
    var field = fields[f];
    var mcol = ensureColumn_(sh, map, field + MATCH_SUFFIX);
    var col = [];
    tally[field] = { match: 0, mismatch: 0, 'existing only': 0, 'gemini only': 0, 'both blank': 0 };
    for (var r = 0; r < n; r++) {
      var existing = data[r][map[field] - 1];
      var raw = data[r][map[field + GEM_SUFFIX] - 1];
      var guess = sanitize_(field, raw);
      var label = matchLabel_(field, existing, guess);
      tally[field][label]++;
      if (label === 'match' || label === 'mismatch') totals[label]++;
      col.push([label]);
    }
    sh.getRange(2, mcol, n, 1).setNumberFormat('@').setValues(col);
  }
  SpreadsheetApp.flush();

  var lines = ['Scored ' + n + ' row(s).', ''];
  for (var f2 = 0; f2 < fields.length; f2++) {
    var t = tally[fields[f2]];
    var both = t.match + t.mismatch;
    lines.push(fields[f2] + ' — match ' + t.match + ', mismatch ' + t.mismatch
      + ', gemini only ' + t['gemini only'] + ', existing only ' + t['existing only']
      + (both ? '  (agreement ' + Math.round(t.match / both * 100) + '%)' : ''));
  }
  var allBoth = totals.match + totals.mismatch;
  lines.push('');
  lines.push(allBoth
    ? 'Overall agreement where both sides had a value: '
        + Math.round(totals.match / allBoth * 100) + '%'
    : 'No row had a value on both sides, so there is no agreement rate yet. '
        + 'If every Gemini cell is empty, the formulas were never activated '
        + 'or never finished computing.');
  alert_(lines.join('\n'));
}
