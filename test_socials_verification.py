"""Socials must belong to the movie/TV title, not a same-named band/artist.
Models the observed 'Crawlers' failure. No network (Wikidata/label/URL calls
are stubbed or disabled)."""
import unittest
import metadata_fetcher as mf

FILM, BAND, PERSON = "Q11424", "Q215380", "Q5"

def entity(p31=(), imdb=None, socials=None, sitelink=None):
    def snak(v): return {"mainsnak": {"snaktype": "value", "datavalue": {"value": v}}}
    c = {}
    if p31: c["P31"] = [snak({"id": q}) for q in p31]
    if imdb: c["P345"] = [snak(imdb)]
    for p, v in (socials or {}).items(): c[p] = [snak(v)]
    e = {"claims": c}
    if sitelink: e["sitelinks"] = {"enwiki": {"title": sitelink}}
    return e

# 'Crawlers': a band and a movie share the name
CRAWLERS_BAND = entity([BAND], socials={
    "P2013": "crawlersband", "P2003": "crawlersband", "P2002": "CrawlersHQ"},
    sitelink="Crawlers (band)")
CRAWLERS_FILM = entity([FILM], imdb="tt1234567", socials={
    "P2013": "CrawlersMovie", "P2003": "crawlersfilm", "P2002": "CrawlersFilm"},
    sitelink="Crawlers (film)")


class Verification(unittest.TestCase):
    def test_band_rejected(self): self.assertFalse(mf.entity_is_wanted_work(CRAWLERS_BAND, True))
    def test_film_accepted(self): self.assertTrue(mf.entity_is_wanted_work(CRAWLERS_FILM, True))
    def test_person_rejected(self): self.assertFalse(mf.entity_is_wanted_work(entity([PERSON]), True))
    def test_tt_conflict_rejected(self):
        self.assertFalse(mf.entity_is_wanted_work(CRAWLERS_FILM, True, tt="tt9999999"))
    def test_tt_match_accepted(self):
        self.assertTrue(mf.entity_is_wanted_work(CRAWLERS_FILM, True, tt="tt1234567"))


class WikidataGate(unittest.TestCase):
    def setUp(self):
        self._e, self._c, self._l = mf._entity, mf._search_candidates, mf._labels
        mf._labels = lambda q: {}
    def tearDown(self):
        mf._entity, mf._search_candidates, mf._labels = self._e, self._c, self._l

    def test_band_qid_yields_no_socials(self):
        mf._entity = lambda q: CRAWLERS_BAND
        mf._search_candidates = lambda t, limit=6: []
        m = mf.wikidata_meta("Crawlers", qid="Qband", is_movie=True)
        for k in ("twitter_handle", "instagram_user", "facebook_page"):
            self.assertNotIn(k, m)

    def test_film_qid_yields_the_movie_socials(self):
        mf._entity = lambda q: CRAWLERS_FILM
        mf._search_candidates = lambda t, limit=6: []
        m = mf.wikidata_meta("Crawlers", qid="Qfilm", is_movie=True, tt="tt1234567")
        self.assertEqual(m["twitter_handle"], "CrawlersFilm")
        self.assertEqual(m["instagram_user"], "crawlersfilm")

    def test_search_fallback_skips_band_takes_film(self):
        items = {"Qband": CRAWLERS_BAND, "Qfilm": CRAWLERS_FILM}
        mf._entity = lambda q: items.get(q)
        mf._search_candidates = lambda t, limit=6: ["Qband", "Qfilm"]
        m = mf.wikidata_meta("Crawlers", is_movie=True)
        self.assertEqual(m["twitter_handle"], "CrawlersFilm")

    def test_only_band_matches_yields_nothing(self):
        mf._entity = lambda q: CRAWLERS_BAND
        mf._search_candidates = lambda t, limit=6: ["Qband"]
        self.assertEqual(mf.wikidata_meta("Crawlers", is_movie=True), {})


class ForeignHandleNet(unittest.TestCase):
    def test_band_slug_is_foreign(self):
        self.assertTrue(mf._handle_foreign_to_title("crawlersband", "Crawlers"))
    def test_facebook_url_band_slug_is_foreign(self):
        self.assertTrue(mf._handle_foreign_to_title("http://www.facebook.com/crawlersband", "Crawlers"))
    def test_movie_slug_is_fine(self):
        self.assertFalse(mf._handle_foreign_to_title("crawlersmovie", "Crawlers"))
    def test_marker_in_title_is_allowed(self):
        self.assertFalse(mf._handle_foreign_to_title("thebandofficial", "The Band"))
    def test_vevo_and_topic_flagged(self):
        self.assertTrue(mf._handle_foreign_to_title("crawlersvevo", "Crawlers"))
        self.assertTrue(mf._handle_foreign_to_title("crawlerstopic", "Crawlers"))


class VerifySocialsNet(unittest.TestCase):
    def setUp(self):
        self._v = mf.VALIDATE_URLS
        mf.VALIDATE_URLS = False  # isolate the foreign-handle net from live checks
    def tearDown(self):
        mf.VALIDATE_URLS = self._v

    def test_movie_drops_band_handles(self):
        meta = {"twitter_handle": "CrawlersHQ", "instagram_user": "crawlersband",
                "facebook_page": "http://www.facebook.com/crawlersband"}
        mf.verify_socials(meta, "Crawlers", reject_foreign=True)
        self.assertNotIn("instagram_user", meta)   # 'band'
        self.assertNotIn("facebook_page", meta)     # 'band'
        self.assertIn("twitter_handle", meta)        # CrawlersHQ has no marker

    def test_talent_path_keeps_artist_handles(self):
        meta = {"instagram_user": "artistvevo", "twitter_handle": "TheBand"}
        mf.verify_socials(meta)  # reject_foreign defaults False
        self.assertIn("instagram_user", meta)
        self.assertIn("twitter_handle", meta)


if __name__ == "__main__":
    unittest.main()
