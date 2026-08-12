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


if __name__ == "__main__":
    unittest.main()
