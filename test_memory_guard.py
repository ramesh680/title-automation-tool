"""Render OOM fix (Oct 2026): memory guard, global download cap, YouTube
breaker and API-key redaction."""
import threading
import time
import unittest
from unittest import mock

import memory_guard as MG
import metadata_fetcher as MF


class _Resp:
    def __init__(self, code, text=""):
        self.status_code, self.text, self.headers = code, text, {}

    def close(self):
        pass


class MemoryGuard(unittest.TestCase):
    def test_rss_is_reported(self):
        self.assertGreater(MG.rss_mb(), 10)

    def test_relieve_clears_registered_caches_lowest_priority_first(self):
        a, b = {"x": 1}, {"y": 2}
        MG.register_cache("t-a", a, priority=-10)
        MG.register_cache("t-b", b, priority=-9)
        try:
            # readings: before, check before t-a, check before t-b, after
            with mock.patch.object(MG, "rss_mb", side_effect=[999, 999, 1, 1, 1]):
                MG.relieve(target_mb=100)
        finally:
            MG._clearers[:] = [c for c in MG._clearers if not c[1].startswith("t-")]
        self.assertEqual(a, {})          # cleared first
        self.assertEqual(b, {"y": 2})    # memory already fine -> kept

    def test_no_wait_when_below_hard_limit(self):
        t = time.time()
        MG.wait_for_headroom()
        self.assertLess(time.time() - t, 0.5)

    def test_title_cache_is_cleared_last(self):
        names = [n for _p, n, _f in MG._clearers]
        self.assertEqual(names[-1], "title metadata")
        self.assertLess(names.index("page html"), names.index("title metadata"))


class InflightCap(unittest.TestCase):
    def test_downloads_in_flight_never_exceed_cap(self):
        live, peak, lock = [0], [0], threading.Lock()

        def fake_get(url, **kw):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.05)
            with lock:
                live[0] -= 1
            return _Resp(200)

        with mock.patch.object(MF._SESSION, "get", side_effect=fake_get):
            ts = [threading.Thread(target=MF._polite_get,
                                   args=(f"https://host{i % 12}.example/p{i}",))
                  for i in range(60)]
            [t.start() for t in ts]
            [t.join() for t in ts]
        self.assertLessEqual(peak[0], MF.MAX_INFLIGHT)


class YouTubeBreaker(unittest.TestCase):
    def setUp(self):
        MF._YT_BLOCKED_UNTIL["t"] = 0.0

    tearDown = setUp

    def test_429_trips_breaker_without_retries(self):
        url = MF.YT_SEARCH + "?q=x&key=SECRET"
        with mock.patch.object(MF._SESSION, "get", return_value=_Resp(429)) as g:
            MF._polite_get(url)
            self.assertEqual(g.call_count, 1)        # no 4x back-off
            self.assertIsNone(MF._get_json(url))     # skipped during cool-down
            self.assertEqual(g.call_count, 1)

    def test_other_hosts_unaffected(self):
        MF._YT_BLOCKED_UNTIL["t"] = time.time() + 60
        with mock.patch.object(MF._SESSION, "get", return_value=_Resp(200)) as g:
            MF._polite_get("https://www.wikidata.org/w/api.php")
            self.assertEqual(g.call_count, 1)


class Redaction(unittest.TestCase):
    def test_api_key_never_logged(self):
        u = "https://www.googleapis.com/youtube/v3/search?part=snippet&key=AIzaXYZ&q=a"
        self.assertNotIn("AIzaXYZ", MF._redact(u))
        with self.assertLogs(MF.log, "WARNING") as cm:
            with mock.patch.object(MF._SESSION, "get", side_effect=OSError(u)):
                MF._get_json("https://example.org/?key=AIzaXYZ")
        self.assertNotIn("AIzaXYZ", "\n".join(cm.output))


if __name__ == "__main__":
    unittest.main()
