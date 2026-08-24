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




class GeneralSchemaBrandSet(unittest.TestCase):
    """The 'Spotify (India) - DAR' class of bug: rows routed to the General
    schema had NO vertical brand set defined, so the required value came back
    empty and the caller fell back to the first dropdown entry -- literally
    'Competitive View' -- for every row, DAR or not."""

    @classmethod
    def setUpClass(cls):
        from titleforge_ingest_ext import detect_schema
        from titleforge_validator import load_rules, validate_row
        import os
        cls.detect = staticmethod(detect_schema)
        cls.validate = staticmethod(validate_row)
        cls.rules = load_rules(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'titleforge_validation_rules.json'))

    def _finding(self, title, brand_set,
                 cat="Music and Entertainment", sub=""):
        row = {"title": title, "title_category": cat,
               "brand_set": brand_set, "title_sub_category": sub}
        schema = self.detect(row) or 'general'
        fds = [f for f in self.validate(row, schema, self.rules)
               if f['field'] == 'brand_set']
        return fds[0] if fds else None

    def test_dar_row_with_correct_set_is_not_flagged(self):
        for t in ["Spotify (Singapore) - DAR", "Spotify (Malaysia) - DAR",
                  "Spotify (India) - DAR", "Spotify (Philippines) - DAR",
                  "Spotify (Japan) - DAR"]:
            self.assertIsNone(
                self._finding(t, "Pristine DAR Brands\nSpotify Global Roll-Up"), t)

    def test_dar_row_never_told_to_add_competitive_view(self):
        fd = self._finding("Spotify (Chile) - DAR", "Spotify Global Roll-Up")
        self.assertIsNotNone(fd)
        self.assertIn("Pristine DAR Brands", fd['expected'])
        self.assertNotIn("Competitive View", fd['expected'])

    def test_dar_row_with_competitive_view_is_corrected(self):
        fd = self._finding("Spotify (Korea) - DAR",
                           "Competitive View\nSpotify Global Roll-Up")
        self.assertEqual(fd['expected'],
                         "Spotify Global Roll-Up\nPristine DAR Brands")

    def test_non_dar_row_with_dar_set_is_corrected(self):
        fd = self._finding("Deezer", "Pristine DAR Brands\nMusic Roll-Up")
        self.assertEqual(fd['expected'], "Music Roll-Up\nCompetitive View")

    def test_non_dar_row_with_competitive_view_is_not_flagged(self):
        self.assertIsNone(self._finding("Spotify (Brazil)", "Competitive View"))

    def test_empty_cell_gaps_to_the_right_perspective(self):
        self.assertEqual(
            self._finding("Spotify (Chile) - DAR", "")['expected'],
            "Pristine DAR Brands")
        self.assertEqual(
            self._finding("Tidal", "")['expected'], "Competitive View")

    def test_schema_vertical_still_wins_where_defined(self):
        # Beauty defines its own vertical; that behaviour is unchanged
        self.assertIsNone(self._finding(
            "Fenty Beauty - DAR", "LF // Beauty",
            cat="Health & Beauty", sub="Beauty Type - Makeup"))
        fd = self._finding("Fenty Beauty - DAR", "LF // Beauty\nCompetitive View",
                           cat="Health & Beauty", sub="Beauty Type - Makeup")
        self.assertEqual(fd['expected'], "LF // Beauty")

    def test_dar_suffix_is_normalised_not_doubled(self):
        from titleforge_ingest_ext import _dar
        for t in ["Spotify-DAR", "Spotify - dar", "Spotify \u2013 DAR", "Spotify"]:
            self.assertEqual(_dar(t, {"Perspective": "Standard"}),
                             "Spotify - DAR", t)


if __name__ == '__main__':
    unittest.main(verbosity=2)
