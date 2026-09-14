"""Release-date-first resolution for Wikipedia, the Wikidata item (and therefore
the social handles) and the video-game path. No network: every primitive that
would leave the machine is monkeypatched."""
import unittest
import metadata_fetcher as mf


class _Patch:
    """Set module attrs for the duration of a with-block, restore on exit."""
    def __init__(self, **kw):
        self.kw = kw
        self.old = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(mf, k)
            setattr(mf, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(mf, k, v)
        return False


def _id_claim(qid):
    return {"mainsnak": {"snaktype": "value",
                         "datavalue": {"value": {"id": qid}}}}


def _str_claim(v):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"value": v}}}


def _time_claim(year):
    return {"mainsnak": {"snaktype": "value",
                         "datavalue": {"value": {"time": "+%s-01-01T00:00:00Z" % year}}}}


def mk(qid, label, p31, year=None, imdb=None, aliases=(), sitelink=None, extra=None):
    """A Wikidata entity shaped the way the real API returns one, so the real
    _claim_values / _parse_time run in these tests."""
    claims = {"P31": [_id_claim(q) for q in p31]}
    if year:
        claims["P577"] = [_time_claim(year)]
    if imdb:
        claims["P345"] = [_str_claim(imdb)]
    for prop, vals in (extra or {}).items():
        claims[prop] = [_str_claim(v) for v in vals]
    return {"id": qid, "labels": {"en": {"value": label}},
            "aliases": {"en": [{"value": a} for a in aliases]},
            "sitelinks": ({"enwiki": {"title": sitelink}} if sitelink else {}),
            "claims": claims}


def _wire(entities):
    """Monkeypatch set that serves the given {qid: entity} map with no HTTP."""
    return dict(_entity=lambda qid: entities.get(qid),
                _labels=lambda qids: {},
                VALIDATE_URLS=False)


FILM = "Q11424"
GAME = "Q7889"


# --------------------------------------------------------------------------
class WikiLookupYear(unittest.TestCase):
    """A remake must not land on the original's article."""

    PAGES = ["Superman (1978 film)", "Superman (2025 film)", "Superman"]

    def _patch(self, entities=None):
        pages = self.PAGES

        def search(url, params=None, headers=None):
            if params and params.get("list") == "search":
                return {"query": {"search": [{"title": p} for p in pages]}}
            return {}
        return _Patch(_get_json=search, _page_qid=lambda pt: None,
                      _entity=lambda q: None, VALIDATE_URLS=False)

    def test_year_suffixed_article_wins(self):
        with self._patch():
            url, pt, qid, ok = mf.wiki_lookup("Superman", True, year=2025)
        self.assertEqual(pt, "Superman (2025 film)")
        self.assertTrue(ok)

    def test_original_article_when_that_year_is_asked_for(self):
        with self._patch():
            _, pt, _, ok = mf.wiki_lookup("Superman", True, year=1978)
        self.assertEqual(pt, "Superman (1978 film)")
        self.assertTrue(ok)

    def test_wrong_year_only_is_kept_but_flagged(self):
        self.PAGES = ["Superman (1978 film)"]
        try:
            with self._patch():
                _, pt, _, ok = mf.wiki_lookup("Superman", True, year=2025)
            self.assertEqual(pt, "Superman (1978 film)")
            self.assertFalse(ok)          # caller flags it for review
        finally:
            self.PAGES = WikiLookupYear.PAGES

    def test_no_year_behaves_as_before(self):
        with self._patch():
            _, pt, _, ok = mf.wiki_lookup("Superman", True)
        self.assertEqual(pt, "Superman (1978 film)")
        self.assertTrue(ok)


# --------------------------------------------------------------------------
class YearSpan(unittest.TestCase):
    def test_series_run_covers_every_year_in_it(self):
        ent = mk("Q1", "Show", [FILM])
        ent["claims"]["P580"] = [_time_claim(2019)]
        ent["claims"]["P582"] = [_time_claim(2023)]
        self.assertEqual(mf._entity_year_span(ent), (2019, 2023))
        self.assertTrue(mf._year_fits(ent, 2022))
        self.assertFalse(mf._year_fits(ent, 2015))

    def test_undated_item_is_never_rejected(self):
        self.assertIsNone(mf._year_fits(mk("Q1", "Upcoming", [FILM]), 2026))

    def test_a_films_single_date_collapses_to_one_year(self):
        self.assertEqual(mf._entity_year_span(mk("Q1", "F", [FILM], year=2025)),
                         (2025, 2025))


# --------------------------------------------------------------------------
class WikidataSocialsYear(unittest.TestCase):
    """The socials on the row come from the item, so the item must be the right
    year's title."""

    def _entities(self):
        return {
            "Q_OLD": mk("Q_OLD", "Superman", [FILM], year=1978,
                        extra={"P2003": ["superman1978"]}),
            "Q_NEW": mk("Q_NEW", "Superman", [FILM], year=2025,
                        extra={"P2003": ["superman2025"]}),
        }

    def test_right_years_item_supplies_the_handles(self):
        ents = self._entities()
        with _Patch(_search_candidates=lambda t, limit=6: ["Q_OLD", "Q_NEW"],
                    **_wire(ents)):
            meta = mf.wikidata_meta("Superman", is_movie=True, year=2025)
        self.assertEqual(meta.get("instagram_user"), "superman2025")
        self.assertNotIn("_wikidata_year_note", meta)

    def test_supplied_qid_from_the_wrong_year_is_replaced(self):
        ents = self._entities()
        with _Patch(_search_candidates=lambda t, limit=6: ["Q_OLD", "Q_NEW"],
                    **_wire(ents)):
            meta = mf.wikidata_meta("Superman", qid="Q_OLD", is_movie=True, year=2025)
        self.assertEqual(meta.get("instagram_user"), "superman2025")

    def test_only_wrong_year_available_is_kept_but_flagged(self):
        ents = {"Q_OLD": self._entities()["Q_OLD"]}
        with _Patch(_search_candidates=lambda t, limit=6: ["Q_OLD"], **_wire(ents)):
            meta = mf.wikidata_meta("Superman", is_movie=True, year=2025)
        self.assertEqual(meta.get("instagram_user"), "superman1978")
        self.assertIn("_wikidata_year_note", meta)

    def test_no_year_behaves_as_before(self):
        ents = self._entities()
        with _Patch(_search_candidates=lambda t, limit=6: ["Q_OLD", "Q_NEW"],
                    **_wire(ents)):
            meta = mf.wikidata_meta("Superman", is_movie=True)
        self.assertEqual(meta.get("instagram_user"), "superman1978")


# --------------------------------------------------------------------------
class GameEntityPicking(unittest.TestCase):
    """Tightened fallback + release-year disambiguation for video games."""

    def setUp(self):
        mf._CACHE.clear()

    def _run(self, entities, order, year_hint=""):
        off = dict(youtube_channel=lambda n: {},
                   verify_socials=lambda m, t=None, reject_foreign=False: None,
                   _resolve_metacritic_game=lambda t, candidate=None: "",
                   fetch_metacritic_game=lambda u: {},
                   wiki_lookup_game=lambda t, year=None: (None, None, None, True),
                   imdb_suggest_game=lambda t, year=None: (None, True))
        with _Patch(_search_candidates=lambda t, limit=8: order,
                    **dict(_wire(entities), **off)):
            return mf.fetch_game("Doom", year_hint=year_hint)

    def test_release_year_picks_the_reboot(self):
        ents = {"Q93": mk("Q93", "Doom", [GAME], year=1993,
                          extra={"P2003": ["doom1993"]}),
                "Q16": mk("Q16", "Doom", [GAME], year=2016,
                          extra={"P2003": ["doom2016"]})}
        meta = self._run(ents, ["Q93", "Q16"], year_hint="2016-05-13")
        self.assertEqual(meta.get("instagram_user"), "doom2016")

    def test_differently_named_game_is_no_longer_the_fallback(self):
        ents = {"QX": mk("QX", "Quake", [GAME], year=1996,
                         extra={"P2003": ["quake"]})}
        meta = self._run(ents, ["QX"])
        self.assertNotIn("instagram_user", meta)   # used to inherit Quake's

    def test_wrong_year_only_is_kept_but_flagged(self):
        ents = {"Q93": mk("Q93", "Doom", [GAME], year=1993,
                          extra={"P2003": ["doom1993"]})}
        meta = self._run(ents, ["Q93"], year_hint="2016")
        self.assertEqual(meta.get("instagram_user"), "doom1993")
        self.assertTrue(any("Social handles" in n for n in meta.get("_year_notes", [])))


# --------------------------------------------------------------------------
class GameFallbacks(unittest.TestCase):
    """Wikipedia and IMDb no longer depend on Wikidata alone."""

    def setUp(self):
        mf._CACHE.clear()

    def test_imdb_suggest_game_filters_to_video_games_and_year(self):
        d = {"d": [{"id": "tt0001", "l": "Doom", "qid": "movie", "y": 2005},
                   {"id": "tt0002", "l": "Doom", "qid": "videoGame", "y": 1993},
                   {"id": "tt0003", "l": "Doom", "qid": "videoGame", "y": 2016}]}
        with _Patch(_get_json=lambda url, params=None, headers=None: d):
            item, ok = mf.imdb_suggest_game("Doom", year=2016)
            self.assertEqual(item["id"], "tt0003")
            self.assertTrue(ok)
            item, ok = mf.imdb_suggest_game("Doom", year=2099)
            self.assertEqual(item["id"], "tt0002")   # kept...
            self.assertFalse(ok)                     # ...and flagged

    def test_imdb_suggest_game_ignores_a_different_title(self):
        d = {"d": [{"id": "tt0009", "l": "Doom Eternal", "qid": "videoGame", "y": 2020}]}
        with _Patch(_get_json=lambda url, params=None, headers=None: d):
            self.assertEqual(mf.imdb_suggest_game("Doom"), (None, True))

    def test_game_name_match_ranks_exact_over_prefix(self):
        exact = mk("Q1", "Doom", [GAME])
        prefix = mk("Q2", "Doom Eternal", [GAME])
        other = mk("Q3", "Quake", [GAME])
        with _Patch(**_wire({"Q1": exact, "Q2": prefix, "Q3": other})):
            self.assertEqual(mf._game_name_match(mf._entity("Q1"), "Doom"), 2)
            self.assertEqual(mf._game_name_match(mf._entity("Q2"), "Doom"), 1)
            self.assertEqual(mf._game_name_match(mf._entity("Q3"), "Doom"), 0)


# --------------------------------------------------------------------------
class MetacriticRottenTomatoesCandidateYear(unittest.TestCase):
    """A curated URL from another year no longer short-circuits the lookup."""

    def test_year_page_beats_a_curated_wrong_year_url(self):
        exists = {"https://www.metacritic.com/movie/superman-2025",
                  "https://www.metacritic.com/movie/superman-1978"}
        with _Patch(VALIDATE_URLS=True,
                    _url_status=lambda u: 200 if u.replace("http://", "https://").rstrip("/") in exists else 404):
            got = mf.resolve_metacritic(
                "Superman", True, candidate="http://www.metacritic.com/movie/superman-1978",
                curated=True, year=2025)
        self.assertEqual(got, "http://www.metacritic.com/movie/superman-2025/")

    def test_curated_url_kept_and_flagged_when_no_year_page_exists(self):
        notes = []
        with _Patch(VALIDATE_URLS=True, _url_status=lambda u: 200 if "superman-1978" in u else 404):
            got = mf.resolve_metacritic(
                "Superman", True, candidate="http://www.metacritic.com/movie/superman-1978",
                curated=True, year=2025, notes=notes)
        self.assertEqual(got, "http://www.metacritic.com/movie/superman-1978")
        self.assertTrue(notes and notes[0].startswith("Metacritic"))

    def test_rt_year_page_beats_a_curated_wrong_year_url(self):
        exists = {"https://www.rottentomatoes.com/m/superman_2025"}
        with _Patch(VALIDATE_URLS=True,
                    _url_status=lambda u: 200 if u.replace("http://", "https://").rstrip("/") in exists else 404):
            got = mf.resolve_rottentomatoes(
                "Superman", True, candidate="http://www.rottentomatoes.com/m/superman_1978",
                curated=True, year=2025)
        self.assertEqual(got, "http://www.rottentomatoes.com/m/superman_2025")

    def test_slug_year_is_read_from_the_last_segment_only(self):
        self.assertEqual(mf._slug_year("http://www.rottentomatoes.com/m/superman_2025"), 2025)
        self.assertEqual(mf._slug_year("http://www.metacritic.com/movie/superman-2025/"), 2025)
        self.assertIsNone(mf._slug_year("http://www.metacritic.com/movie/superman"))
        self.assertIsNone(mf._slug_year("http://www.rottentomatoes.com/m/blade_runner_2049_sequel"))


class ForeignHandleMarkers(unittest.TestCase):
    """The band/VEVO guard now runs on games too, so 'band' inside a longer
    word must not read as a band account."""

    def test_band_suffix_is_still_rejected(self):
        self.assertTrue(mf._handle_foreign_to_title("crawlersband", "Crawlers"))

    def test_publisher_handles_containing_band_are_kept(self):
        for h in ("bandainamcous", "bandcampgame", "contrabandgame"):
            self.assertFalse(mf._handle_foreign_to_title(h, "Whatever"), h)

    def test_marker_in_the_title_itself_is_allowed(self):
        self.assertFalse(mf._handle_foreign_to_title("thebandofficial", "The Band"))


if __name__ == "__main__":
    unittest.main()
