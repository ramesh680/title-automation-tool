"""Manual-file review of PUBLISHER rows (the 41-column BrandDefinitionReport).

Two things were wrong before these tests existed:

1. A Publisher row was routed into the General brand schema, because
   'Publishers' is one of the 49 General master categories -- so the review
   never applied any publisher logic (and with the titleforge extension
   unavailable the row was reviewed as a MOVIE instead).

2. Even routed correctly, a diff against the generated row finds almost
   nothing: with auto-discovery off the expected value of most columns is
   blank, and a blank expectation passes everything. Reviewing a real
   180-brand publishing file produced 2 findings, both false positives.

So the Publishers schema is now reviewed against RULES (every rule below holds
for 100% of the production export), independent of auto-discovery.
"""
import io
import unittest

import pandas as pd

import app


# a clean publisher row, shaped exactly like the production export
GOOD = dict(
    record_type='INGESTED', brand_id=31999, title='Barstool Sports - DAR',
    title_created_date='2016-05-04', title_category='Publishers',
    title_sub_category='Publication Type - Sports',
    genre='', primary_genre='', iso_mic='', stock_exchange='', ticker_symbol='',
    companies='Pristine Brand',
    brand_set='LF // Publishing\nPristine DAR Brands',
    composite_brand_set='', active='TRUE', released_on='',
    domestic_opening_weekend_box_office='', domestic_opening_weekend_screens='',
    domestic_opening_weekend_rank='', street_date='', network='Barstool Sports',
    facebook_page='http://www.facebook.com/barstoolsports',
    facebook_verified='TRUE|http://www.facebook.com/barstoolsports',
    twitter_handle='barstoolsports',
    twitter_verified='TRUE|http://twitter.com/barstoolsports',
    instagram_user='barstoolsports',
    youtube_channel_username='http://www.youtube.com/@BarstoolSportsTV',
    youtube_channel_company='', tiktok_user='barstoolsports',
    linkedin_page='barstool-sports|DAR', threads_page='',
    pinterest_user_username='', pinterest_board='',
    wikipedia_page='http://en.wikipedia.org/wiki/Barstool_Sports',
    rottentomatoes='', imdb_id='', metacritic='',
    twitter_search_terms='@barstoolsports|DAR|DAR\n#barstoolsports|DAR|DAR',
    instagram_business_hashtags='', twitter_search_term_keywords='',
    last_reviewed='2026-08-21')


def _findings(rows):
    """Run the review over rows and return (findings, summary)."""
    import openpyxl
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False)
    data, summary = app.build_review((buf.getvalue(), 'pub.xlsx'),
                                     auto_fetch=False)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    out = []
    for r in wb['Findings'].iter_rows(min_row=2, values_only=True):
        if r[0] is None:
            continue
        out.append(dict(row=r[0], title=r[1], column=r[2], status=r[3],
                        current=r[4], suggested=r[5]))
    return out, summary


def _one(**overrides):
    """Findings for a single row, keyed by column."""
    f, _ = _findings([dict(GOOD, **overrides)])
    return {d['column']: d for d in f}


class Detection(unittest.TestCase):
    def test_publishers_is_not_reviewed_as_a_general_brand(self):
        self.assertIsNone(app._tfx_schema_for_row(dict(GOOD), 'Publishers'))

    def test_category_variants(self):
        for cat in ('Publishers', 'Publisher', 'publishing'):
            self.assertTrue(app._cat_is_publisher(cat), cat)

    def test_video_game_publishers_stays_a_video_game(self):
        self.assertFalse(app._cat_is_publisher('Video Game Publishers'))
        row = dict(GOOD, title_category='Video Game Publishers')
        self.assertFalse(app._row_is_publisher(row, 'Video Game Publishers',
                                               {c: c for c in row}))

    def test_publication_rows_under_another_category(self):
        """In the export publications also sit under Media / TV Network /
        Music and Entertainment; they are still publisher rows."""
        lower = {c: c for c in GOOD}
        for cat in ('Media', 'TV Network', 'Music and Entertainment', ''):
            row = dict(GOOD, title_category=cat)
            self.assertTrue(app._row_is_publisher(row, cat, lower), cat)

    def test_a_plain_brand_row_is_not_a_publisher(self):
        row = {'brand_set': 'Competitive View', 'title_sub_category': ''}
        self.assertFalse(app._row_is_publisher(
            row, '', {'brand_set': 'brand_set',
                      'title_sub_category': 'title_sub_category'}))


class CleanRowIsQuiet(unittest.TestCase):
    def test_no_findings_on_a_clean_row(self):
        f, s = _findings([GOOD])
        self.assertEqual(f, [], f)
        self.assertEqual(s['gaps'] + s['mismatches'], 0)

    def test_curated_extras_are_never_flagged(self):
        """Extra brand sets, extra search-term labels, multi-line accounts,
        a '|label' YouTube suffix and a blank network are all valid."""
        f = _one(brand_set='2021 CFB Sponsors\nLF // Publishing\n'
                           'Pristine DAR Brands\nSports Publishers',
                 network='',
                 title_sub_category='Publication Type - Entertainment\n'
                                    'Publication Type - Film & TV',
                 youtube_channel_username='http://www.youtube.com/user/CNN|cnn '
                                          'breaking news',
                 twitter_search_terms='@barstoolsports|Operations - Core Title|'
                                      'Operations - Core Title\n'
                                      '@barstoolsports|DAR|DAR\n'
                                      '#barstoolsports|DAR|DAR')
        self.assertEqual(f, {}, f)

    def test_dar_hashtag_keyword_is_not_flagged(self):
        """The Movies/TV rule 'a #hashtag is never a keyword' must not fire on
        a publisher row -- '#term|DAR|DAR' is the publisher convention."""
        f = _one(twitter_search_term_keywords='#barstoolsports|DAR|DAR')
        self.assertEqual(f, {}, f)


class Rules(unittest.TestCase):
    def test_title_must_be_a_dar_row(self):
        f = _one(title='Barstool Sports')
        self.assertEqual(f['title']['suggested'], 'Barstool Sports - DAR')

    def test_blank_category_is_backfilled(self):
        f = _one(title_category='')
        self.assertEqual(f['title_category']['status'], 'Gap')
        self.assertEqual(f['title_category']['suggested'], 'Publishers')

    def test_unapproved_category_is_flagged(self):
        f = _one(title_category='Magazines')
        self.assertEqual(f['title_category']['suggested'], 'Publishers')

    def test_sub_category_must_use_the_publication_type_dropdown(self):
        f = _one(title_sub_category='Sports')          # missing the prefix
        self.assertIn('Publication Type - Sports',
                      f['title_sub_category']['suggested'])
        f = _one(title_sub_category='Publication Type - Tecch')   # not a value
        self.assertIn('title_sub_category', f)
        f = _one(title_sub_category='')
        self.assertEqual(f['title_sub_category']['status'], 'Gap')

    def test_companies_must_be_pristine_brand(self):
        self.assertEqual(_one(companies='Unknown')['companies']['suggested'],
                         'Pristine Brand')

    def test_required_brand_sets_are_merged_not_replaced(self):
        f = _one(brand_set='Competitive View')
        s = f['brand_set']['suggested']
        self.assertIn('LF // Publishing', s)
        self.assertIn('Pristine DAR Brands', s)
        self.assertIn('Competitive View', s)          # nothing is dropped

    def test_columns_a_publication_never_carries(self):
        for col, bad in (('genre', 'Tech'), ('primary_genre', 'Tech'),
                         ('released_on', '2020-01-01'), ('imdb_id', 'tt1234567'),
                         ('rottentomatoes', 'http://x'), ('metacritic', 'http://x'),
                         ('domestic_opening_weekend_box_office', '1000'),
                         ('instagram_business_hashtags', '#x')):
            f = _one(**{col: bad})
            self.assertIn(col, f, col)
            self.assertEqual(f[col]['suggested'], app.PUB_LEAVE_BLANK, col)

    def test_handles_must_be_bare(self):
        self.assertEqual(_one(twitter_handle='@Barstool',
                              twitter_verified='')['twitter_handle']['suggested'],
                         'Barstool')
        self.assertEqual(_one(instagram_user='Barstool')['instagram_user']['suggested'],
                         'barstool')
        self.assertEqual(
            _one(tiktok_user='https://tiktok.com/@barstool')['tiktok_user']['suggested'],
            'barstool')

    def test_mixed_case_twitter_and_tiktok_are_fine(self):
        self.assertEqual(_one(tiktok_user='AccuWeather'), {})
        self.assertEqual(
            _one(twitter_handle='BarstoolSports',
                 twitter_verified='TRUE|http://twitter.com/BarstoolSports'), {})

    def test_verified_shape_and_account_match(self):
        f = _one(facebook_verified='TRUE')
        self.assertIn('facebook_verified', f)
        f = _one(twitter_verified='TRUE|http://twitter.com/somebodyelse')
        self.assertEqual(f['twitter_verified']['suggested'],
                         'TRUE|http://twitter.com/barstoolsports')

    def test_verified_fills_an_empty_account_cell(self):
        f = _one(facebook_page='')
        self.assertEqual(f['facebook_page']['status'], 'Gap')
        self.assertEqual(f['facebook_page']['suggested'],
                         'http://www.facebook.com/barstoolsports')

    def test_multi_line_accounts_and_verified_lines_match_as_sets(self):
        f = _one(facebook_page='http://www.facebook.com/albanytimesunion\n'
                               'http://www.facebook.com/timesunion',
                 facebook_verified='TRUE|http://www.facebook.com/timesunion\n'
                                   'TRUE|http://www.facebook.com/albanytimesunion')
        self.assertNotIn('facebook_verified', f)
        self.assertNotIn('facebook_page', f)

    def test_youtube_must_be_a_channel_url(self):
        self.assertEqual(
            _one(youtube_channel_username='barstool'
                 )['youtube_channel_username']['suggested'],
            'http://www.youtube.com/@barstool')

    def test_wikipedia_must_be_an_en_wikipedia_url(self):
        self.assertIn('wikipedia_page', _one(wikipedia_page='barstoolsports.com'))

    def test_linkedin_is_a_slug_with_a_label(self):
        self.assertEqual(
            _one(linkedin_page='http://www.linkedin.com/company/barstool-sports'
                 )['linkedin_page']['suggested'], 'barstool-sports|DAR')
        self.assertEqual(
            _one(linkedin_page='barstool-sports')['linkedin_page']['suggested'],
            'barstool-sports|DAR')
        # a dotted slug is valid -- 'fansided.com|DAR' is a real production value
        self.assertEqual(_one(linkedin_page='fansided.com|DAR'), {})

    def test_pinterest_needs_the_double_dar_label(self):
        self.assertEqual(
            _one(pinterest_user_username='barstool'
                 )['pinterest_user_username']['suggested'], 'barstool|DAR|DAR')
        self.assertEqual(
            _one(pinterest_board='Tech Picks')['pinterest_board']['suggested'],
            'Tech Picks|DAR|DAR')

    def test_search_terms_shape_and_dar_pair(self):
        f = _one(twitter_search_terms='')
        self.assertEqual(f['twitter_search_terms']['status'], 'Gap')
        self.assertIn('|DAR|DAR', f['twitter_search_terms']['suggested'])
        # a handle line without the double label
        f = _one(twitter_search_terms='@barstoolsports|DAR')
        self.assertIn('@barstoolsports|DAR|DAR',
                      f['twitter_search_terms']['suggested'])
        # missing the '#term|DAR|DAR' line
        f = _one(twitter_search_terms='@barstoolsports|DAR|DAR')
        self.assertIn('#barstoolsports|DAR|DAR',
                      f['twitter_search_terms']['suggested'])
        # ... and the suggestion never duplicates a line
        lines = f['twitter_search_terms']['suggested'].split('\n')
        self.assertEqual(len(lines), len(set(lines)))

    def test_keywords_need_the_label_shape(self):
        self.assertEqual(
            _one(twitter_search_term_keywords='barstool sports'
                 )['twitter_search_term_keywords']['suggested'],
            'barstool sports|DAR|DAR')

    def test_no_finding_ever_suggests_the_current_value(self):
        f, _ = _findings([dict(GOOD, linkedin_page='fansided.com|DAR',
                               tiktok_user='AccuWeather')])
        for d in f:
            self.assertNotEqual(str(d['current'] or ''), str(d['suggested'] or ''))


class WithoutTitleforgeExtension(unittest.TestCase):
    """A publisher row must never fall through to the movie branch."""

    def setUp(self):
        self._orig = app.TFX_OK
        app.TFX_OK = False

    def tearDown(self):
        app.TFX_OK = self._orig

    def test_publisher_rules_still_apply(self):
        f = _one(companies='Unknown')
        self.assertEqual(f['companies']['suggested'], 'Pristine Brand')
        # the Movies/TV mandatory-column rule must not fire
        self.assertNotIn('genre', f)
        self.assertNotIn('released_on', f)


class MixedFile(unittest.TestCase):
    def test_a_movie_row_in_the_same_file_keeps_movie_handling(self):
        movie = dict(GOOD, title='Dune: Part Two', title_category='Movies',
                     title_sub_category='', companies='Unknown',
                     brand_set='Competitive View', twitter_search_terms='')
        f, s = _findings([GOOD, movie])
        cols = {(d['row'], d['column']) for d in f}
        # the movie row is flagged for the Movies/TV mandatory columns
        self.assertIn((3, 'genre'), cols)
        self.assertIn((3, 'released_on'), cols)
        # the publisher row stays clean
        self.assertFalse([c for c in cols if c[0] == 2], cols)
        self.assertEqual(s['rows'], 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
