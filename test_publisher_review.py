"""Manual-file review of PUBLISHER rows (title_category 'Publishers').

Before this fix a Publisher row was routed into the General brand schema
(because 'Publishers' is one of the 49 General master categories), so the
review never checked it against the Publishers 40-column
BrandDefinitionReport logic: the wrong brand set was suggested
('Pristine DAR Brands' instead of 'LF // Publishing\nPristine DAR Brands')
and publisher columns such as twitter_search_terms were never checked at all.
With the titleforge extension unavailable the same row was reviewed as a MOVIE.
"""
import io
import unittest

import pandas as pd

import app


def _review(rows):
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    _, summary = app.build_review((buf.getvalue(), 'pub.xlsx'), auto_fetch=False)
    return summary


def _findings(rows):
    """Re-run the review capturing findings (via the Findings sheet)."""
    import openpyxl
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    data, _ = app.build_review((buf.getvalue(), 'pub.xlsx'), auto_fetch=False)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb['Findings']
    out = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        if r[0] is None:
            continue
        out.append(dict(row=r[0], title=r[1], column=r[2], status=r[3],
                        current=r[4], suggested=r[5]))
    return out


GOOD = dict(title='Wired - DAR', title_category='Publishers',
            title_sub_category='Publication Type - Magazine',
            companies='Pristine Brand',
            brand_set='LF // Publishing\nPristine DAR Brands',
            network='Conde Nast', twitter_handle='wired',
            instagram_user='wired',
            twitter_search_terms='@wired|DAR|DAR\n#wired|DAR|DAR',
            active='TRUE', genre='', primary_genre='', imdb_id='',
            rottentomatoes='', metacritic='')

BAD = dict(title='iMore - DAR', title_category='Publishers',
           title_sub_category='', companies='',
           brand_set='Competitive View', network='',
           twitter_handle='imore', instagram_user='IMORE',
           twitter_search_terms='', active='TRUE', genre='Tech',
           primary_genre='', imdb_id='', rottentomatoes='', metacritic='')


class PublisherCategoryDetection(unittest.TestCase):
    def test_publishers_is_not_a_general_brand(self):
        self.assertIsNone(app._tfx_schema_for_row(dict(GOOD), 'Publishers'))

    def test_publisher_category_variants(self):
        for cat in ('Publishers', 'Publisher', 'publishing'):
            self.assertTrue(app._cat_is_publisher(cat), cat)

    def test_video_game_publishers_is_not_a_publishing_brand(self):
        self.assertFalse(app._cat_is_publisher('Video Game Publishers'))

    def test_blank_category_detected_from_row_content(self):
        lower = {'brand_set': 'brand_set', 'title_sub_category': 'title_sub_category'}
        self.assertTrue(app._row_is_publisher(
            {'brand_set': 'LF // Publishing\nPristine DAR Brands'}, '', lower))
        self.assertTrue(app._row_is_publisher(
            {'title_sub_category': 'Publication Type - Magazine'}, '', lower))
        self.assertFalse(app._row_is_publisher({'brand_set': 'Competitive View'},
                                               '', lower))


class PublisherReviewFindings(unittest.TestCase):
    def test_clean_publisher_row_has_no_findings(self):
        s = _review([GOOD])
        self.assertEqual(s['rows'], 1)
        self.assertEqual(s['gaps'] + s['mismatches'], 0)

    def test_publisher_columns_are_actually_checked(self):
        """The publisher schema checks far more cells than the General rules."""
        self.assertGreaterEqual(_review([GOOD])['cells_checked'], 10)

    def test_bad_publisher_row_is_flagged(self):
        f = {d['column']: d for d in _findings([BAD])}
        self.assertIn('brand_set', f)
        self.assertIn('LF // Publishing', f['brand_set']['suggested'])
        self.assertIn('Pristine DAR Brands', f['brand_set']['suggested'])
        # nothing already in the file is dropped by the suggestion
        self.assertIn('Competitive View', f['brand_set']['suggested'])
        self.assertEqual(f['companies']['status'], 'Gap')
        self.assertEqual(f['companies']['suggested'], 'Pristine Brand')
        self.assertEqual(f['twitter_search_terms']['status'], 'Gap')
        self.assertIn('|DAR|DAR', f['twitter_search_terms']['suggested'])

    def test_search_terms_suggestion_uses_the_rows_own_handle(self):
        f = {d['column']: d for d in _findings([BAD])}
        self.assertIn('@imore|DAR|DAR', f['twitter_search_terms']['suggested'])

    def test_ratings_and_genre_are_not_flagged_for_publishers(self):
        """Publishing brands are not rated titles: genre / RT / IMDb /
        Metacritic stay blank and must never be flagged as mandatory."""
        cols = {d['column'] for d in _findings([GOOD, BAD])}
        for c in ('genre', 'primary_genre', 'imdb_id', 'rottentomatoes',
                  'metacritic'):
            self.assertNotIn(c, cols, c)

    def test_missing_category_is_backfilled_as_a_gap(self):
        # blank category, but the LF // Publishing brand set identifies the row
        row = dict(GOOD, title_category='')
        f = {d['column']: d for d in _findings([row])}
        self.assertEqual(f['title_category']['suggested'], 'Publishers')
        self.assertEqual(f['title_category']['status'], 'Gap')

    def test_publisher_row_is_not_reviewed_as_a_movie(self):
        """Even with the titleforge extension unavailable (TFX_OK False) a
        publisher row keeps publisher handling instead of movie handling."""
        orig = app.TFX_OK
        app.TFX_OK = False
        try:
            f = {d['column']: d for d in _findings([BAD])}
        finally:
            app.TFX_OK = orig
        # the Movies/TV mandatory-column rule must not fire on a publisher
        self.assertNotIn('released_on', f)
        self.assertNotIn('genre', f)
        self.assertIn('brand_set', f)
        self.assertIn('LF // Publishing', f['brand_set']['suggested'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
