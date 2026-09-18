"""Validator: the attribution-window rule must behave like the Review's."""
import io
import sys

import pandas as pd
import validator
import app
import attribution_window as AW

FAIL = []


def check(label, got, want):
    if got != want:
        FAIL.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    else:
        print("  ok  %s" % label)


class Up:
    """Minimal stand-in for a Werkzeug FileStorage."""
    def __init__(self, df, name='book.xlsx'):
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        self._b = buf.getvalue()
        self.filename = name

    def read(self):
        return self._b


def run(rows):
    _, summary = validator.validate_workbook(Up(pd.DataFrame(rows)))
    return {(f['column'], f['severity']): f['message'] for f in summary['failures']}


# is_sequel marks these rows as ones whose accounts an earlier title already
# used -- the only rows the window applies to. test_attribution_scope.py covers
# what happens when a row does NOT qualify.
BASE = dict(title_category='Movies', released_on='2026-11-20', genre='Action',
            primary_genre='Action', companies='Pristine Brand', is_sequel='yes')

print("\n-- with a trailer_release_date column --")
f = run([dict(BASE, title='Movie A - DAR', trailer_release_date='2026-04-13',
              facebook_page='http://www.facebook.com/MovieA|2026-03-13',
              twitter_handle='MovieA|2026-03-13',
              instagram_user='moviea|2026-03-13',
              tiktok_user='movieaofficial|2026-03-13')])
check("correct window -> no attribution issue",
      [k for k in f if 'attribution' in str(f[k]).lower() or 'Attribution' in f[k]], [])

f = run([dict(BASE, title='Movie B - DAR', trailer_release_date='2026-04-13',
              facebook_page='http://www.facebook.com/MovieB',
              twitter_handle='MovieB', instagram_user='movieb',
              tiktok_user='moviebofficial')])
check("missing window -> FAIL on all four",
      sorted(c for (c, sev) in f if sev == 'fail'),
      ['facebook_page', 'instagram_user', 'tiktok_user', 'twitter_handle'])
check("message names the expected date",
      '2026-03-13' in f[('twitter_handle', 'fail')], True)

f = run([dict(BASE, title='Movie D - DAR', trailer_release_date='2026-04-13',
              facebook_page='http://www.facebook.com/MovieD|2026-03-14',
              twitter_handle='MovieD|2026-03-13', instagram_user='movied|2026-03-13',
              tiktok_user='moviedofficial|2026-03-13')])
check("wrong date -> FAIL on that column only",
      sorted(c for (c, sev) in f if sev == 'fail'), ['facebook_page'])

print("\n-- without a trailer_release_date column --")
f = run([dict(BASE, title='Movie C - DAR',
              facebook_page='http://www.facebook.com/MovieC',
              twitter_handle='MovieC', instagram_user='moviec',
              tiktok_user='moviecofficial')])
check("missing window -> WARN, not FAIL",
      sorted(c for (c, sev) in f if sev == 'warn'),
      ['facebook_page', 'instagram_user', 'tiktok_user', 'twitter_handle'])
check("no hard failures raised", [c for (c, sev) in f if sev == 'fail'], [])

f = run([dict(BASE, title='Movie E - DAR',
              facebook_page='http://www.facebook.com/MovieE|2026-03-13',
              twitter_handle='MovieE|2026-03-13', instagram_user='moviee|2026-03-13',
              tiktok_user='movieeofficial|2026-03-13')])
check("well-formed window passes unchecked", list(f), [])

print("\n-- scope --")
f = run([dict(BASE, title='Movie B', trailer_release_date='2026-04-13',
              facebook_page='http://www.facebook.com/MovieB',
              twitter_handle='MovieB', instagram_user='movieb',
              tiktok_user='moviebofficial')])
check("base (non-DAR) row is never flagged",
      [c for (c, sev) in f if 'attribution' in str(f[(c, sev)]).lower()], [])

f = run([dict(BASE, title='Some Show - DAR', title_category='TV Shows',
              trailer_release_date='2026-04-13', released_on='2026-11-20',
              facebook_page='http://www.facebook.com/Show',
              twitter_handle='Show', instagram_user='show', tiktok_user='showofficial')])
check("TV DAR row is never flagged",
      [c for (c, sev) in f if 'ttribution' in f[(c, sev)]], [])

f = run([dict(BASE, title='Movie F - DAR', trailer_release_date='2026-04-13',
              facebook_page='', twitter_handle='', instagram_user='', tiktok_user='')])
check("blank cells are a gap, not a bad value",
      [c for (c, sev) in f if 'ttribution' in f[(c, sev)]], [])

print("\n-- shape and consistency --")
f = run([dict(BASE, title='Movie G - DAR', twitter_handle='MovieG|2026-3-13',
              facebook_page='http://www.facebook.com/MovieG|2026-03-13',
              instagram_user='movieg|2026-03-13', tiktok_user='moviegofficial|2026-03-13')])
check("malformed '|2026-3-13' fails on format even with no trailer column",
      ('twitter_handle', 'fail') in f, True)

f = run([dict(BASE, title='Movie H - DAR',
              facebook_page='http://www.facebook.com/H1|2026-03-13\nhttp://www.facebook.com/H2|2026-04-13',
              twitter_handle='H|2026-03-13', instagram_user='h|2026-03-13',
              tiktok_user='hofficial|2026-03-13')])
check("lines disagreeing with each other fail",
      'inconsistent' in f.get(('facebook_page', 'fail'), ''), True)

print("\n-- lenient DAR detection now matches the Generator --")
for t in ('Movie I -DAR', 'Movie I  -  dar', 'Movie I – DAR'):
    f = run([dict(BASE, title=t, trailer_release_date='2026-04-13',
                  facebook_page='http://www.facebook.com/I', twitter_handle='I',
                  instagram_user='i', tiktok_user='iofficial')])
    check("%-22r is treated as DAR" % t,
          ('twitter_handle', 'fail') in f, True)
    check("   ...and app agrees", app._is_dar_title(t), True)

print("\n-- Validator and Review agree on the same row --")
import openpyxl
row = dict(BASE, title='Movie J - DAR', trailer_release_date='2026-04-13',
           facebook_page='http://www.facebook.com/MovieJ',
           twitter_handle='MovieJ', instagram_user='moviej', tiktok_user='moviejofficial')
v = run([row])
buf = io.BytesIO(); pd.DataFrame([row]).to_excel(buf, index=False)
rx, _ = app.build_review((buf.getvalue(), 'm.xlsx'), auto_fetch=False)
ws = openpyxl.load_workbook(io.BytesIO(rx))['Findings']
hdr = [str(c.value).lower() for c in ws[1]]
ci, si = hdr.index('column'), hdr.index('suggested value')
review = {r[ci]: str(r[si]) for r in ws.iter_rows(min_row=2, values_only=True)}

check("Validator fails the four columns",
      sorted(c for (c, sev) in v if sev == 'fail'), sorted(AW.COLUMNS))
check("Review flags the same four",
      sorted(c for c in review if c in AW.COLUMNS), sorted(AW.COLUMNS))
check("Review suggests the stamped handle",
      review['twitter_handle'], 'MovieJ|2026-03-13')
check("both sections used the same window date",
      all('2026-03-13' in review[c] for c in AW.COLUMNS)
      and all('2026-03-13' in v[(c, 'fail')] for c in AW.COLUMNS), True)

print()
if FAIL:
    print("FAILED (%d):" % len(FAIL))
    for x in FAIL:
        print("  x  " + x)
    sys.exit(1)
print("All Validator attribution-window checks passed.")
