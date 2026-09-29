"""Only live, public social profiles reach the export (Sep 2026)."""
import unittest
from unittest import mock

import metadata_fetcher as mf


class VerifySocialsLiveness(unittest.TestCase):
    def setUp(self):
        self._v, self._s = mf.VALIDATE_URLS, mf.SOCIAL_STRICT
        mf.VALIDATE_URLS, mf.SOCIAL_STRICT = True, False
        mf._PROBE_CACHE.clear()

    def tearDown(self):
        mf.VALIDATE_URLS, mf.SOCIAL_STRICT = self._v, self._s

    def _meta(self, **src):
        m = {"facebook_page": "http://www.facebook.com/gonepage",
             "instagram_user": "liveig", "twitter_handle": "unknowntw",
             "tiktok_user": "privatetok"}
        if src:
            m["_social_src"] = src
        return m

    def _probe(self, plat, h):
        return {"facebook": (False, ""), "instagram": (True, "Live"),
                "twitter": (None, ""), "tiktok": (False, "")}[plat]

    def test_dead_or_restricted_dropped_with_note(self):
        meta, notes = self._meta(), []
        with mock.patch.object(mf, "probe_handle", side_effect=self._probe):
            mf.verify_socials(meta, "X", notes=notes)
        self.assertNotIn("facebook_page", meta)
        self.assertNotIn("tiktok_user", meta)   # TikTok is now verified too
        self.assertEqual(meta["instagram_user"], "liveig")
        self.assertEqual(meta["twitter_handle"], "unknowntw")  # curated + unknown -> kept
        self.assertTrue(any("Facebook" in n and "removed" in n for n in notes))
        self.assertTrue(any("could not be opened" in n for n in notes))

    def test_unknown_guess_dropped(self):
        meta = self._meta(twitter="guess")
        with mock.patch.object(mf, "probe_handle", side_effect=self._probe):
            mf.verify_socials(meta, "X")
        self.assertNotIn("twitter_handle", meta)

    def test_strict_drops_every_unconfirmed(self):
        mf.SOCIAL_STRICT = True
        meta = self._meta()
        with mock.patch.object(mf, "probe_handle", side_effect=self._probe):
            mf.verify_socials(meta, "X")
        self.assertEqual(set(k for k in meta if not k.startswith("_")), {"instagram_user"})


class FacebookProbe(unittest.TestCase):
    def setUp(self):
        self._tok = mf.FB_ACCESS_TOKEN
        mf.FB_ACCESS_TOKEN = ""

    def tearDown(self):
        mf.FB_ACCESS_TOKEN = self._tok

    def test_unavailable_page_is_false(self):
        html = "<html><title>Facebook</title>This content isn't available right now</html>"
        with mock.patch.object(mf, "_fetch", return_value=(200, html)):
            self.assertEqual(mf._probe_handle("facebook", "http://www.facebook.com/gone")[0], False)

    def test_country_restricted_is_false(self):
        html = "<html>This page isn't available in your country</html>"
        with mock.patch.object(mf, "_fetch", return_value=(200, html)):
            self.assertFalse(mf._probe_handle("facebook", "restricted")[0])

    def test_public_page_is_true(self):
        html = '<meta property="og:title" content="Superman" />'
        with mock.patch.object(mf, "_fetch", return_value=(200, html)):
            self.assertEqual(mf._probe_handle("facebook", "SupermanMovie"), (True, "Superman"))

    def test_login_wall_is_unknown(self):
        html = '<meta property="og:title" content="Log into Facebook" />'
        with mock.patch.object(mf, "_fetch", return_value=(200, html)):
            self.assertIsNone(mf._probe_handle("facebook", "whatever")[0])

    def test_graph_api_not_found(self):
        mf.FB_ACCESS_TOKEN = "app|secret"
        with mock.patch.object(mf, "_fetch",
                               return_value=(404, '{"error":{"code":803,"message":"x"}}')):
            self.assertFalse(mf._probe_handle("facebook", "nope")[0])


class OtherProbes(unittest.TestCase):
    def test_instagram_missing_user(self):
        with mock.patch.object(mf, "_fetch", return_value=(404, "")):
            self.assertFalse(mf._probe_handle("instagram", "nobody")[0])

    def test_instagram_private_is_false(self):
        body = '{"data":{"user":{"username":"x","is_private":true}}}'
        with mock.patch.object(mf, "_fetch", return_value=(200, body)):
            self.assertFalse(mf._probe_handle("instagram", "x")[0])

    def test_twitter_suspended(self):
        with mock.patch.object(mf, "_fetch", return_value=(403, "")):
            self.assertFalse(mf._probe_handle("twitter", "gone")[0])

    def test_tiktok_private(self):
        body = '"uniqueId":"abc","privateAccount":true'
        with mock.patch.object(mf, "_fetch", return_value=(200, body)):
            self.assertFalse(mf._probe_handle("tiktok", "abc")[0])


if __name__ == "__main__":
    unittest.main()
