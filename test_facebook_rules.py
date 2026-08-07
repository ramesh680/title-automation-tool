"""Facebook page hygiene rule (Rule 5, Aug 2026), applied on all three surfaces:

A Facebook value is NOT usable data when the URL contains a /p/, /php/ or
/people/ path segment, or is a profile.php URL. Such values must be dropped
(generator + discovery) or flagged (review + validator) rather than kept.
"""
import unittest

from app import _review_compare
import validator
import metadata_fetcher


def rok(col, cur, exp, **kw):
    return _review_compare(col, cur, exp, **kw)[0]


GOOD = "http://www.facebook.com/OfficialMoviePage"
BAD_P = "http://www.facebook.com/p/Some-Movie-123"
BAD_PHP = "http://www.facebook.com/php/Some-Movie"
BAD_PROFILE = "http://www.facebook.com/profile.php?id=100012345"
BAD_PEOPLE = "http://www.facebook.com/people/Some-Movie/100012345"


class ReviewFacebook(unittest.TestCase):
    def test_p_url_flagged(self):
        self.assertFalse(rok('facebook_page', BAD_P, GOOD))

    def test_php_url_flagged(self):
        self.assertFalse(rok('facebook_page', BAD_PHP, GOOD))

    def test_profile_php_flagged(self):
        self.assertFalse(rok('facebook_page', BAD_PROFILE, GOOD))

    def test_people_url_flagged(self):
        self.assertFalse(rok('facebook_page', BAD_PEOPLE, GOOD))

    def test_bad_value_flagged_even_with_no_expected(self):
        # early "expected empty -> pass" short-circuit must not let it through
        self.assertFalse(rok('facebook_page', BAD_PHP, ''))
        self.assertFalse(rok('facebook_page', BAD_PROFILE, ''))
        self.assertFalse(rok('facebook_page', BAD_P, ''))

    def test_good_page_not_flagged(self):
        self.assertTrue(rok('facebook_page', GOOD, GOOD))


class ValidatorFacebook(unittest.TestCase):
    def chk(self, val):
        return validator._chk_facebook_page_valid(val, {}, {"message": "bad fb"})[0]

    def test_p_url_fails(self):
        self.assertEqual(self.chk(BAD_P), validator.SEV_FAIL)

    def test_php_url_fails(self):
        self.assertEqual(self.chk(BAD_PHP), validator.SEV_FAIL)

    def test_profile_php_fails(self):
        self.assertEqual(self.chk(BAD_PROFILE), validator.SEV_FAIL)

    def test_people_url_fails(self):
        self.assertEqual(self.chk(BAD_PEOPLE), validator.SEV_FAIL)

    def test_good_page_passes(self):
        self.assertIsNone(self.chk(GOOD))

    def test_blank_passes(self):
        self.assertIsNone(self.chk(""))

    def test_rule_registered_and_in_defaults(self):
        self.assertIn("facebook_page_hygiene", validator.CHECKS)
        cols = [r.get("column") for r in validator.DEFAULT_RULES["rules"]]
        self.assertIn("facebook_page", cols)


class DiscoveryDropsBadFacebook(unittest.TestCase):
    def setUp(self):
        self._prev = metadata_fetcher.VALIDATE_URLS
        metadata_fetcher.VALIDATE_URLS = False  # isolate format rule from live checks

    def tearDown(self):
        metadata_fetcher.VALIDATE_URLS = self._prev

    def test_p_url_dropped(self):
        meta = {"facebook_page": BAD_P}
        metadata_fetcher.verify_socials(meta)
        self.assertNotIn("facebook_page", meta)

    def test_php_url_dropped(self):
        meta = {"facebook_page": BAD_PHP}
        metadata_fetcher.verify_socials(meta)
        self.assertNotIn("facebook_page", meta)

    def test_profile_php_dropped(self):
        meta = {"facebook_page": BAD_PROFILE}
        metadata_fetcher.verify_socials(meta)
        self.assertNotIn("facebook_page", meta)

    def test_good_page_kept(self):
        meta = {"facebook_page": GOOD}
        metadata_fetcher.verify_socials(meta)
        self.assertEqual(meta.get("facebook_page"), GOOD)


if __name__ == "__main__":
    unittest.main()
