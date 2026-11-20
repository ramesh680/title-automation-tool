"""Generator audit, group 1 (Nov 2026): the Validator must not fail the
Generator's own TV / Video Game output, Sports & General base rows must not be
left blank, and the templates' 2-part search terms ('#x|TV Ops') are valid."""
import io
import unittest

import openpyxl

import app as A
import validator as V
import titleforge_ingest_ext as TFX


def _validate(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    cols = sorted({k for r in rows for k in r})
    ws.append(cols)
    for r in rows:
        ws.append([r.get(c, '') for c in cols])
    b = io.BytesIO()
    wb.save(b)
    b.seek(0)
    b.filename = 'x.xlsx'
    _, summ = V.validate_workbook(b)
    return {(f.get('column'), f.get('severity')) for f in summ.get('failures', [])}


class MetacriticByType(unittest.TestCase):
    def test_game_page_ok_on_game(self):
        self.assertNotIn(('metacritic', V.SEV_FAIL), _validate([dict(
            title='Star Quest', title_category='Video Game',
            metacritic='https://www.metacritic.com/game/star-quest')]))

    def test_movie_page_on_game_fails(self):
        self.assertIn(('metacritic', V.SEV_FAIL), _validate([dict(
            title='Star Quest', title_category='Video Game',
            metacritic='https://www.metacritic.com/movie/star-quest')]))

    def test_game_page_on_movie_fails(self):
        self.assertIn(('metacritic', V.SEV_FAIL), _validate([dict(
            title='Skyfall', title_category='Movies',
            metacritic='https://www.metacritic.com/game/skyfall')]))


class SearchTermFormats(unittest.TestCase):
    def test_two_part_template_format_ok(self):
        self.assertNotIn(('twitter_search_terms', V.SEV_FAIL), _validate([dict(
            title='X', title_category='Video Game',
            twitter_search_terms='#X|Operations - Core Title')]))
        # the TV template's own format ('#x|TV Ops') is the upload format
        self.assertNotIn(('twitter_search_terms', V.SEV_FAIL), _validate([dict(
            title='X', title_category='TV Shows', twitter_search_terms='#X|TV Ops')]))

    def test_three_part_still_ok_and_garbage_fails(self):
        self.assertNotIn(('twitter_search_terms', V.SEV_FAIL), _validate([dict(
            title='X', title_category='Movies', twitter_search_terms='@x|TV Ops|TV Ops')]))
        self.assertIn(('twitter_search_terms', V.SEV_FAIL), _validate([dict(
            title='X', title_category='Movies', twitter_search_terms='#x')]))

    def test_dar_two_part_game(self):
        self.assertNotIn(('twitter_search_terms', V.SEV_WARN), _validate([dict(
            title='X - DAR', title_category='Video Game', twitter_search_terms='#X|DAR')]))


class TvNetworkList(unittest.TestCase):
    def test_tv_network_from_tv_template_ok(self):
        self.assertNotIn(('network', V.SEV_WARN), _validate([dict(
            title='X', title_category='TV Shows', network='HBO')]))

    def test_unknown_tv_network_warns(self):
        self.assertIn(('network', V.SEV_WARN), _validate([dict(
            title='X', title_category='TV Shows', network='Not A Real Channel')]))


class BrandBaseRows(unittest.TestCase):
    def test_sports_base_row_defaults(self):
        r = TFX.create_tfx_row('Jaipur Jaguars', 'sports', {}) \
            if hasattr(TFX, 'create_tfx_row') else A.create_tfx_row('Jaipur Jaguars', 'sports', {})
        self.assertEqual(r['brand_set'], 'Competitive View')
        self.assertEqual(r['companies'], 'Unknown')

    def test_sports_dar_unchanged(self):
        r = A.create_tfx_row('Jaipur Jaguars - DAR', 'sports', {})
        self.assertEqual(r['brand_set'], 'LF // Professional Sports Teams')
        self.assertEqual(r['companies'], 'Pristine Brand')

    def test_general_base_company(self):
        self.assertEqual(A.create_tfx_row('Northwind Air', 'general', {})['companies'], 'Unknown')



if __name__ == '__main__':
    unittest.main()
