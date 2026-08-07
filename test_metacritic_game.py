"""Tests for the Metacritic sourcing added to the Video Game ingest.

    python -m unittest test_metacritic_game -v

Metacritic is the PRIMARY source for a game's developer, publisher (network) and
platforms: fetch_game resolves the game's Metacritic page for every game and its
values win for those three fields, with Wikidata kept as the fallback for
anything Metacritic doesn't list. The page is resolved via a curated Wikidata
URL, then the slugged title, then Metacritic's own search (for editions /
subtitles / odd punctuation whose slug doesn't match). Genre and release date
stay gap-fill.

Every test runs offline: the HTTP boundary (_get_html) and the URL liveness
check (_mc_alive) are monkeypatched, so no request ever leaves the machine.
"""
from __future__ import annotations

import unittest

import metadata_fetcher as mf


# --- realistic fixtures, one per parse path -------------------------------
# 1) Visible-label DOM (the layout WebFetch confirmed on the live page).
LABEL_HTML = """
<html><body>
<div class="c-pageProductGame_row">
  <div class="c-gameDetails_Developer">
    <span class="c-gameDetails_title">Developer:</span>
    <ul><li><a href="/company/refugium-games">Refugium Games</a></li></ul>
  </div>
  <div class="c-gameDetails_Distributor">
    <span class="c-gameDetails_title">Publisher:</span>
    <ul><li><a href="/company/refugium-games">Refugium Games</a></li></ul>
  </div>
  <div class="c-gameDetails_Platforms">
    <span class="c-gameDetails_title">Platforms:</span>
    <ul><li>PC</li><li>Xbox Series X</li><li>Nintendo Switch 2</li></ul>
  </div>
</div>
</body></html>
"""

# 2) schema.org VideoGame JSON-LD.
JSONLD_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"VideoGame",
 "name":"Agefield High: Rock the School",
 "author":{"@type":"Organization","name":"Refugium Games"},
 "publisher":[{"@type":"Organization","name":"Refugium Games"}],
 "gamePlatform":["PC","Xbox Series X","Nintendo Switch 2"],
 "genre":"Open-World Action",
 "datePublished":"2026-08-12"}
</script>
</head><body>no visible labels here</body></html>
"""

# 3) Next.js __NEXT_DATA__ blob, production.companies with typeName roles.
NEXT_HTML = """
<html><body>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"item":{
  "title":"Agefield High: Rock the School",
  "platforms":[{"name":"PC"},{"name":"Xbox Series X"},{"name":"Nintendo Switch 2"}],
  "genres":[{"name":"Open-World Action"}],
  "releaseDate":"2026-08-12",
  "production":{"companies":[
    {"name":"Refugium Games","typeName":"Developer"},
    {"name":"Refugium Games","typeName":"Publisher"}]}}}}}
</script>
</body></html>
"""


class _Patch:
    """Minimal monkeypatch: set module attrs, restore on exit."""

    def __init__(self, **attrs):
        self.attrs = attrs
        self.saved = {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self.saved[k] = getattr(mf, k)
            setattr(mf, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(mf, k, v)


class ScraperParsePathTests(unittest.TestCase):
    """Each of the three sources yields the same developer/publisher/platforms."""

    def _run(self, html):
        with _Patch(_get_html=lambda url: html):
            return mf.fetch_metacritic_game(
                "https://www.metacritic.com/game/agefield-high-rock-the-school/")

    def test_label_dom_parse(self):
        out = self._run(LABEL_HTML)
        self.assertEqual(out["developer"], "Refugium Games")
        self.assertEqual(out["network"], "Refugium Games")
        self.assertEqual(out["platforms"], ["PC", "Xbox Series X", "Nintendo Switch 2"])

    def test_jsonld_parse(self):
        out = self._run(JSONLD_HTML)
        self.assertEqual(out["developer"], "Refugium Games")
        self.assertEqual(out["network"], "Refugium Games")
        self.assertEqual(out["platforms"], ["PC", "Xbox Series X", "Nintendo Switch 2"])
        self.assertEqual(out["genre"], "Open-World Action")
        self.assertEqual(out["released_on"], "2026-08-12")

    def test_next_data_parse(self):
        out = self._run(NEXT_HTML)
        self.assertEqual(out["developer"], "Refugium Games")
        self.assertEqual(out["network"], "Refugium Games")
        self.assertEqual(out["platforms"], ["PC", "Xbox Series X", "Nintendo Switch 2"])
        self.assertEqual(out["genre"], "Open-World Action")

    def test_empty_html_fails_soft(self):
        with _Patch(_get_html=lambda url: ""):
            self.assertEqual(mf.fetch_metacritic_game("https://x/"), {})

    def test_network_error_fails_soft(self):
        def boom(url):
            raise RuntimeError("down")
        with _Patch(_get_html=boom):
            self.assertEqual(mf.fetch_metacritic_game("https://x/"), {})

    def test_blank_url_returns_empty(self):
        self.assertEqual(mf.fetch_metacritic_game(""), {})


class ResolverTests(unittest.TestCase):
    def test_slug_matches_metacritic_path(self):
        with _Patch(VALIDATE_URLS=False):
            self.assertEqual(
                mf._resolve_metacritic_game("Agefield High: Rock the School"),
                "https://www.metacritic.com/game/agefield-high-rock-the-school/")

    def test_curated_candidate_is_preferred_and_https(self):
        with _Patch(VALIDATE_URLS=False):
            self.assertEqual(
                mf._resolve_metacritic_game(
                    "Whatever", candidate="http://www.metacritic.com/game/akatori/"),
                "https://www.metacritic.com/game/akatori/")

    def test_verified_200_is_accepted(self):
        with _Patch(VALIDATE_URLS=True, _mc_alive=lambda u: True):
            self.assertTrue(mf._resolve_metacritic_game("Akatori").endswith("/game/akatori/"))

    def test_dead_slug_falls_through_to_empty_when_search_finds_nothing(self):
        with _Patch(VALIDATE_URLS=True, _mc_alive=lambda u: False,
                    _mc_search_game=lambda t: ""):
            self.assertEqual(mf._resolve_metacritic_game("No Such Game At All"), "")


class SearchFallbackTests(unittest.TestCase):
    """When the slugged URL 404s, Metacritic's own search finds the real page."""

    def test_search_used_when_slug_dead_and_hub_links_skipped(self):
        # results page lists a platform-hub link first, then the real game
        html = ('<a href="/game/pc/">PC</a>'
                '<a href="/game/the-real-game-2024/">The Real Game</a>')

        def alive(u):                       # slug URL dead; the search hit lives
            return "the-real-game-2024" in u

        with _Patch(VALIDATE_URLS=True, _mc_alive=alive, _get_html=lambda u: html):
            got = mf._resolve_metacritic_game("A Title That Slugs Wrong!! (Deluxe)")
        self.assertEqual(got, "https://www.metacritic.com/game/the-real-game-2024/")

    def test_search_returns_empty_when_no_results(self):
        with _Patch(VALIDATE_URLS=True, _mc_alive=lambda u: False,
                    _get_html=lambda u: "<div>no results found</div>"):
            self.assertEqual(mf._resolve_metacritic_game("Definitely Not A Game"), "")

    def test_search_ignores_only_platform_hubs(self):
        html = '<a href="/game/ps5/">PS5</a><a href="/game/switch-2/">Switch 2</a>'
        with _Patch(VALIDATE_URLS=True, _mc_alive=lambda u: True,
                    _get_html=lambda u: html):
            # every link is a hub -> nothing genuine to return
            self.assertEqual(mf._mc_search_game("Whatever"), "")

    def test_search_fails_soft_on_network_error(self):
        def boom(u):
            raise RuntimeError("down")
        with _Patch(VALIDATE_URLS=True, _get_html=boom):
            self.assertEqual(mf._mc_search_game("Whatever"), "")


class SourcingPolicyTests(unittest.TestCase):
    """Metacritic wins for developer/network/platforms; Wikidata is the fallback."""

    def _fake_wikidata(self, dev=None, pub=None, plats=None):
        """Patch the Wikidata primitives so fetch_game 'discovers' exactly the
        fields we pass -- and nothing else -- with no network. Metacritic is
        stubbed to a fixed page result unless a test overrides it."""
        cv = {"P31": ["Q7889"]}
        if dev:
            cv["P178"] = ["QDEV"]
        if pub:
            cv["P123"] = ["QPUB"]
        if plats:
            cv["P400"] = ["QP%d" % i for i in range(len(plats))]
        labels = {}
        if dev:
            labels["QDEV"] = dev
        if pub:
            labels["QPUB"] = pub
        if plats:
            labels.update({"QP%d" % i: p for i, p in enumerate(plats)})

        def fake_claim_values(claims, prop, **kw):
            return list(cv.get(prop, []))

        def fake_entity(qid):
            return {"claims": {}, "labels": {"en": {"value": self.title}},
                    "sitelinks": {}}

        return _Patch(
            _search_candidates=lambda name, limit=8: ["Q1"],
            _entity=fake_entity,
            _claim_values=fake_claim_values,
            _labels=lambda qids: labels,
            youtube_channel=lambda name: {},
            verify_socials=lambda meta: None,
            _resolve_metacritic_game=lambda title, candidate=None:
                "https://www.metacritic.com/game/agefield-high-rock-the-school/",
            fetch_metacritic_game=lambda url: {
                "developer": "MC Dev", "network": "MC Pub",
                "platforms": ["PC"], "genre": "MC Genre"},
        )

    def setUp(self):
        mf._CACHE.clear()

    def test_all_blank_fields_are_filled_from_metacritic(self):
        self.title = "Agefield High: Rock the School"
        with self._fake_wikidata():
            meta = mf.fetch_game(self.title)
        self.assertEqual(meta["developer"], "MC Dev")
        self.assertEqual(meta["network"], "MC Pub")
        self.assertEqual(meta["platforms"], ["PC"])
        self.assertTrue(meta["metacritic"].endswith("/game/agefield-high-rock-the-school/"))

    def test_metacritic_overrides_wikidata_for_the_three_fields(self):
        self.title = "Override Game"
        with self._fake_wikidata(dev="WD Dev", pub="WD Pub", plats=["Switch 2"]):
            meta = mf.fetch_game(self.title)
        self.assertEqual(meta["developer"], "MC Dev")   # Metacritic wins
        self.assertEqual(meta["network"], "MC Pub")     # Metacritic wins
        self.assertEqual(meta["platforms"], ["PC"])     # Metacritic wins

    def test_metacritic_is_consulted_even_when_wikidata_is_complete(self):
        self.title = "Complete Game"
        called = {"n": 0}

        def mc(url):
            called["n"] += 1
            return {"developer": "MC Dev", "network": "MC Pub", "platforms": ["PC"]}

        with self._fake_wikidata(dev="WD Dev", pub="WD Pub", plats=["PS5"]):
            with _Patch(fetch_metacritic_game=mc):
                meta = mf.fetch_game(self.title)
        self.assertEqual(called["n"], 1)                # now IS called
        self.assertEqual(meta["developer"], "MC Dev")

    def test_wikidata_is_kept_where_metacritic_lacks_a_field(self):
        self.title = "Partial MC Game"
        with self._fake_wikidata(dev="WD Dev", pub="WD Pub", plats=["PS5"]):
            with _Patch(fetch_metacritic_game=lambda url: {"platforms": ["PC"]}):
                meta = mf.fetch_game(self.title)
        self.assertEqual(meta["platforms"], ["PC"])     # Metacritic wins where present
        self.assertEqual(meta["developer"], "WD Dev")   # Wikidata kept where MC blank
        self.assertEqual(meta["network"], "WD Pub")

    def test_empty_scrape_does_not_store_a_metacritic_url(self):
        self.title = "Empty Scrape Game"
        with self._fake_wikidata(dev="WD Dev", pub="WD Pub", plats=["PS5"]):
            with _Patch(fetch_metacritic_game=lambda url: {}):
                meta = mf.fetch_game(self.title)
        self.assertNotIn("metacritic", meta)            # nothing scraped -> no URL kept
        self.assertEqual(meta["developer"], "WD Dev")   # Wikidata retained


class PlatformNormalisationTests(unittest.TestCase):
    """Wikidata/Metacritic platform labels normalise instead of being dropped."""

    def test_xbox_series_x_and_series_s_is_recognised(self):
        from app import _game_platform_lines
        self.assertEqual(_game_platform_lines(["Xbox Series X and Series S"]),
                         ["Platform - Xbox Series X"])

    def test_wikidata_labels_no_longer_drop_platforms(self):
        from app import _game_platform_lines
        # Clair Obscur's Wikidata labels used to collapse to just 'Platform - PC'
        self.assertEqual(
            _game_platform_lines(["Xbox Series X and Series S", "Microsoft Windows"]),
            ["Platform - PC", "Platform - Xbox Series X"])

    def test_playstation_5_pro_folds_to_ps5(self):
        from app import _game_platform_lines
        self.assertEqual(_game_platform_lines(["PlayStation 5 Pro"]), ["Platform - PS5"])


class EndToEndRowTests(unittest.TestCase):
    """The scraped values flow into a clean ingest row -- no «CONFIRM» flags."""

    def test_sample_game_row_has_no_confirm_placeholders(self):
        from app import (create_game_row, GAME_CONFIRM_DEVELOPER,
                         GAME_CONFIRM_PLATFORMS, GAME_CONFIRM_NETWORK)
        # what fetch_metacritic_game(LABEL_HTML) yields for the sample game
        meta = {"developer": "Refugium Games", "network": "Refugium Games",
                "platforms": ["PC", "Xbox Series X", "Nintendo Switch 2"]}
        row = create_game_row("Agefield High: Rock the School - DAR", meta)
        self.assertEqual(
            row["title_sub_category"],
            "Developer - Refugium Games\nPlatform - PC\n"
            "Platform - Switch 2\nPlatform - Xbox Series X")
        self.assertEqual(row["network"], "Refugium Games")
        for flag in (GAME_CONFIRM_DEVELOPER, GAME_CONFIRM_PLATFORMS, GAME_CONFIRM_NETWORK):
            self.assertNotIn(flag, row["title_sub_category"])
            self.assertNotIn(flag, row["network"])


if __name__ == "__main__":
    unittest.main()
