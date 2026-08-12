"""Rule 2 (IMDb / Metacritic / RT URL variants are equivalent) and
Rule 6 (release-date validation for Movies & TV Shows)."""
import unittest

from app import _review_compare
import validator


def rok(col, cur, exp, **kw):
    return _review_compare(col, cur, exp, **kw)[0]


class UrlVariantsRule2(unittest.TestCase):
    """http vs https and a trailing '/' must not be flagged as a mismatch."""

    def test_imdb_http_trailing_vs_https_bare(self):
        self.assertTrue(rok('imdb_id',
                            'http://www.imdb.com/title/tt14125350/',
                            'https://www.imdb.com/title/tt14125350'))

    def test_metacritic_http_bare_vs_https_trailing(self):
        self.assertTrue(rok('metacritic',
                            'http://www.metacritic.com/tv/boarders',
                            'https://www.metacritic.com/tv/boarders/'))

    def test_rottentomatoes_variants(self):
        self.assertTrue(rok('rottentomatoes',
                            'http://www.rottentomatoes.com/m/superman/',
                            'https://www.rottentomatoes.com/m/superman'))

    def test_www_difference_ignored(self):
        self.assertTrue(rok('imdb_id',
                            'https://imdb.com/title/tt14125350',
                            'https://www.imdb.com/title/tt14125350/'))

    def test_genuinely_different_id_still_flagged(self):
        self.assertFalse(rok('imdb_id',
                             'https://www.imdb.com/title/tt0000001',
                             'https://www.imdb.com/title/tt14125350'))


class ReleaseDateRule6(unittest.TestCase):
    def chk(self, val, cat="Movies"):
        rule = {"applies_to": ["Movies", "TV Shows"], "message": "bad date"}
        return validator._chk_release_date_valid(val, {"title_category": cat}, rule)

    def test_valid_iso_date_passes(self):
        self.assertIsNone(self.chk("2026-08-05")[0])

    def test_valid_us_date_passes(self):
        self.assertIsNone(self.chk("08/05/2026")[0])

    def test_missing_date_is_mandatory_failure(self):
        # Rule 1 (Aug 2026): released_on is mandatory for Movies & TV Shows, so a
        # blank is now a hard failure rather than a soft "lookup pending" warning.
        self.assertEqual(self.chk("")[0], validator.SEV_FAIL)

    def test_bare_year_is_warning(self):
        self.assertEqual(self.chk("2026")[0], validator.SEV_WARN)

    def test_malformed_date_fails(self):
        self.assertEqual(self.chk("not-a-date")[0], validator.SEV_FAIL)

    def test_impossible_date_fails(self):
        self.assertEqual(self.chk("2026-13-40")[0], validator.SEV_FAIL)

    def test_only_movies_and_tv_checked(self):
        # a Talent row is not subject to the Movies/TV release-date rule
        self.assertIsNone(self.chk("", cat="Talent")[0])

    def test_tv_shows_checked(self):
        # blank released_on for a TV Show is mandatory -> hard failure
        self.assertEqual(self.chk("", cat="TV Shows")[0], validator.SEV_FAIL)

    def test_rule_registered_and_in_defaults(self):
        self.assertIn("release_date_valid", validator.CHECKS)
        cols = [r.get("column") for r in validator.DEFAULT_RULES["rules"]]
        self.assertIn("released_on", cols)


if __name__ == "__main__":
    unittest.main()
