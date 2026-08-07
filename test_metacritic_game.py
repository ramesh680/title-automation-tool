"""Tests for the Metacritic gap-fill added to the Video Game ingest.

    python -m unittest test_metacritic_game -v

Wikidata has no entry for a brand-new game (e.g. 'Agefield High: Rock the
School', out 2026-08-12), so fetch_game used to leave developer / publisher /
platforms blank and the ingest flagged each with a «CONFIRM ...» placeholder.
Metacritic lists all three, so fetch_game now scrapes the game page to fill ONLY
the fields Wikidata left empty -- never to override a value Wikidata supplied.

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

    def test_dead_slug_is_rejected(self):
        with _Patch(VALIDATE_URLS=True, _mc_alive=lambda u: False):
            self.assertEqual(mf._resolve_metacritic_game("No Such Game At All"), "")


class GapFillPolicyTests(unittest.TestCase):
    """fetch_game copies a Metacritic value across ONLY when its own is blank."""

    def _fake_wikidata(self, dev=None, pub=None, plats=None):
        """Patch the Wikidata primitives so fetch_game 'discovers' exactly the
        fields we pass -- and nothing else -- with no network."""
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
        # a URL discovered during gap-fill is kept
        self.assertTrue(meta["metacritic"].endswith("/game/agefield-high-rock-the-school/"))

    def test_wikidata_developer_is_not_overridden(self):
        self.title = "Partial Game"
        with self._fake_wikidata(dev="WD Dev"):
            meta = mf.fetch_game(self.title)
        self.assertEqual(meta["developer"], "WD Dev")     # kept, not overridden
        self.assertEqual(meta["network"], "MC Pub")       # gap filled
        self.assertEqual(meta["platforms"], ["PC"])       # gap filled

    def test_metacritic_is_not_called_when_wikidata_is_complete(self):
        self.title = "Complete Game"
        called = {"n": 0}

        def _should_not_run(url):
            called["n"] += 1
            return {}

        with self._fake_wikidata(dev="WD Dev", pub="WD Pub", plats=["PS5"]):
            with _Patch(fetch_metacritic_game=_should_not_run):
                meta = mf.fetch_game(self.title)
        self.assertEqual(called["n"], 0)
        self.assertEqual(meta["developer"], "WD Dev")
        self.assertEqual(meta["network"], "WD Pub")
        self.assertEqual(meta["platforms"], ["PS5"])


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
