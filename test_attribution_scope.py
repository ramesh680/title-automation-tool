"""The attribution window applies ONLY to a title whose accounts an earlier
title already used -- a franchise reusing its handles (Avengers, Hunger Games).
A standalone film, and the first film of a franchise, carry none.
"""
import io
import sys

import openpyxl
import pandas as pd

import app
import validator
import attribution_window as AW

FAIL = []


def check(label, got, want):
    if got != want:
        FAIL.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    else:
        print("  ok  %s" % label)


class Up:
    def __init__(self, df):
        b = io.BytesIO(); df.to_excel(b, index=False)
        self._b = b.getvalue(); self.filename = 'b.xlsx'
    def read(self):
        return self._b


def validate(rows):
    _, s = validator.validate_workbook(Up(pd.DataFrame(rows)))
    return {(f['column'].lower(), f['severity']): f['message'] for f in s['failures']}


def review(rows):
    b = io.BytesIO(); pd.DataFrame(rows).to_excel(b, index=False)
    xl, _ = app.build_review((b.getvalue(), 'b.xlsx'), auto_fetch=False)
    ws = openpyxl.load_workbook(io.BytesIO(xl))['Findings']
    hdr = [str(c.value).lower() for c in ws[1]]
    ri, ci, si = hdr.index('row'), hdr.index('column'), hdr.index('suggested value')
    return {(r[ri], str(r[ci]).lower()): str(r[si]) for r in ws.iter_rows(min_row=2, values_only=True)}


BASE = dict(title_category='Movies', genre='Action', primary_genre='Action')
SOC = dict(facebook_page='http://www.facebook.com/Avengers',
           twitter_handle='Avengers', instagram_user='avengers',
           tiktok_user='avengersofficial')

# ------------------------------------------------------------ the generator
print("\n-- Generator: a standalone film gets NO window --")
solo = app.create_row('Some Standalone Film - DAR', True, 'Neon',
                      dict(BASE, **SOC, released_on='2026-11-20',
                           trailer_released_on='2026-04-13'))
check("handle left bare", solo['twitter_handle'], 'Avengers')
check("no window recorded", solo.get('_attribution_window'), None)
check("and it is NOT pushed to review", bool(solo.get('_needs_review')), False)

print("\n-- Generator: discovery says an earlier title used these accounts --")
seq = app.create_row('Avengers: Endgame - DAR', True, 'Disney',
                     dict(BASE, **SOC, released_on='2026-11-20',
                          trailer_released_on='2026-04-13',
                          attribution_shared_handle=True,
                          attribution_shared_with='Avengers: Infinity War'))
check("stamped", seq['twitter_handle'], 'Avengers|2026-03-13')
check("window recorded", seq.get('_attribution_window'), '2026-03-13')

print("\n-- Generator: discovery says the accounts are new --")
first = app.create_row('Avengers - DAR', True, 'Disney',
                       dict(BASE, **SOC, released_on='2026-11-20',
                            trailer_released_on='2026-04-13',
                            attribution_shared_handle=False))
check("first film of a franchise is left bare", first['twitter_handle'], 'Avengers')

print("\n-- Generator: an explicit column overrides either way --")
forced = app.create_row('Some Film - DAR', True, 'Neon',
                        dict(BASE, **SOC, released_on='2026-11-20',
                             trailer_released_on='2026-04-13', is_sequel='yes'))
check("is_sequel=yes stamps", forced['twitter_handle'], 'Avengers|2026-03-13')
suppressed = app.create_row('Some Film - DAR', True, 'Neon',
                            dict(BASE, **SOC, released_on='2026-11-20',
                                 trailer_released_on='2026-04-13',
                                 attribution_shared_handle=True, is_sequel='no'))
check("an explicit no beats discovery", suppressed['twitter_handle'], 'Avengers')

print("\n-- Generator: the batch itself is evidence --")
buf = io.BytesIO()
pd.DataFrame([
    dict(BASE, **SOC, title='Avengers - DAR', released_on='2012-05-04',
         trailer_release_date='2012-02-04', network='Disney'),
    dict(BASE, **SOC, title='Avengers: Endgame - DAR', released_on='2019-04-26',
         trailer_release_date='2019-03-14', network='Disney'),
]).to_excel(buf, index=False)
rows = app.build_rows_from_upload((buf.getvalue(), 'f.xlsx'),
                                  include_dar=False, auto_fetch=False)
by_title = {r['title']: r for r in rows}
check("the earlier film carries no window",
      by_title['Avengers - DAR']['twitter_handle'], 'Avengers')
check("the later film is stamped from its OWN trailer",
      by_title['Avengers: Endgame - DAR']['twitter_handle'], 'Avengers|2019-02-14')

# ---------------------------------------------------------------- validator
print("\n-- Validator: a lone standalone row is not flagged --")
v = validate([dict(BASE, **SOC, title='Some Standalone Film - DAR',
                   released_on='2026-11-20', trailer_release_date='2026-04-13')])
check("no attribution finding",
      [k for k, m in v.items() if 'ttribution' in m], [])

print("\n-- Validator: a franchise in one file IS flagged, later film only --")
v = validate([
    dict(BASE, **SOC, title='Avengers - DAR', released_on='2012-05-04',
         trailer_release_date='2012-02-04'),
    dict(BASE, **SOC, title='Avengers: Endgame - DAR', released_on='2019-04-26',
         trailer_release_date='2019-03-14'),
])
msgs = [m for k, m in v.items() if 'ttribution window missing' in m]
check("exactly one title flagged", len(msgs) > 0, True)
check("the message names the expected date",
      any('2019-02-14' in m for m in msgs), True)

print("\n-- Validator: an explicit column alone is enough --")
v = validate([dict(BASE, **SOC, title='Some Film - DAR', is_sequel='yes',
                   released_on='2026-11-20', trailer_release_date='2026-04-13')])
check("flagged on the explicit column",
      any('2026-03-13' in m for m in v.values()), True)

print("\n-- Validator: a wrong date is still caught whatever the answer --")
v = validate([dict(BASE, title='Some Film - DAR', released_on='2026-11-20',
                   trailer_release_date='2026-04-13',
                   facebook_page='http://www.facebook.com/F|2026-03-14',
                   twitter_handle='F|2026-03-13', instagram_user='f|2026-03-13',
                   tiktok_user='fo|2026-03-13')])
check("wrong window date still fails",
      ('facebook_page', validator.SEV_FAIL) in v, True)

# ------------------------------------------------------------------- review
print("\n-- Review: standalone quiet, franchise flagged --")
r = review([dict(BASE, **SOC, title='Some Standalone Film - DAR',
                 released_on='2026-11-20', trailer_release_date='2026-04-13')])
check("no attribution findings on a standalone row",
      [k for k in r if k[1] in AW.COLUMNS], [])

r = review([
    dict(BASE, **SOC, title='Avengers - DAR', released_on='2012-05-04',
         trailer_release_date='2012-02-04'),
    dict(BASE, **SOC, title='Avengers: Endgame - DAR', released_on='2019-04-26',
         trailer_release_date='2019-03-14'),
])
later = {k[1]: v for k, v in r.items() if k[0] == 3}
earlier = {k[1]: v for k, v in r.items() if k[0] == 2}
check("the later film's four columns are flagged",
      sorted(c for c in later if c in AW.COLUMNS), sorted(AW.COLUMNS))
check("the suggestion uses its own trailer date",
      later.get('twitter_handle'), 'Avengers|2019-02-14')
check("the earlier film is left alone",
      [c for c in earlier if c in AW.COLUMNS], [])

print()
if FAIL:
    print("FAILED (%d):" % len(FAIL))
    for x in FAIL:
        print("  x  " + x)
    sys.exit(1)
print("Attribution-scope checks passed.")
