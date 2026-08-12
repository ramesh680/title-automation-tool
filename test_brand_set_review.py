"""Brand-set review rule (Aug 2026):

Reviewer feedback: the Review must NOT remove the brand sets already present in
an uploaded file. It should only check whether the brand set(s) required by the
ingest template are present; if a required brand set is missing, flag it
(Mismatch when the cell has other values, Gap when empty) and suggest the file's
own value with the missing required brand set appended -- never a replacement
that drops the curated values.

Covers both review paths:
  * legacy (Movies / TV / Talent / Video Game) via app._review_compare
  * new ingest schemas (Beauty / Beverages / Sports / General) via
    titleforge_validator.validate_row
"""
import unittest

from app import _review_compare, _merge_brand_set
from titleforge_validator import load_rules, validate_row
from titleforge_ingest_ext import detect_schema


def cmp(col, cur, exp, **kw):
    return _review_compare(col, cur, exp, **kw)


class LegacyBrandSet(unittest.TestCase):
    def test_required_present_extra_kept_not_flagged(self):
        # file carries the required 'Competitive View' plus extra curated sets
        cur = "Competitive View\nLF // Custom Client Brands\nLF // Special Project"
        ok, sugg = cmp('brand_set', cur, "Competitive View")
        self.assertTrue(ok)

    def test_order_insensitive_required_present(self):
        cur = "LF // Custom\nCompetitive View"
        ok, _ = cmp('brand_set', cur, "Competitive View")
        self.assertTrue(ok)

    def test_missing_required_flagged_as_mismatch_and_suggestion_is_additive(self):
        # file has curated brand sets but is MISSING the required 'Competitive View'
        cur = "LF // Custom Client Brands\nLF // Special Project"
        ok, sugg = cmp('brand_set', cur, "Competitive View")
        self.assertFalse(ok)                       # flagged
        # suggestion keeps everything the file had...
        self.assertIn("LF // Custom Client Brands", sugg)
        self.assertIn("LF // Special Project", sugg)
        # ...and appends the missing required brand set (nothing removed)
        self.assertIn("Competitive View", sugg)

    def test_empty_cell_gap_suggests_required(self):
        ok, sugg = cmp('brand_set', "", "Competitive View")
        self.assertFalse(ok)
        self.assertEqual(sugg, "Competitive View")

    def test_dar_required_present_with_extras(self):
        cur = "Pristine DAR Brands\nLF // Custom Roll-Up"
        ok, _ = cmp('brand_set', cur, "Pristine DAR Brands")
        self.assertTrue(ok)


class MergeHelper(unittest.TestCase):
    def test_no_duplicate_when_already_present_case_insensitive(self):
        merged = _merge_brand_set("competitive view\nLF // X", "Competitive View")
        # existing line kept as-is, required not duplicated
        self.assertEqual(merged.count("ompetitive"), 1)
        self.assertIn("LF // X", merged)

    def test_appends_only_missing_lines(self):
        merged = _merge_brand_set("LF // X", "Competitive View\nPristine DAR Brands")
        self.assertEqual(
            merged.splitlines(),
            ["LF // X", "Competitive View", "Pristine DAR Brands"])


class TfxBrandSet(unittest.TestCase):
    RULES = load_rules("titleforge_validation_rules.json")

    def _brand_finding(self, row):
        sk = detect_schema(row) or "general"
        return [f for f in validate_row(row, sk, self.RULES)
                if f["field"] == "brand_set"]

    def test_beauty_required_present_with_extras_not_flagged(self):
        row = {"Perspective": "Standard", "title": "Fenty Beauty - DAR",
               "title_category": "Health & Beauty",
               "Beauty Type 1": "Beauty Type - Makeup", "Beauty Company": "LVMH",
               "brand_set": "LF // Beauty\nLF // Custom Client Brands",
               "companies": "Pristine Brand", "active": "t"}
        self.assertEqual(self._brand_finding(row), [])

    def test_beauty_missing_required_flagged_additive(self):
        row = {"Perspective": "Standard", "title": "Fenty Beauty - DAR",
               "title_category": "Health & Beauty",
               "Beauty Type 1": "Beauty Type - Makeup", "Beauty Company": "LVMH",
               "brand_set": "LF // Custom Client Brands",
               "companies": "Pristine Brand", "active": "t"}
        f = self._brand_finding(row)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["status"], "mismatch")
        # curated value kept + required Standard brand set appended
        self.assertIn("LF // Custom Client Brands", f[0]["expected"])
        self.assertIn("LF // Beauty", f[0]["expected"])

    def test_beauty_empty_is_gap_with_required(self):
        row = {"Perspective": "Standard", "title": "Fenty Beauty - DAR",
               "title_category": "Health & Beauty",
               "Beauty Type 1": "Beauty Type - Makeup", "Beauty Company": "LVMH",
               "brand_set": "",
               "companies": "Pristine Brand", "active": "t"}
        f = self._brand_finding(row)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["status"], "gap")
        self.assertEqual(f[0]["expected"], "LF // Beauty")


class KeywordFormattingNoise(unittest.TestCase):
    """twitter_search_term_keywords (Aug 2026): the tool-generated clause is
    lowercased and emits both an @handle and a #hashtag for a network; a curator
    often writes only the #hashtag + quoted name in mixed case. Those are the
    SAME query and must not be flagged as a Mismatch. A genuinely different
    distributor/year still is. Strings below are the real Current/Suggested
    values from the reviewed file."""

    # Row 2 -- equivalent (case + extra @handle only)
    R2_CUR = ('("The Last Picture Shows") (#FoghornFeatures OR "Foghorn Features" OR '
              '"2026" OR "All New" OR Episode OR Watch OR tv OR Show OR Series OR season '
              'OR binge OR Stream OR Film OR Movie OR Premiere OR Screening OR Feature OR '
              'Trailer OR Teaser OR theater OR release)|DAR|DAR|2021-01-01')
    R2_SUGG = ('("the last picture shows") ("foghorn features" or @foghornfeatures or '
               '#foghornfeatures or "2026" or "all new" or episode or watch or tv or show '
               'or series or season or binge or stream or film or movie or premiere or '
               'screening or feature or trailer or teaser or theater or release)'
               '|DAR|DAR|2021-01-01')

    # Row 3 -- equivalent
    R3_CUR = ('("Awarapan 2") (#MarudharFilms OR "Marudhar Films" OR "2026" OR "All New" '
              'OR Episode OR Watch OR tv OR Show OR Series OR season OR binge OR Stream OR '
              'Film OR Movie OR Premiere OR Screening OR Feature OR Trailer OR Teaser OR '
              'theater OR release)|DAR|DAR|2021-01-01')
    R3_SUGG = ('("awarapan 2") ("marudhar films" or @marudharfilms or #marudharfilms or '
               '"2026" or "all new" or episode or watch or tv or show or series or season '
               'or binge or stream or film or movie or premiere or screening or feature or '
               'trailer or teaser or theater or release)|DAR|DAR|2021-01-01')

    # Row 4 -- genuinely different (Rialto Pictures / 2026 vs Anglo-Amalgamated / 1963)
    R4_CUR = ('("Billy Liar") ("Rialto Pictures" OR @RialtoPictures OR #RialtoPictures OR '
              '"2026" OR "All New" OR Episode OR release)|DAR|DAR|2021-01-01')
    R4_SUGG = ('("billy liar") ("anglo-amalgamated film distributors" or '
               '@angloamalgamatedfilmdistributors or #angloamalgamatedfilmdistributors or '
               '"1963" or "all new" or episode or release)|DAR|DAR|2021-01-01')

    def test_row2_case_and_handle_variant_not_flagged(self):
        # current is correct; suggested differs only in case + an extra @handle
        self.assertTrue(cmp('twitter_search_term_keywords', self.R2_CUR, self.R2_SUGG)[0])

    def test_row3_case_and_handle_variant_not_flagged(self):
        self.assertTrue(cmp('twitter_search_term_keywords', self.R3_CUR, self.R3_SUGG)[0])

    def test_row4_different_distributor_still_flagged(self):
        self.assertFalse(cmp('twitter_search_term_keywords', self.R4_CUR, self.R4_SUGG)[0])

    def test_empty_current_still_gap(self):
        self.assertFalse(cmp('twitter_search_term_keywords', '', self.R2_SUGG)[0])


if __name__ == "__main__":
    unittest.main()
