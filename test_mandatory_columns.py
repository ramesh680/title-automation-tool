"""Rule 1 (Aug 2026): for the title categories 'Movies' and 'TV Shows', the
columns genre, primary_genre and released_on are MANDATORY -- they must never be
blank. Enforced across all three surfaces:

  * Review    -> app._review_compare flags a blank as a finding even when
                 auto-discovery found nothing to compare against.
  * Validator -> validator.validate_workbook fails a blank mandatory cell.
  * Generator -> app.create_row marks a row 'Needs Review' when it cannot fill a
                 mandatory column.

A non-Movies/TV category (e.g. Talent) is NOT subject to the rule.
"""
import io
import unittest

from openpyxl import Workbook

from app import _review_compare, create_row
from validator import validate_workbook


def ok(col, cur, exp, **kw):
    return _review_compare(col, cur, exp, **kw)[0]


class ReviewMandatory(unittest.TestCase):
    def test_blank_genre_flagged_for_movies_even_without_discovery(self):
        # expected empty (discovery found nothing) -> must STILL flag the blank
        self.assertFalse(ok('genre', '', '', cat='Movies'))

    def test_blank_primary_genre_flagged_for_tv(self):
        self.assertFalse(ok('primary_genre', '', '', cat='TV Shows'))

    def test_blank_released_on_flagged_for_movies(self):
        self.assertFalse(ok('released_on', '', '', cat='Movies'))

    def test_present_genre_not_flagged(self):
        self.assertTrue(ok('genre', 'Drama', 'Drama', cat='Movies'))

    def test_blank_genre_not_flagged_for_talent(self):
        # rule is Movies/TV only; Talent genre blank is not a mandatory failure
        self.assertTrue(ok('genre', '', '', cat='Talent'))


class ValidatorMandatory(unittest.TestCase):
    HEADERS = ['title', 'title_category', 'genre', 'primary_genre', 'released_on']

    def _run(self, rows):
        wb = Workbook()
        ws = wb.active
        ws.append(self.HEADERS)
        for r in rows:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        buf.filename = 'test.xlsx'
        _, summary = validate_workbook(buf)
        return summary

    def _fails_for(self, summary, column):
        return [f for f in summary['failures']
                if f['column'] == column and f['severity'] == 'fail']

    def test_blank_mandatory_columns_fail_for_movies(self):
        s = self._run([['Some Movie', 'Movies', '', '', '']])
        self.assertTrue(self._fails_for(s, 'genre'))
        self.assertTrue(self._fails_for(s, 'primary_genre'))
        self.assertTrue(self._fails_for(s, 'released_on'))

    def test_filled_mandatory_columns_pass(self):
        s = self._run([['Some Movie', 'Movies', 'Drama', 'Drama', '2024-01-01']])
        self.assertFalse(self._fails_for(s, 'genre'))
        self.assertFalse(self._fails_for(s, 'primary_genre'))
        self.assertFalse(self._fails_for(s, 'released_on'))

    def test_talent_blank_genre_not_failed(self):
        s = self._run([['Some Person', 'Talent', '', '', '']])
        self.assertFalse(self._fails_for(s, 'genre'))
        self.assertFalse(self._fails_for(s, 'primary_genre'))


class GeneratorMandatory(unittest.TestCase):
    def test_movie_missing_genre_flagged_needs_review(self):
        # no metadata -> genre / primary_genre / released_on come out blank
        row = create_row('Totally Unknown Film - DAR', True, '', {})
        self.assertTrue(row.get('_needs_review'))
        self.assertIn('genre', row.get('_review_reason', ''))

    def test_movie_with_all_mandatory_not_flagged(self):
        meta = {'genre': 'Drama', 'primary_genre': 'Drama', 'released_on': '2024-01-01'}
        row = create_row('Some Film - DAR', True, 'A24', meta)
        self.assertFalse(row.get('_needs_review'))


if __name__ == '__main__':
    unittest.main()
