"""Network/studio YouTube channel is resolved live for the title's network
(network_youtube.py); the ingest-template value is only a fallback."""
import os
import unittest
from unittest import mock

import metadata_fetcher as mf
import network_youtube as nyt
import app


def _ent(label, aliases=(), p31=("Q4830453",), handles=(), ids=(), pref_id=None):
    def claim(v, rank="normal"):
        return {"rank": rank, "mainsnak": {"datavalue": {"value": v}}}
    claims = {"P31": [claim({"id": q}) for q in p31]}
    if handles:
        claims["P11245"] = [claim(h) for h in handles]
    if ids:
        claims["P2397"] = [claim(i, "preferred" if i == pref_id else "normal") for i in ids]
    return {"labels": {"en": {"value": label}},
            "aliases": {"en": [{"value": a} for a in aliases]},
            "claims": claims}


class FakeAPI:
    """Routes _get_json calls to canned Wikidata / YouTube answers."""
    def __init__(self, search=None, entities=None, alive=None, yt_search=None, yt_stats=None):
        self.search = search or {}
        self.entities = entities or {}
        self.alive = alive            # None -> every channel exists
        self.yt_search = yt_search or {}
        self.yt_stats = yt_stats or {}
        self.calls = []

    def __call__(self, url, params=None, headers=None):
        params = params or {}
        self.calls.append((url, dict(params)))
        if "wikidata" in url and params.get("action") == "wbsearchentities":
            return {"search": [{"id": q} for q in self.search.get(params["search"], [])]}
        if "wikidata" in url and params.get("action") == "wbgetentities":
            return {"entities": {q: self.entities[q] for q in params["ids"].split("|")
                                 if q in self.entities}}
        if url == nyt.YT_CHANNELS and "forHandle" in params or \
                (url == nyt.YT_CHANNELS and params.get("part") == "id"):
            key = params.get("forHandle") or params.get("id")
            ok = True if self.alive is None else key in self.alive
            return {"items": [{"id": key}]} if ok else {"items": []}
        if url == nyt.YT_SEARCH:
            return {"items": [{"snippet": {"title": t, "channelId": c}}
                              for t, c in self.yt_search.get(params["q"], [])]}
        if url == nyt.YT_CHANNELS:
            return {"items": [{"id": c, "statistics": {"subscriberCount": str(self.yt_stats.get(c, 0))}}
                              for c in params["id"].split(",")]}
        return None


class Base(unittest.TestCase):
    def setUp(self):
        nyt.clear_cache()
        self._env = mock.patch.dict(os.environ, {"NETWORK_YT_LIVE": "1",
                                                 "NETWORK_YT_TEMPLATE_FALLBACK": "1"})
        self._env.start()
        self._key = mf.YOUTUBE_API_KEY
        mf.YOUTUBE_API_KEY = ""

    def tearDown(self):
        self._env.stop()
        mf.YOUTUBE_API_KEY = self._key
        nyt.clear_cache()

    def use(self, api):
        p = mock.patch.object(mf, "_get_json", api)
        p.start()
        self.addCleanup(p.stop)
        return api


class NameMatching(unittest.TestCase):
    def test_generic_words_ignored(self):
        self.assertTrue(nyt.names_match("Paramount", "Paramount Pictures"))
        self.assertTrue(nyt.names_match("Sony Classics", "Sony Pictures Classics"))
        self.assertFalse(nyt.names_match("Sony Classics", "Sony Pictures"))
        self.assertFalse(nyt.names_match("Magnet Releasing", "Magnolia Pictures"))

    def test_query_names_order(self):
        self.assertEqual(nyt.query_names("Lionsgate / Summit", raw="Lionsgate Films",
                                         company="Lionsgate"),
                         ["Lionsgate Films", "Lionsgate / Summit", "Lionsgate"])
        self.assertEqual(nyt.query_names("\u00abCONFIRM network (publisher)\u00bb"), [])


class Resolver(Base):
    def test_title_network_qid_used_first(self):
        api = self.use(FakeAPI(entities={"Q1": _ent("Paramount Pictures", handles=["paramountpictures"])}))
        url = nyt.resolve("Paramount", raw="Paramount Pictures", qid="Q1")
        self.assertEqual(url, "http://www.youtube.com/@paramountpictures")
        self.assertFalse(any(p.get("action") == "wbsearchentities" for _, p in api.calls))

    def test_qid_of_a_different_network_ignored(self):
        self.use(FakeAPI(entities={"Q1": _ent("Netflix", handles=["netflix"]),
                                   "Q2": _ent("Neon", aliases=["Neon Rated"], ids=["UCneon000000000000000000"])},
                         search={"Neon": ["Q2"]}))
        self.assertEqual(nyt.resolve("Neon", qid="Q1"),
                         "http://www.youtube.com/channel/UCneon000000000000000000")

    def test_search_skips_films_and_name_mismatches(self):
        self.use(FakeAPI(search={"A24": ["Qfilm", "Qother", "Qa24"]},
                         entities={"Qfilm": _ent("A24", p31=("Q11424",), handles=["a24movie"]),
                                   "Qother": _ent("A24 Fan Club", handles=["a24fans"]),
                                   "Qa24": _ent("A24", handles=["A24"])}))
        self.assertEqual(nyt.resolve("A24", scheme="https"), "https://www.youtube.com/@A24")

    def test_preferred_channel_id_first(self):
        self.use(FakeAPI(search={"Netflix": ["Q9"]},
                         entities={"Q9": _ent("Netflix", ids=["UCaaaaaaaaaaaaaaaaaaaaaa",
                                                              "UCpppppppppppppppppppppp"],
                                              pref_id="UCpppppppppppppppppppppp")}))
        self.assertTrue(nyt.resolve("Netflix").endswith("UCpppppppppppppppppppppp"))

    def test_dead_channel_skipped_when_key_set(self):
        mf.YOUTUBE_API_KEY = "k"
        self.use(FakeAPI(search={"Neon": ["Q2"]}, alive={"@NEONrated"},
                         entities={"Q2": _ent("Neon", handles=["oldneon", "NEONrated"])}))
        self.assertEqual(nyt.resolve("Neon"), "http://www.youtube.com/@NEONrated")

    def test_youtube_search_fallback(self):
        mf.YOUTUBE_API_KEY = "k"
        self.use(FakeAPI(yt_search={"Kani Releasing": [
            ("Kani Releasing Clips", "UCclips"), ("Kani Releasing", "UCsmall"),
            ("Kani Releasing", "UCbig")]},
            yt_stats={"UCsmall": 50, "UCbig": 20000, "UCclips": 900000}))
        self.assertEqual(nyt.resolve("Kani Releasing"), "http://www.youtube.com/channel/UCbig")

    def test_cached_per_network(self):
        api = self.use(FakeAPI(search={"A24": ["Qa24"]}, entities={"Qa24": _ent("A24", handles=["A24"])}))
        nyt.resolve("A24")
        n = len(api.calls)
        nyt.resolve("A24")
        self.assertEqual(len(api.calls), n)


class Fallback(Base):
    def test_template_used_when_live_finds_nothing(self):
        self.use(FakeAPI())
        self.assertEqual(nyt.channel_for("Tiny Studio", fallback="https://www.youtube.com/user/tiny"),
                         "http://www.youtube.com/user/tiny")

    def test_template_fallback_can_be_disabled(self):
        self.use(FakeAPI())
        with mock.patch.dict(os.environ, {"NETWORK_YT_TEMPLATE_FALLBACK": "0"}):
            self.assertEqual(nyt.channel_for("Tiny Studio", fallback="http://x/user/tiny"), "")

    def test_live_off_keeps_template(self):
        api = self.use(FakeAPI())
        with mock.patch.dict(os.environ, {"NETWORK_YT_LIVE": "0"}):
            self.assertEqual(nyt.channel_for("Neon", fallback="http://www.youtube.com/@x"),
                             "http://www.youtube.com/@x")
        self.assertEqual(api.calls, [])


class Generators(Base):
    def test_movie_row_uses_live_network_channel(self):
        # template says user/MagnoliaPictures for Magnet Releasing; live says its own channel
        self.use(FakeAPI(search={"Magnet Releasing": ["Qm"]},
                         entities={"Qm": _ent("Magnet Releasing", handles=["MagnetReleasing"])}))
        row = app.create_row("Test Horror Film", True, metadata={"network": "Magnet Releasing"})
        self.assertEqual(row["youtube_channel_company"], "http://www.youtube.com/@MagnetReleasing")
        self.assertEqual(row["youtube_channel_username"],
                         "http://www.youtube.com/@MagnetReleasing|test horror film")

    def test_movie_without_network_gets_no_network_channel(self):
        api = self.use(FakeAPI())
        row = app.create_row("Test Horror Film", True, metadata={})
        self.assertEqual(row["youtube_channel_company"], "")
        self.assertEqual(row["youtube_channel_username"], "")
        self.assertEqual(api.calls, [])

    def test_tv_row_uses_live_network_channel(self):
        self.use(FakeAPI(search={"Netflix": ["Qn"]},
                         entities={"Qn": _ent("Netflix", handles=["Netflix"])}))
        row = app.create_tv_row("Test Show", metadata={"network": "Netflix"})
        self.assertEqual(row["youtube_channel_username"], "https://www.youtube.com/@Netflix|Test Show")

    def test_game_row_uses_live_publisher_channel(self):
        self.use(FakeAPI(search={"Devolver Digital": ["Qd"]},
                         entities={"Qd": _ent("Devolver Digital", handles=["DevolverDigital"])}))
        row = app.create_game_row("Test Game", metadata={"network": "Devolver Digital",
                                                         "developer": "Some Dev"})
        self.assertEqual(row["youtube_channel_username"],
                         "https://www.youtube.com/@DevolverDigital|Test Game")


if __name__ == "__main__":
    unittest.main()
