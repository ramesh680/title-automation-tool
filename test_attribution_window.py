"""Attribution window (Sep 2026) -- Movies DAR rows.

The DAR row's facebook_page / twitter_handle / instagram_user / tiktok_user
carry a '|YYYY-MM-DD' suffix: ONE CALENDAR MONTH before the official trailer.
"""
import os
import sys

os.environ.setdefault('ATTRIBUTION_WINDOW', '1')

import app  # noqa: E402

FAIL = []


def check(label, got, want):
    if got != want:
        FAIL.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    else:
        print("  ok  %s" % label)


# ------------------------------------------------------------------ date math
print("\n-- attribution_window_date --")
check("Hunger Games trailer 2026-04-13",
      app.attribution_window_date('2026-04-13'), '2026-03-13')
check("January rolls back a year",
      app.attribution_window_date('2026-01-09'), '2025-12-09')
check("31 Mar clamps to 28 Feb (non-leap)",
      app.attribution_window_date('2026-03-31'), '2026-02-28')
check("31 Mar clamps to 29 Feb (leap)",
      app.attribution_window_date('2024-03-31'), '2024-02-29')
check("31 May -> 30 Apr",
      app.attribution_window_date('2026-05-31'), '2026-04-30')
check("datetime-shaped cell",
      app.attribution_window_date('2026-04-13 00:00:00'), '2026-03-13')
check("blank", app.attribution_window_date(''), '')
check("nan", app.attribution_window_date('nan'), '')
check("not a date", app.attribution_window_date('April 13 2026'), '')
check("impossible date", app.attribution_window_date('2026-02-30'), '')

# ------------------------------------------------------------ trailer sources
print("\n-- trailer_date_from --")
check("discovery key", app.trailer_date_from({'trailer_released_on': '2026-04-13'}),
      '2026-04-13')
check("sheet column", app.trailer_date_from({'trailer_release_date': '2026-04-13'}),
      '2026-04-13')
check("odd header casing/spacing",
      app.trailer_date_from({'Trailer Release Date': '2026-04-13'}), '2026-04-13')
check("no trailer field", app.trailer_date_from({'released_on': '2026-11-20'}), '')

# ------------------------------------------------------------------- the row
print("\n-- create_row: the Hunger Games example --")
META = {
    'released_on': '2026-11-20',
    'trailer_released_on': '2026-04-13',
    'facebook_page': 'http://www.facebook.com/TheHungerGamesMovie',
    'twitter_handle': 'TheHungerGames',
    'instagram_user': 'thehungergames',
    'tiktok_user': 'hungergamesofficial',
    'network': 'Lionsgate',
    'genre': 'Action', 'primary_genre': 'Action',
}
TITLE = 'The Hunger Games: Sunrise on the Reaping'

dar = app.create_row(TITLE + ' - DAR', True, 'Lionsgate', dict(META))
check("facebook_page", dar['facebook_page'],
      'http://www.facebook.com/TheHungerGamesMovie|2026-03-13')
check("twitter_handle", dar['twitter_handle'], 'TheHungerGames|2026-03-13')
check("instagram_user", dar['instagram_user'], 'thehungergames|2026-03-13')
check("tiktok_user", dar['tiktok_user'], 'hungergamesofficial|2026-03-13')

print("\n-- scope --")
base = app.create_row(TITLE, True, 'Lionsgate', dict(META))
check("base (non-DAR) row is untouched", base['twitter_handle'], 'TheHungerGames')
check("base facebook untouched", base['facebook_page'],
      'http://www.facebook.com/TheHungerGamesMovie')

tv = app.create_tv_row('Some Show - DAR', 'Netflix',
                       dict(META, title_category='TV Shows'))
check("TV DAR row is untouched", tv['twitter_handle'], 'TheHungerGames')

talent = app.create_talent_row('Jane Doe - DAR', dict(META))
check("Talent DAR row is untouched", talent['twitter_handle'], 'TheHungerGames')

print("\n-- columns the window must NOT reach --")
check("url_managers keeps the bare handle",
      'twitter|http://twitter.com/TheHungerGames|' in dar['url_managers']
      or 'TheHungerGames|2026-03-13' not in dar['url_managers'], True)
check("url_managers has no stamped facebook",
      '|2026-03-13|' in dar['url_managers'], False)
check("twitter_search_terms keeps the bare handle",
      '@thehungergames|DAR|DAR' in dar['twitter_search_terms'], True)
check("youtube_channel_username untouched",
      '2026-03-13' in str(dar['youtube_channel_username']), False)

print("\n-- multi-line cells and re-stamping --")
multi = app.create_row(TITLE + ' - DAR', True, 'Lionsgate', dict(
    META, facebook_page='http://www.facebook.com/A\nhttp://www.facebook.com/B'))
check("every line stamped", multi['facebook_page'],
      'http://www.facebook.com/A|2026-03-13\nhttp://www.facebook.com/B|2026-03-13')
check("an existing date is replaced, not doubled",
      app.attribution_stamp('handle|2020-01-01', '2026-03-13'),
      'handle|2026-03-13')
check("blank cell stays blank",
      app.create_row(TITLE + ' - DAR', True, 'Lionsgate',
                     dict(META, tiktok_user=''))['tiktok_user'], '')

print("\n-- no trailer date -> flagged, not silently skipped --")
nodate = dict(META)
nodate.pop('trailer_released_on')
nt = app.create_row(TITLE + ' - DAR', True, 'Lionsgate', nodate)
check("handle left bare", nt['twitter_handle'], 'TheHungerGames')
check("row flagged for review", bool(nt.get('_needs_review')), True)
check("reason mentions the trailer date",
      'trailer' in str(nt.get('_review_reason')).lower(), True)

print("\n-- review comparison --")
# expected value carries the window; the manual file does not -> Mismatch,
# and the suggestion keeps the reviewer's own (differently cased) handle
ok, sugg = app._review_compare('twitter_handle', 'TheHungerGames',
                               'TheHungerGames|2026-03-13', title=TITLE + ' - DAR',
                               cat='Movies')
check("missing window is flagged", ok, False)
check("suggestion stamps the manual handle", sugg, 'TheHungerGames|2026-03-13')

ok, _ = app._review_compare('twitter_handle', 'TheHungerGames|2026-03-13',
                            'TheHungerGames|2026-03-13', title=TITLE + ' - DAR',
                            cat='Movies')
check("correct window passes", ok, True)

ok, sugg = app._review_compare('facebook_page',
                               'http://www.facebook.com/TheHungerGamesMovie|2026-03-14',
                               'http://www.facebook.com/TheHungerGamesMovie|2026-03-13',
                               title=TITLE + ' - DAR', cat='Movies')
check("wrong window date is flagged (facebook)", ok, False)
check("facebook suggestion fixes only the date", sugg,
      'http://www.facebook.com/TheHungerGamesMovie|2026-03-13')

ok, sugg = app._review_compare('instagram_user', 'curatedhandle',
                               'thehungergames|2026-03-13',
                               title=TITLE + ' - DAR', cat='Movies')
check("curated instagram handle is kept, date added", sugg,
      'curatedhandle|2026-03-13')

ok, _ = app._review_compare('twitter_handle', 'SomeShow', 'SomeShow',
                            title='Some Show - DAR', cat='TV Shows')
check("no expected window -> rule stays out of the way", ok, True)

print("\n-- kill switch --")
app.ATTRIBUTION_WINDOW_ENABLED = False
off = app.create_row(TITLE + ' - DAR', True, 'Lionsgate', dict(META))
check("ATTRIBUTION_WINDOW=0 disables the stamp", off['twitter_handle'],
      'TheHungerGames')
check("and raises no review flag", bool(off.get('_needs_review')), False)
app.ATTRIBUTION_WINDOW_ENABLED = True

print()
if FAIL:
    print("FAILED (%d):" % len(FAIL))
    for f in FAIL:
        print("  x  " + f)
    sys.exit(1)
print("All attribution-window checks passed.")
