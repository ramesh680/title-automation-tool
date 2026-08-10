"""
test_movie_rules.py
-------------------
Locks in the Movies-column rules added Aug 2026:
  * Validator: network parent-vs-child checkpoint, twitter_search_terms
    structure, twitter_search_term_keywords "no bare hashtag" rule.
  * Review comparator: network alias/umbrella tolerance (+ blank allowed),
    companies DAR/main rule, and the bare-hashtag-in-keywords guard.
Run: python test_movie_rules.py
"""
import unittest

import validator as V
import app


def _chk(name, val, row, rule=None):
    return V.CHECKS[name](val, row, rule or {"applies_to": ["Movies", "TV Shows"]})


class ValidatorMovieChecks(unittest.TestCase):
    def test_network_umbrella_warns(self):
        sev, msg = _chk("network_boxofficemojo_checkpoint",
                        "Walt Disney Studios Motion Pictures",
                        {"title": "Ice Age", "title_category": "Movies"})
        self.assertEqual(sev, V.SEV_WARN)
        self.assertIn("20th Century Studios", msg)

    def test_network_blank_allowed(self):
        self.assertEqual(_chk("network_boxofficemojo_checkpoint", "",
                              {"title": "X", "title_category": "Movies"})[0], None)

    def test_network_approved_passes(self):
        self.assertEqual(_chk("network_boxofficemojo_checkpoint", "A24",
                              {"title": "X", "title_category": "Movies"})[0], None)

    def test_network_offlist_warns(self):
        self.assertEqual(_chk("network_boxofficemojo_checkpoint", "Made Up Co",
                              {"title": "X", "title_category": "Movies"})[0], V.SEV_WARN)

    def test_tst_structure_ok(self):
        self.assertEqual(_chk("twitter_search_terms_structure",
                              "#airbud|DAR|DAR\n@airbud|DAR|DAR",
                              {"title": "Air Bud - DAR"}, {})[0], None)

    def test_tst_wrong_field_count_fails(self):
        self.assertEqual(_chk("twitter_search_terms_structure", "airbud|DAR",
                              {"title": "Air Bud - DAR"}, {})[0], V.SEV_FAIL)

    def test_tst_space_runs_fail(self):
        self.assertEqual(_chk("twitter_search_terms_structure",
                              "#x|A24|HBO Max   HBO Max Latam",
                              {"title": "X"}, {})[0], V.SEV_FAIL)

    def test_keywords_bare_hashtag_fails(self):
        self.assertEqual(_chk("twitter_search_term_keywords_query",
                              "#primetime(2026)|A24|A24", {"title": "X"}, {})[0], V.SEV_FAIL)

    def test_keywords_query_ok(self):
        self.assertEqual(_chk("twitter_search_term_keywords_query",
                              '("x") ("a24" or #a24)|A24|A24', {"title": "X"}, {})[0], None)


class ReviewComparatorMovieRules(unittest.TestCase):
    rc = staticmethod(app._review_compare)

    def test_network_umbrella_resolves_to_child(self):
        self.assertTrue(self.rc("network", "Walt Disney Studios Motion Pictures",
                                "Disney", title="Avengers")[0])

    def test_network_sony_releasing_alias(self):
        self.assertTrue(self.rc("network", "Sony Pictures Releasing",
                                "Sony / Columbia", title="Klara")[0])

    def test_network_umbrella_vs_specific_flags(self):
        self.assertFalse(self.rc("network", "Walt Disney Studios Motion Pictures",
                                 "20th Century Studios", title="Ice Age")[0])

    def test_network_blank_allowed(self):
        self.assertTrue(self.rc("network", "", "Disney", title="X")[0])

    def test_companies_dar_must_be_pristine(self):
        self.assertFalse(self.rc("companies", "Warner Bros. Pictures",
                                 "Pristine Brand", title="Clayface - DAR")[0])

    def test_companies_regular_nonblank_ok(self):
        self.assertTrue(self.rc("companies", "Unknown",
                                "Warner Bros. Pictures", title="Clayface")[0])

    def test_companies_regular_blank_is_gap(self):
        self.assertFalse(self.rc("companies", "",
                                 "Warner Bros. Pictures", title="Clayface")[0])

    def test_keywords_bare_hashtag_flagged(self):
        self.assertFalse(self.rc("twitter_search_term_keywords",
                                 '#primetime(2026)|A24|A24\n("primetime") (x)',
                                 '("primetime") (x)', title="Primetime")[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
