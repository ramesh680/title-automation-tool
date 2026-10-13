"""The Movies review rules apply to EVERY row whose title_category is Movies
(any network / brand, base and DAR rows), and to no other category."""
import io
import unittest
import pandas as pd
import app

COLS = ['title', 'title_category', 'title_sub_category', 'released_on', 'network',
        'companies', 'twitter_handle', 'instagram_user', 'metacritic',
        'twitter_search_term_keywords']


def mrow(title, cat, network, **kw):
    r = dict(title=title, title_category=cat, title_sub_category='Release - Limited\nStudio - Independent',
             released_on='2026-10-09', network=network, companies='Unknown',
             twitter_handle='', instagram_user='', metacritic='',
             twitter_search_term_keywords='("%s") ("%s" OR "2026" OR Film)|Operations - '
             'Core Title|Operations - Core Title' % (title.replace(' - DAR', ''), network))
    r.update(kw)
    return r


def review(rows):
    buf = io.BytesIO()
    pd.DataFrame(rows, columns=COLS).to_excel(buf, sheet_name='BrandIngest', index=False)
    buf.seek(0)
    buf.filename = 'in.xlsx'
    xb, _ = app.build_review(buf, auto_fetch=False)
    f = pd.read_excel(io.BytesIO(xb), sheet_name='Findings').fillna('')
    return {(r.Title, r.Column, r.Type) for r in f.itertuples()}


class AllMoviesBrands(unittest.TestCase):
    def test_same_rule_fires_for_every_movies_brand(self):
        rows = [
            mrow('Alpha Story', 'Movies', 'Neon', twitter_handle='https://x.com/AlphaStoryMovie'),
            mrow('Alpha Story - DAR', 'Movies', 'Neon', twitter_handle='https://x.com/AlphaStoryMovie'),
            mrow('Beta Night', 'Movie', 'A24', twitter_handle='@BetaNightFilm'),
            mrow('Gamma Road', 'Films', 'Lionsgate', twitter_handle='http://twitter.com/GammaRoad'),
            mrow('Delta Hour', 'movies', 'Bleecker Street',
                 metacritic='http://www.metacritic.com/tv/delta-hour/'),
        ]
        got = review(rows)
        for t in ('Alpha Story', 'Alpha Story - DAR', 'Beta Night', 'Gamma Road'):
            self.assertIn((t, 'twitter_handle', 'Mismatch'), got)
        self.assertIn(('Delta Hour', 'metacritic', 'Mismatch'), got)

    def test_company_account_flagged_for_any_distributor(self):
        rows = [mrow('Echo Lake', 'Movies', 'Magnolia Pictures',
                     instagram_user='magnoliapictures'),
                mrow('Foxtrot', 'Movies', 'IFC Films', instagram_user='indiestudiomedia')]
        got = review(rows)
        self.assertIn(('Echo Lake', 'instagram_user', 'Mismatch'), got)
        self.assertIn(('Foxtrot', 'instagram_user', 'Mismatch'), got)

    def test_other_categories_untouched(self):
        rows = [mrow('Some Show', 'TV Shows', 'HBO', twitter_handle='https://x.com/SomeShow')]
        got = review(rows)
        self.assertNotIn(('Some Show', 'twitter_handle', 'Mismatch'), got)

    def test_category_matcher(self):
        for c in ('Movies', 'movies', ' Movie ', 'Film', 'FILMS'):
            self.assertTrue(app._is_movies_cat(c), c)
        for c in ('TV Shows', 'TV Movie', 'Talent', 'Video Game', '', 'Movie Theaters'):
            self.assertFalse(app._is_movies_cat(c), c)


if __name__ == '__main__':
    unittest.main()
