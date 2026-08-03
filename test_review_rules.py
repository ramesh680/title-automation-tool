"""Review-comparison rules requested Aug 2026:
  1. TV title_sub_category: same components in any order/spacing => not an error.
  2. twitter_handle & twitter_search_term_keywords: case-insensitive (and, for
     keywords, grouping-insensitive) => not an error.
"""
import unittest
from app import _review_compare

def ok(col, cur, exp, **kw): return _review_compare(col, cur, exp, **kw)[0]


class TitleSubCategoryTV(unittest.TestCase):
    A = "Daypart - Daytime\nLanguage Type - English\nNetwork - Oxygen\nProgram Type - Series"
    B = "Program Type - Series\nNetwork - Oxygen\nDaypart - Daytime\nLanguage Type - English"

    def test_same_components_different_order_not_flagged(self):
        self.assertTrue(ok('title_sub_category', self.A, self.B))

    def test_reverse_direction_also_ok(self):
        self.assertTrue(ok('title_sub_category', self.B, self.A))

    def test_internal_spacing_difference_not_flagged(self):
        cur = "Daypart -  Daytime\nLanguage  Type - English\nNetwork - Oxygen\nProgram Type - Series"
        self.assertTrue(ok('title_sub_category', cur, self.A))

    def test_case_difference_not_flagged(self):
        cur = "daypart - daytime\nlanguage type - english\nnetwork - oxygen\nprogram type - series"
        self.assertTrue(ok('title_sub_category', cur, self.A))

    def test_genuinely_different_value_still_flagged(self):
        cur = "Daypart - Daytime\nLanguage Type - English\nNetwork - Bravo\nProgram Type - Series"
        self.assertFalse(ok('title_sub_category', cur, self.A))  # Oxygen expected, missing


class TwitterHandle(unittest.TestCase):
    def test_camel_vs_lower(self):
        self.assertTrue(ok('twitter_handle', 'officiallivepd', 'OfficialLivePD'))
        self.assertTrue(ok('twitter_handle', 'OfficialLivePD', 'officiallivepd'))

    def test_second_example(self):
        self.assertTrue(ok('twitter_handle', 'themadisonpplus', 'TheMadisonPPlus'))

    def test_url_vs_bare_handle(self):
        self.assertTrue(ok('twitter_handle', 'http://twitter.com/OfficialLivePD', 'officiallivepd'))

    def test_at_prefix_ignored(self):
        self.assertTrue(ok('twitter_handle', '@OfficialLivePD', 'officiallivepd'))

    def test_genuinely_different_handle_flagged(self):
        self.assertFalse(ok('twitter_handle', 'someotheracct', 'OfficialLivePD'))


EX1 = ('("a plan to kill")("oxygen" or @oxygen or #oxygen or #oxygennetwork) '
       '("2024" or "all new" or episode or watch or tv or show or series or season '
       'or binge or stream or film or movie or premiere or screening or feature or '
       'trailer or teaser or theater or release)|Operations - Core Title|Operations - Core Title')
EX2 = ('("A Plan to Kill") ("Oxygen" OR @oxygen OR #Oxygen OR #oxygennetwork OR "2024" '
       'OR "All New" OR Episode OR Watch OR tv OR Show OR Series OR season OR binge OR '
       'Stream OR Film OR Movie OR Premiere OR Screening OR Feature OR Trailer OR Teaser '
       'OR theater OR release)|Operations - Core Title|Operations - Core Title')


class KeywordClause(unittest.TestCase):
    def test_case_and_grouping_variants_are_equal(self):
        self.assertTrue(ok('twitter_search_term_keywords', EX1, EX2))
        self.assertTrue(ok('twitter_search_term_keywords', EX2, EX1))

    def test_missing_term_still_flagged(self):
        dropped = EX2.replace(' OR #oxygennetwork', '')  # genuinely fewer terms
        self.assertFalse(ok('twitter_search_term_keywords', dropped, EX1))

    def test_empty_current_flagged_as_gap(self):
        self.assertFalse(ok('twitter_search_term_keywords', '', EX1))


if __name__ == "__main__":
    unittest.main()
