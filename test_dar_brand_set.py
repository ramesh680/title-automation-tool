"""Regression tests for the brand_set / '- DAR' rule.

Rule: a title carrying a '- DAR' suffix is a DAR row and takes
'Pristine DAR Brands'. A title without it is a base row and takes
'Competitive View'. Detection must be tolerant of spacing, letter case and
en/em dashes -- those variants used to be classified as base rows.
"""
import unittest

import app


# spacing / case / dash variants that must all count as DAR
DAR_VARIANTS = [
    "Severance - DAR", "Severance- DAR", "Severance -DAR", "Severance-DAR",
    "Severance  -  DAR", "Severance - Dar", "Severance - dar",
    "Severance \u2013 DAR", "Severance \u2014 DAR", "Severance - DAR ",
]

# titles that must NOT be treated as DAR ('Dark...' must not false-positive)
BASE_TITLES = [
    "Severance", "Severance (2026)", "Dune: Part Two", "The Dark Knight",
    "Darkwing Duck", "Daredevil", "DAR Williams",
]


class DarDetection(unittest.TestCase):
    def test_variants_detected(self):
        for t in DAR_VARIANTS:
            self.assertTrue(app._is_dar_title(t), t)

    def test_base_titles_not_detected(self):
        for t in BASE_TITLES:
            self.assertFalse(app._is_dar_title(t), t)

    def test_strip_matches_detection(self):
        for t in DAR_VARIANTS:
            self.assertEqual(app._strip_dar_suffix(t), "Severance", t)


class BrandSetByRowType(unittest.TestCase):
    def _assert(self, row, want_dar, label):
        bs = row.get('brand_set', '')
        if want_dar:
            self.assertIn('Pristine DAR Brands', bs, label)
            self.assertNotIn('Competitive View', bs, label)
        else:
            self.assertIn('Competitive View', bs, label)
            self.assertNotIn('Pristine DAR Brands', bs, label)

    def test_tv(self):
        for t in DAR_VARIANTS:
            self._assert(app.create_tv_row(t, "Netflix"), True, t)
        for t in BASE_TITLES:
            self._assert(app.create_tv_row(t, "Netflix"), False, t)

    def test_movies(self):
        for t in DAR_VARIANTS:
            self._assert(app.create_row(t, True, "Warner Bros."), True, t)
        for t in BASE_TITLES:
            self._assert(app.create_row(t, True, "Warner Bros."), False, t)

    def test_games(self):
        for t in DAR_VARIANTS:
            self._assert(app.create_game_row(t, {}), True, t)
        for t in BASE_TITLES:
            self._assert(app.create_game_row(t, {}), False, t)


class TalentAndPublisher(unittest.TestCase):
    """Generation always emits DAR rows; Review must follow the file's title."""

    def test_talent_generation_default_unchanged(self):
        row = app.create_talent_row("Zendaya", {})
        self.assertEqual(row['title'], "Zendaya - DAR")
        self.assertIn('Pristine DAR Brands', row['brand_set'])

    def test_talent_review_respects_title(self):
        row = app.create_talent_row("Zendaya", {}, respect_title_dar=True)
        self.assertEqual(row['title'], "Zendaya")
        self.assertIn('Competitive View', row['brand_set'])
        self.assertNotIn('Pristine DAR Brands', row['brand_set'])

        row = app.create_talent_row("Zendaya -DAR", {}, respect_title_dar=True)
        self.assertEqual(row['title'], "Zendaya - DAR")
        self.assertIn('Pristine DAR Brands', row['brand_set'])

    def test_publisher_review_respects_title(self):
        row = app.create_publisher_row("Wired", {}, respect_title_dar=True)
        self.assertEqual(row['title'], "Wired")
        self.assertIn('Competitive View', row['brand_set'])

        row = app.create_publisher_row("Wired - dar", {}, respect_title_dar=True)
        self.assertEqual(row['title'], "Wired - DAR")
        self.assertIn('Pristine DAR Brands', row['brand_set'])

    def test_publisher_generation_default_unchanged(self):
        row = app.create_publisher_row("Wired", {})
        self.assertEqual(row['title'], "Wired - DAR")
        self.assertIn('Pristine DAR Brands', row['brand_set'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
