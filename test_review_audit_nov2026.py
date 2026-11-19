"""Regression tests for the Review reliability fixes (Nov 2026 audit).

Every case is a column-by-column mutation: a correct ("gold") row is built by
the generator, one cell is broken, and the Review must flag exactly that cell
-- without suggesting a value that would write wrong data into the INGESTED
row. Discovery is mocked so the tests run offline.
"""
import io
import unittest

import openpyxl
import pandas as pd

import app as A


TITLE = 'Skyfall Rising'
MOVIE = {
    'network': 'Warner Bros.', 'released_on': '2026-07-17',
    'genre': 'Action\nAdventure\nSci-Fi', 'release_scale': 'Wide',
    'facebook_page': 'https://www.facebook.com/SkyfallRisingMovie',
    'twitter_handle': 'SkyfallRisingMovie', 'instagram_user': 'skyfallrisingmovie',
    'tiktok_user': 'skyfallrisingmovie',
    'wikipedia_page': 'https://en.wikipedia.org/wiki/Skyfall_Rising_(film)',
    'rottentomatoes': 'https://www.rottentomatoes.com/m/skyfall_rising',
    'imdb_id': 'https://www.imdb.com/title/tt1234567',
    'metacritic': 'https://www.metacritic.com/movie/skyfall-rising',
}
TV = {
    'network': 'HBO', 'released_on': '2026-04-12', 'genre': 'Drama\nThriller',
    'program_type': 'Series', 'original_language': 'en',
    'facebook_page': 'https://www.facebook.com/NightHarborHBO',
    'twitter_handle': 'NightHarborHBO', 'instagram_user': 'nightharborhbo',
    'wikipedia_page': 'https://en.wikipedia.org/wiki/Night_Harbor_(TV_series)',
    'imdb_id': 'https://www.imdb.com/title/tt2222222',
    'rottentomatoes': 'https://www.rottentomatoes.com/tv/night_harbor',
}
GAME = {
    'network': 'Nintendo', 'released_on': '2026-03-05', 'genre': 'Action\nAdventure',
    'developer': 'Nintendo EPD', 'platforms': ['Nintendo Switch 2'],
    'metacritic': 'https://www.metacritic.com/game/star-quest',
}
TALENT = {'gender': 'Gender - Woman', 'occupations': ['actress'],
          'instagram_user': 'janedoe',
          'imdb_id': 'https://www.imdb.com/name/nm1111111'}


def _fake(meta):
    return lambda *a, **k: dict(meta)


class _Base(unittest.TestCase):
    PATCH = ('fetch_metadata', 'fetch_metadata_by_tt', 'fetch_game', 'fetch_person')

    def setUp(self):
        self._saved = {n: getattr(A, n) for n in self.PATCH}

    def tearDown(self):
        for n, f in self._saved.items():
            setattr(A, n, f)

    def gold(self, kind, titles, meta):
        cols = {'movie': A.COLUMNS, 'tv': A.TV_COLUMNS,
                'game': A.GAME_COLUMNS, 'talent': A.TALENT_COLUMNS}[kind]
        rows = []
        for t in titles:
            r = A.make_row(t, kind == 'movie', meta.get('network', ''), dict(meta),
                           talent=kind == 'talent', game=kind == 'game')
            rows.append({c: r.get(c, '') for c in cols})
        return pd.DataFrame(rows, columns=cols)

    def review(self, df, meta, auto=True):
        for n in self.PATCH:
            setattr(A, n, _fake(meta))
        b = io.BytesIO()
        df.to_excel(b, index=False)
        frames = A._read_upload_frames((b.getvalue(), 'x.xlsx'))
        return A._review_frame(frames[0][1], auto_fetch=auto)['findings']

    def find(self, findings, col, row=2):
        return [f for f in findings if f['row'] == row and str(f['column']).lower() == col]


class GoldFilesAreClean(_Base):
    def test_every_kind_on_and_off(self):
        for kind, titles, meta in (('movie', [TITLE, TITLE + ' - DAR'], MOVIE),
                                   ('tv', ['Night Harbor', 'Night Harbor - DAR'], TV),
                                   ('game', ['Star Quest', 'Star Quest - DAR'], GAME),
                                   ('talent', ['Jane Doe - DAR'], TALENT)):
            for auto in (True, False):
                with self.subTest(kind=kind, auto=auto):
                    self.assertEqual(self.review(self.gold(kind, titles, meta), meta, auto), [])

    def test_url_managers_not_flagged_when_discovery_returns_nothing(self):
        # used to suggest deleting the facebook/instagram/tiktok/twitter lines
        df = self.gold('movie', [TITLE], MOVIE)
        self.assertEqual(self.review(df, {}, auto=True), [])


class MovieColumns(_Base):
    def mutate(self, col, val, auto=True, row=0, meta=MOVIE):
        df = self.gold('movie', [TITLE, TITLE + ' - DAR'], meta)
        df[col] = df[col].astype(object)
        df.at[row, col] = val
        return self.review(df, meta, auto)

    def test_wrong_release_year_raised_on_released_on_not_keywords(self):
        f = self.mutate('released_on', '2019-07-17')
        rel = self.find(f, 'released_on')
        self.assertTrue(rel and rel[0]['status'] in ('Mismatch', 'Verify'))
        # the keyword year must NOT be 'corrected' to the wrong file year
        self.assertFalse(any('"2019"' in str(x['suggested']) for x in f))

    def test_bad_date_format(self):
        f = self.find(self.mutate('released_on', '17/07/2026'), 'released_on')
        self.assertEqual((f[0]['status'], f[0]['suggested']), ('Mismatch', '2026-07-17'))

    def test_genre_without_overlap(self):
        f = self.mutate('genre', 'Horror')
        self.assertEqual(self.find(f, 'genre')[0]['status'], 'Mismatch')
        # primary_genre advice must come from the row's own genres
        self.assertEqual(self.find(f, 'primary_genre')[0]['suggested'], 'Horror')

    def test_genre_outside_imdb_vocabulary(self):
        f = self.mutate('genre', 'Action\nHorror Fiction', auto=False)
        self.assertEqual(self.find(f, 'genre')[0]['status'], 'Mismatch')

    def test_wrong_company_on_base_row(self):
        self.assertEqual(self.find(self.mutate('companies', 'Universal Pictures'),
                                   'companies')[0]['status'], 'Mismatch')

    def test_unknown_company_still_allowed(self):
        self.assertEqual(self.find(self.mutate('companies', 'Unknown'), 'companies'), [])

    def test_blank_network_is_gap(self):
        f = self.find(self.mutate('network', ''), 'network')
        self.assertEqual((f[0]['status'], f[0]['suggested']), ('Gap', 'Warner Bros.'))

    def test_blank_network_offline_uses_keyword_clause(self):
        f = self.find(self.mutate('network', '', auto=False), 'network')
        self.assertEqual((f[0]['status'], f[0]['suggested']), ('Gap', 'Warner Bros.'))

    def test_different_facebook_and_instagram(self):
        f = self.mutate('facebook_page', 'https://www.facebook.com/SomeOtherFilm')
        self.assertTrue(self.find(f, 'facebook_page'))
        f = self.mutate('instagram_user', 'someotherfilm')
        self.assertTrue(self.find(f, 'instagram_user'))

    def test_profile_php_facebook_offline(self):
        f = self.mutate('facebook_page', 'https://www.facebook.com/profile.php?id=1', auto=False)
        self.assertEqual(self.find(f, 'facebook_page')[0]['status'], 'Mismatch')

    def test_youtube_missing_network_channel(self):
        f = self.find(self.mutate('youtube_channel_username',
                                  'http://www.youtube.com/@randomfan'), 'youtube_channel_username')
        self.assertIn('WarnerBrosPictures', f[0]['suggested'])

    def test_dar_search_terms_with_foreign_handle(self):
        f = self.find(self.mutate('twitter_search_terms', '@wronghandle|DAR|DAR', row=1),
                      'twitter_search_terms', row=3)
        self.assertEqual(f[0]['status'], 'Mismatch')
        self.assertNotIn('@wronghandle', f[0]['suggested'])

    def test_blank_keywords_is_gap(self):
        self.assertEqual(self.find(self.mutate('twitter_search_term_keywords', ''),
                                   'twitter_search_term_keywords')[0]['status'], 'Gap')

    def test_hashtag_keyword_is_mismatch(self):
        self.assertEqual(self.find(self.mutate('twitter_search_term_keywords', '#skyfallrising'),
                                   'twitter_search_term_keywords')[0]['status'], 'Mismatch')

    def test_offline_wikipedia_slug_check(self):
        f = self.mutate('wikipedia_page', 'https://en.wikipedia.org/wiki/Something_Else', auto=False)
        self.assertEqual(self.find(f, 'wikipedia_page')[0]['status'], 'Mismatch')

    def test_offline_url_shapes(self):
        f = self.mutate('imdb_id', 'tt1234567', auto=False)
        self.assertEqual(self.find(f, 'imdb_id')[0]['status'], 'Mismatch')

    def test_category_and_active(self):
        self.assertEqual(self.find(self.mutate('title_category', 'Movie'),
                                   'title_category')[0]['suggested'], 'Movies')
        self.assertEqual(self.find(self.mutate('active', 'maybe'), 'active')[0]['status'],
                         'Mismatch')


class DarBrandSets(_Base):
    def test_dar_suggestion_drops_competitive_view(self):
        for kind, titles, meta in (('tv', ['Night Harbor', 'Night Harbor - DAR'], TV),
                                   ('game', ['Star Quest', 'Star Quest - DAR'], GAME)):
            with self.subTest(kind=kind):
                df = self.gold(kind, titles, meta)
                df.at[1, 'brand_set'] = 'Competitive View'
                f = self.find(self.review(df, meta), 'brand_set', row=3)
                self.assertEqual(f[0]['status'], 'Mismatch')
                self.assertNotIn('Competitive View', f[0]['suggested'])
                self.assertIn('Pristine DAR Brands', f[0]['suggested'])

    def test_talent_dar_with_base_set(self):
        df = self.gold('talent', ['Jane Doe - DAR'], TALENT)
        df.at[0, 'brand_set'] = 'LF // Talent\nCompetitive View'
        f = self.find(self.review(df, TALENT), 'brand_set')
        self.assertEqual(f[0]['suggested'], 'LF // Talent\nPristine DAR Brands')


class TvColumns(_Base):
    def test_wrong_network_offline_not_propagated(self):
        df = self.gold('tv', ['Night Harbor'], TV)
        df.at[0, 'network'] = 'Netflix'
        f = self.review(df, TV, auto=False)
        self.assertEqual([x['column'] for x in f], ['network'])
        self.assertEqual(f[0]['status'], 'Verify')

    def test_program_type_dropdown(self):
        df = self.gold('tv', ['Night Harbor'], TV)
        df.at[0, 'title_sub_category'] = df.at[0, 'title_sub_category'].replace('Series', 'Scripted')
        f = self.find(self.review(df, TV, auto=False), 'title_sub_category')
        self.assertIn('Program Type - Series', f[0]['suggested'])

    def test_movie_rt_page_on_tv_row_offline(self):
        df = self.gold('tv', ['Night Harbor'], TV)
        df.at[0, 'rottentomatoes'] = 'https://www.rottentomatoes.com/m/night_harbor'
        self.assertTrue(self.find(self.review(df, TV, auto=False), 'rottentomatoes'))


class GameAndTalent(_Base):
    def test_game_rules(self):
        df = self.gold('game', ['Star Quest'], GAME)
        df['rottentomatoes'] = df['rottentomatoes'].astype(object)
        df.at[0, 'rottentomatoes'] = 'https://www.rottentomatoes.com/m/x'
        df.at[0, 'network'] = ''
        df.at[0, 'genre'] = ''
        df.at[0, 'released_on'] = '2020-03-05'
        f = self.review(df, GAME)
        for col in ('rottentomatoes', 'network', 'genre', 'released_on'):
            with self.subTest(col=col):
                self.assertTrue(self.find(f, col))

    def test_game_keywords_without_developer_not_rewritten(self):
        df = self.gold('game', ['Star Quest'], GAME)
        df.at[0, 'title_sub_category'] = ''
        f = self.review(df, {}, auto=False)
        self.assertFalse(self.find(f, 'twitter_search_term_keywords'))

    def test_game_keywords_no_duplicate_video_game(self):
        r = A.make_row('Star Quest', False, '', {'network': 'Nintendo'}, game=True)
        self.assertEqual(r['twitter_search_term_keywords'].count('"Video Game"'), 1)

    def test_talent_rules_offline(self):
        df = self.gold('talent', ['Jane Doe - DAR'], TALENT)
        df.at[0, 'imdb_id'] = 'https://www.imdb.com/title/tt1111111'
        df['genre'] = df['genre'].astype(object)
        df.at[0, 'genre'] = 'Drama'
        f = self.review(df, TALENT, auto=False)
        self.assertTrue(self.find(f, 'imdb_id'))
        self.assertTrue(self.find(f, 'genre'))

    def test_actress_occupation(self):
        r = A.make_row('Jane Doe - DAR', False, '', dict(TALENT), talent=True)
        self.assertIn('Talent Type - Actress', r['title_sub_category'])


class Workbook(_Base):
    def test_reviewed_row_and_unverified_count(self):
        A.fetch_metadata = A.fetch_metadata_by_tt = _fake(MOVIE)
        df = self.gold('movie', [TITLE, TITLE + ' - DAR', 'Third Film'], MOVIE)
        df.at[2, 'imdb_id'] = 'https://www.imdb.com/title/tt7654321'
        b = io.BytesIO()
        df.to_excel(b, index=False)
        xl, summ = A.build_review((b.getvalue(), 'x.xlsx'), auto_fetch=False)
        self.assertIn('unverified', summ)
        self.assertGreater(summ['unverified'], 0)
        wb = openpyxl.load_workbook(io.BytesIO(xl))
        hdr = [c.value for c in wb['Findings'][1]]
        self.assertEqual(hdr[:3], ['Row', 'Title', 'Column'])   # positions unchanged
        rr = hdr.index('Reviewed Row')
        rev = wb['Reviewed']
        thdr = [c.value for c in rev[1]]
        for r in wb['Findings'].iter_rows(min_row=2, values_only=True):
            self.assertEqual(rev.cell(r[rr], thdr.index('title') + 1).value, r[1])
            self.assertEqual(rev.cell(r[rr], 1).value, 'INGESTED')

    def test_xls_rejected_clearly(self):
        with self.assertRaises(ValueError):
            A._read_upload_frames((b'abc', 'old.xls'))


if __name__ == '__main__':
    unittest.main()
