"""Stakeholder feedback, Sep 2026 -- tested on 'The Rescue' (2027, Paramount).

1. record_type / title_created_date / youtube_channel_company are generated at
   ingest -> not exported.
2. 'Paramount Pictures' is not a network in our database -> 'Paramount'.
3. Release scale must be confirmed on Rotten Tomatoes / Metacritic / the
   distributor, not Box Office Mojo alone (BOM said Limited, RT says Wide).
4. Socials the studio publishes (trailer description) must be captured, and a
   handle used on one platform is probed on the others.
5. Same-named titles: all candidates are offered; '(YYYY)' narrows to one and
   RT / Metacritic pages dated to another year are rejected.

Every network call is mocked.
"""
import io
import unittest
from unittest import mock

import pandas as pd

import app
import metadata_fetcher as mf
import source_pages as sp

TRAILER_DESC = """Watch the official trailer for The Rescue.
#TheRescueMovie
Instagram: https://www.instagram.com/therescuemovie/
Facebook: https://www.facebook.com/therescuemovie
X: https://x.com/therescuemovie
TikTok: https://www.tiktok.com/@therescuemovie
Follow Paramount Pictures: https://www.instagram.com/paramountpics https://x.com/ParamountPics"""

RT_HTML_2027 = """<html><head><title>The Rescue | Rotten Tomatoes</title></head><body>
<dl><dt><rt-text>Director</rt-text></dt><dd>Potsy Ponciroli</dd>
<dt><rt-text>Distributor</rt-text></dt><dd><rt-link>Paramount Pictures</rt-link></dd>
<dt><rt-text>Production Co</rt-text></dt><dd>Paramount Pictures, Down Home</dd>
<dt><rt-text>Genre</rt-text></dt><dd>Action, Drama</dd>
<dt><rt-text>Release Date (Theaters)</rt-text></dt><dd><rt-text>Jan 29, 2027</rt-text><rt-text>, Wide</rt-text></dd>
<dt><rt-text>Runtime</rt-text></dt><dd>1h 50m</dd></dl></body></html>"""

RT_HTML_2021 = """<html><body><b>Distributor:</b> National Geographic
<b>Release Date (Theaters):</b> Oct 8, 2021&nbsp;limited <b>Runtime:</b> 1h 47m</body></html>"""

MC_HTML_2027 = """<html><script type="application/ld+json">{"@type":"Movie","name":"The Rescue",
"datePublished":"2027-01-29"}</script><body>Release Date: Jan 29, 2027</body></html>"""


class ColumnsRemoved(unittest.TestCase):
    GONE = ("record_type", "title_created_date", "youtube_channel_company")

    def test_schemas_drop_ingest_generated_columns(self):
        for cols in (app.COLUMNS, app.TV_COLUMNS, app.PUBLISHER_COLUMNS):
            for c in self.GONE:
                self.assertNotIn(c, cols)
        self.assertEqual(len(app.COLUMNS), 39)

    def test_exported_movie_sheet_has_none_of_them(self):
        row = app.create_row("The Rescue", True, "Paramount",
                             {"released_on": "2027-01-29", "genre": "Drama"})
        wb = app._rows_to_workbook([row])
        df = pd.read_excel(io.BytesIO(wb.getvalue()), sheet_name=0)
        for c in self.GONE:
            self.assertNotIn(c, df.columns)
        # the company channel still feeds the username variants
        self.assertIn("youtube.com", str(df["youtube_channel_username"][0]))


class NetworkMapsToDatabase(unittest.TestCase):
    def test_paramount_pictures_is_paramount(self):
        for raw in ("Paramount Pictures", "Paramount Pictures Releasing", "paramount"):
            self.assertEqual(app.resolve_network(raw, True), ("Paramount", True))

    def test_common_spellings(self):
        cases = {"Walt Disney Studios Motion Pictures": "Disney",
                 "Sony Pictures Releasing": "Sony / Columbia",
                 "A24 Films": "A24", "Neon Rated LLC": "Neon",
                 "Warner Bros. Pictures": "Warner Bros.",
                 "Focus Features": "Focus Features"}
        for raw, want in cases.items():
            self.assertEqual(app.resolve_network(raw, True)[0], want, raw)

    def test_tv_spellings(self):
        self.assertEqual(app.resolve_network("Max", False)[0], "HBO Max")
        self.assertEqual(app.resolve_network("Prime Video", False)[0], "Amazon Prime Video")

    def test_unknown_studio_is_flagged(self):
        row = app.create_row("Some Film", True, "",
                             {"network": "Totally Unknown Films", "released_on": "2026-01-01",
                              "genre": "Drama"})
        self.assertTrue(row.get("_needs_review"))
        self.assertIn("not in the studio database", row["_review_reason"])

    def test_row_for_paramount_title(self):
        row = app.create_row("The Rescue", True, "",
                             {"network": "Paramount Pictures", "released_on": "2027-01-29",
                              "genre": "Drama", "release_scale": "Wide"})
        self.assertEqual(row["network"], "Paramount")
        self.assertEqual(row["title_sub_category"], "Release - Wide\nStudio - Major")
        self.assertIn("Wide Release", row["brand_set"])
        self.assertFalse("not in the studio database" in str(row.get("_review_reason")))


class ReleaseScaleConsensus(unittest.TestCase):
    def test_rt_parse(self):
        info = sp.parse_rottentomatoes(RT_HTML_2027)
        self.assertEqual(info["scale"], "Wide")
        self.assertEqual(info["year"], 2027)
        self.assertEqual(info["distributor"], "Paramount Pictures")
        old = sp.parse_rottentomatoes(RT_HTML_2021)
        self.assertEqual((old["year"], old["scale"]), (2021, "Limited"))

    def test_rt_beats_bom_alone(self):
        scale, note = sp.release_scale_consensus({"rottentomatoes": "Wide",
                                                  "boxofficemojo": "Limited"})
        self.assertEqual(scale, "Wide")
        self.assertIn("disagree", note)

    def test_majority(self):
        scale, _ = sp.release_scale_consensus({"rottentomatoes": "Limited",
                                               "distributor": "Wide",
                                               "boxofficemojo": "Wide"})
        self.assertEqual(scale, "Wide")

    def test_official_site_wording(self):
        self.assertEqual(sp.parse_official_site_scale("<p>In select theaters Friday</p>"), "Limited")
        self.assertEqual(sp.parse_official_site_scale("<p>In theaters everywhere Jan 29</p>"), "Wide")
        self.assertEqual(sp.parse_official_site_scale("<p>Only in theaters</p>"), "")

    def test_cross_check_uses_rt(self):
        meta = {"rottentomatoes": "http://www.rottentomatoes.com/m/the_rescue_2027",
                "release_scale": "Limited", "network": "Paramount Pictures"}
        notes = []
        with mock.patch.object(mf, "_page", side_effect=lambda u: RT_HTML_2027 if "rotten" in u else ""):
            mf._cross_check(meta, True, 2027, notes)
        self.assertEqual(meta["release_scale"], "Wide")
        self.assertTrue(any("disagree" in n for n in notes))

    def test_cross_check_fills_missing_network_from_rt(self):
        meta = {"rottentomatoes": "http://www.rottentomatoes.com/m/the_rescue_2027"}
        with mock.patch.object(mf, "_page", side_effect=lambda u: RT_HTML_2027 if "rotten" in u else ""):
            mf._cross_check(meta, True, 2027, [])
        self.assertEqual(meta["network"], "Paramount Pictures")
        self.assertEqual(meta["released_on"], "2027-01-29")

    def test_major_without_signal_defaults_wide(self):
        row = app.create_row("X", True, "", {"network": "Paramount", "released_on": "2027-01-01",
                                             "genre": "Drama"})
        self.assertTrue(row["title_sub_category"].startswith("Release - Wide"))


class SocialDiscovery(unittest.TestCase):
    def setUp(self):
        self._flags = (mf.SOCIAL_DISCOVERY, mf.SOCIAL_GUESSING, mf.VALIDATE_URLS)
        mf.SOCIAL_DISCOVERY = mf.SOCIAL_GUESSING = mf.VALIDATE_URLS = True

    def tearDown(self):
        mf.SOCIAL_DISCOVERY, mf.SOCIAL_GUESSING, mf.VALIDATE_URLS = self._flags

    def test_links_parsed_and_studio_handles_dropped(self):
        picks = sp.pick_title_handles(sp.extract_social_links(TRAILER_DESC), "The Rescue", 2027)
        self.assertEqual(picks, {"instagram": "therescuemovie", "facebook": "therescuemovie",
                                 "twitter": "therescuemovie", "tiktok": "therescuemovie"})

    def test_netflix_url_is_not_an_x_handle(self):
        links = sp.extract_social_links("https://www.netflix.com/title/81234567")
        self.assertNotIn("twitter", links)

    def test_trailer_description_fills_all_four(self):
        meta = {"_trailer_keys": ["abc123def45"]}
        with mock.patch.object(mf, "youtube_descriptions",
                               return_value=[("abc123def45", "The Rescue | Trailer", TRAILER_DESC)]), \
                mock.patch.object(mf, "_page", return_value=""), \
                mock.patch.object(mf, "probe_handle", return_value=(None, "")):
            mf._discover_socials(meta, "The Rescue", 2027, [], network="Paramount Pictures")
        self.assertEqual(meta["instagram_user"], "therescuemovie")
        self.assertEqual(meta["facebook_page"], "http://www.facebook.com/therescuemovie")
        self.assertEqual(meta["twitter_handle"], "therescuemovie")
        self.assertEqual(meta["tiktok_user"], "therescuemovie")

    def test_no_trailer_on_tmdb_searches_youtube(self):
        meta = {}
        with mock.patch.object(mf, "youtube_trailer_ids", return_value=["v1"]) as s, \
                mock.patch.object(mf, "youtube_descriptions",
                                  return_value=[("v1", "The Rescue - Official Trailer", TRAILER_DESC)]), \
                mock.patch.object(mf, "_page", return_value=""):
            mf._discover_socials(meta, "The Rescue", 2027, [])
        s.assert_called_once()
        self.assertEqual(meta["tiktok_user"], "therescuemovie")

    def test_handle_propagates_to_missing_platforms(self):
        # only Instagram known (e.g. Wikidata); the same handle is probed elsewhere
        meta = {"instagram_user": "therescuemovie"}

        def probe(plat, h):
            return (True, "The Rescue") if h == "therescuemovie" else (False, "")
        notes = []
        with mock.patch.object(mf, "youtube_trailer_ids", return_value=[]), \
                mock.patch.object(mf, "youtube_descriptions", return_value=[]), \
                mock.patch.object(mf, "_page", return_value=""), \
                mock.patch.object(mf, "probe_handle", side_effect=probe):
            mf._discover_socials(meta, "The Rescue", 2027, notes)
        self.assertEqual(meta["twitter_handle"], "therescuemovie")
        self.assertEqual(meta["tiktok_user"], "therescuemovie")
        self.assertEqual(meta["facebook_page"], "http://www.facebook.com/therescuemovie")
        self.assertTrue(all("verify" in n for n in notes))

    def test_pattern_guess_needs_matching_profile_name(self):
        meta = {}
        with mock.patch.object(mf, "youtube_trailer_ids", return_value=[]), \
                mock.patch.object(mf, "youtube_descriptions", return_value=[]), \
                mock.patch.object(mf, "_page", return_value=""), \
                mock.patch.object(mf, "probe_handle", return_value=(True, "Some Rescue Dog Shelter Inc")):
            mf._discover_socials(meta, "Zorblax", 2027, [])
        self.assertNotIn("instagram_user", meta)

    def test_official_source_replaces_stale_handle_with_note(self):
        meta = {"instagram_user": "rescuedocumentary", "_trailer_keys": ["k"]}
        notes = []
        with mock.patch.object(mf, "youtube_descriptions", return_value=[("k", "t", TRAILER_DESC)]), \
                mock.patch.object(mf, "_page", return_value=""), \
                mock.patch.object(mf, "probe_handle", return_value=(None, "")):
            mf._discover_socials(meta, "The Rescue", 2027, notes)
        self.assertEqual(meta["instagram_user"], "therescuemovie")
        self.assertTrue(any("replaced" in n for n in notes))

    def test_imdb_official_sites(self):
        html = ('{"officialSites":{"edges":[{"node":{"url":"https:\\/\\/www.instagram.com\\/'
                'therescuemovie\\/","label":"Instagram"}},{"node":{"url":"https:\\/\\/www.'
                'paramountmovies.com\\/movies\\/the-rescue"}}]}}')
        self.assertEqual(sp.imdb_official_socials(html)["instagram"], ["therescuemovie"])
        self.assertIn("paramountmovies.com", sp.homepage_from_imdb(html))


SUGGEST = [
    {"id": "tt35606013", "l": "The Rescue", "y": 2027, "qid": "movie", "s": "Brandon Sklenar, Josh Lucas"},
    {"id": "tt11772812", "l": "The Rescue", "y": 2021, "qid": "movie", "s": "Documentary"},
    {"id": "tt7456436", "l": "The Rescue", "y": 2020, "qid": "movie", "s": "Eddie Peng"},
    {"id": "tt0096000", "l": "The Rescue", "y": 1988, "qid": "movie", "s": "Kevin Dillon"},
    {"id": "tt9999999", "l": "The Rescue", "y": 2019, "qid": "tvSeries", "s": "Some show"},
    {"id": "tt1111111", "l": "The Rescuers", "y": 1977, "qid": "movie", "s": "Bob Newhart"},
]


class SameNamedTitles(unittest.TestCase):
    def test_all_candidates_newest_first(self):
        with mock.patch.object(mf, "_imdb_suggest_raw", return_value=SUGGEST):
            c = mf.title_candidates("The Rescue", True)
        self.assertEqual([x["year"] for x in c], [2027, 2021, 2020, 1988])  # no TV, no 'Rescuers'
        self.assertEqual(c[0]["tt"], "tt35606013")

    def test_bracket_year_narrows(self):
        with mock.patch.object(mf, "_imdb_suggest_raw", return_value=SUGGEST):
            c = mf.title_candidates("The Rescue (2021)", True)
        self.assertEqual([x["tt"] for x in c], ["tt11772812"])

    def test_tv_candidates(self):
        with mock.patch.object(mf, "_imdb_suggest_raw", return_value=SUGGEST):
            c = mf.title_candidates("The Rescue", False)
        self.assertEqual([x["tt"] for x in c], ["tt9999999"])

    def test_rt_page_dated_to_another_year_is_rejected(self):
        pages = {"https://www.rottentomatoes.com/m/the_rescue": RT_HTML_2021,
                 "https://www.rottentomatoes.com/m/the_rescue_2027": RT_HTML_2027}
        with mock.patch.object(mf, "_rt_alive", side_effect=lambda u: u.replace("http://", "https://") in pages), \
                mock.patch.object(mf, "_page", side_effect=lambda u: pages.get(u.replace("http://", "https://"), "")), \
                mock.patch.object(mf, "VALIDATE_URLS", True):
            self.assertEqual(mf.resolve_rottentomatoes("The Rescue", True, year=2027),
                             "http://www.rottentomatoes.com/m/the_rescue_2027")
            self.assertEqual(mf.resolve_rottentomatoes("The Rescue", True, year=2021),
                             "http://www.rottentomatoes.com/m/the_rescue")
            # a curated URL whose page is the 2021 film is not accepted for 2027
            self.assertEqual(mf.resolve_rottentomatoes(
                "The Rescue", True, candidate="http://www.rottentomatoes.com/m/the_rescue",
                curated=True, year=2027), "http://www.rottentomatoes.com/m/the_rescue_2027")

    def test_mc_page_year_check(self):
        with mock.patch.object(mf, "_page", return_value=MC_HTML_2027):
            self.assertTrue(mf._page_year_conflict("http://x/movie/the-rescue/", 2021, "mc"))
            self.assertFalse(mf._page_year_conflict("http://x/movie/the-rescue/", 2027, "mc"))

    def test_no_year_uses_resolved_imdb_year_and_lists_others(self):
        captured = {}

        def enrich(tt, is_movie, title, wikidata_id=None, want_year=None):
            captured.update(tt=tt, year=want_year)
            return {"imdb_id": "http://www.imdb.com/title/" + tt, "released_on": "2027-01-29"}
        mf._CACHE.clear()
        with mock.patch.object(mf, "_imdb_suggest_raw", return_value=SUGGEST), \
                mock.patch.object(mf, "_upcoming_index", return_value={"by_title": {}, "by_tt": {}}), \
                mock.patch.object(mf, "_enrich_by_tt", side_effect=enrich):
            meta = mf.fetch_metadata("The Rescue", True)
        self.assertEqual(captured, {"tt": "tt35606013", "year": 2027})
        note = " ".join(meta.get("_year_notes") or [])
        self.assertIn("4 titles are named", note)
        self.assertIn("tt11772812", note)
        # ...and the note reaches the Needs Review sheet
        row = app.create_row("The Rescue", True, "", dict(meta, genre="Drama"))
        self.assertIn("4 titles are named", row["_review_reason"])

    def test_bracket_year_picks_that_title(self):
        captured = {}

        def enrich(tt, is_movie, title, wikidata_id=None, want_year=None):
            captured.update(tt=tt, year=want_year, title=title)
            return {"imdb_id": "http://www.imdb.com/title/" + tt}
        mf._CACHE.clear()
        with mock.patch.object(mf, "_imdb_suggest_raw", return_value=SUGGEST), \
                mock.patch.object(mf, "_upcoming_index", return_value={"by_title": {}, "by_tt": {}}), \
                mock.patch.object(mf, "_enrich_by_tt", side_effect=enrich), \
                mock.patch.object(mf, "omdb_by_id", return_value={}):
            meta = mf.fetch_metadata("The Rescue (2021)", True)
        self.assertEqual(captured, {"tt": "tt11772812", "year": 2021, "title": "The Rescue"})
        self.assertFalse(any("titles are named" in n for n in meta.get("_year_notes") or []))

    def test_candidates_endpoint(self):
        client = app.app.test_client()
        with mock.patch.object(mf, "_imdb_suggest_raw", return_value=SUGGEST):
            r = client.post("/api/candidates", json={
                "titles": ["The Rescue", "The Rescue (2027)", "LeBron James"],
                "titles_type": {"The Rescue": "movie", "The Rescue (2027)": "movie",
                                "LeBron James": "talent"}})
        c = r.get_json()["candidates"]
        self.assertEqual(list(c), ["The Rescue"])
        self.assertEqual(len(c["The Rescue"]), 4)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Same-name picker extended to Video Games and Talent (Sep 2026)
# ---------------------------------------------------------------------------
def _ent(qid, label, p31, desc="", props=None, aliases=(), sitelinks=None):
    claims = {"P31": [{"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": q}}}} for q in p31]}
    for p, vals in (props or {}).items():
        claims[p] = []
        for v in vals:
            if isinstance(v, str) and v.startswith("+"):
                dv = {"time": v}
            elif isinstance(v, str) and v.startswith("Q"):
                dv = {"id": v}
            else:
                dv = v
            claims[p].append({"mainsnak": {"snaktype": "value", "datavalue": {"value": dv}}})
    return {"id": qid, "labels": {"en": {"value": label}},
            "aliases": {"en": [{"value": a} for a in aliases]},
            "descriptions": {"en": {"value": desc}}, "claims": claims,
            "sitelinks": sitelinks or {}}


GAMES = {
    "Q1": _ent("Q1", "Doom", ["Q7889"], "1993 video game", {"P577": ["+1993-12-10T00:00:00Z"]}),
    "Q2": _ent("Q2", "Doom", ["Q7889"], "2016 video game", {"P577": ["+2016-05-13T00:00:00Z"]}),
    "Q3": _ent("Q3", "Doom", ["Q11424"], "2005 film", {"P577": ["+2005-10-21T00:00:00Z"]}),
    "Q4": _ent("Q4", "Doom Eternal", ["Q7889"], "2020 video game", {"P577": ["+2020-03-20T00:00:00Z"]}),
}
GAME_SUGGEST = [
    {"id": "tt0286598", "l": "Doom", "y": 1993, "qid": "videoGame"},
    {"id": "tt4978540", "l": "Doom", "y": 2016, "qid": "videoGame"},
    {"id": "tt0419706", "l": "Doom", "y": 2005, "qid": "movie"},
]
PEOPLE = {
    "Q10": _ent("Q10", "Michael B. Jordan", ["Q5"], "American actor",
                {"P569": ["+1987-02-09T00:00:00Z"], "P106": ["Q33999"], "P345": ["nm0430107"]},
                sitelinks={"enwiki": {}, "frwiki": {}}),
    "Q11": _ent("Q11", "Michael B. Jordan", ["Q5"], "American basketball player",
                {"P569": ["+1990-01-01T00:00:00Z"], "P106": ["Q3665646"]}),
    "Q12": _ent("Q12", "Michael Jordan", ["Q5"], "basketball player"),
    "Q13": _ent("Q13", "Michael B. Jordan", ["Q515"], "a city"),
}


class GameAndTalentCandidates(unittest.TestCase):
    def test_game_candidates_by_year(self):
        with mock.patch.object(mf, "_search_candidates", return_value=list(GAMES)), \
                mock.patch.object(mf, "_entity", side_effect=GAMES.get), \
                mock.patch.object(mf, "_imdb_suggest_raw", return_value=GAME_SUGGEST):
            c = mf.game_candidates("Doom")
            one = mf.game_candidates("Doom (2016)")
        self.assertEqual([(x["year"], x["qid"], x["tt"]) for x in c],
                         [(2016, "Q2", "tt4978540"), (1993, "Q1", "tt0286598")])
        self.assertEqual([x["qid"] for x in one], ["Q2"])

    def test_talent_candidates(self):
        with mock.patch.object(mf, "_search_candidates", return_value=list(PEOPLE)), \
                mock.patch.object(mf, "_entity", side_effect=PEOPLE.get), \
                mock.patch.object(mf, "_labels", return_value={"Q33999": "actor",
                                                               "Q3665646": "basketball player"}):
            c = mf.talent_candidates("Michael B. Jordan")
        self.assertEqual([x["qid"] for x in c], ["Q10", "Q11"])   # most notable first
        self.assertEqual(c[0]["nm"], "nm0430107")
        self.assertEqual(c[0]["born"], 1987)
        self.assertEqual(c[1]["occupations"], ["basketball player"])

    def test_endpoint_games_multi_talent_single(self):
        client = app.app.test_client()
        with mock.patch.object(app, "game_candidates", return_value=[
                    {"qid": "Q2", "tt": "tt4978540", "year": 2016, "kind": "Video Game", "stars": ""},
                    {"qid": "Q1", "tt": "tt0286598", "year": 1993, "kind": "Video Game", "stars": ""}]), \
                mock.patch.object(app, "talent_candidates", return_value=[
                    {"qid": "Q10", "title": "Michael B. Jordan", "born": 1987, "description": "American actor",
                     "occupations": ["actor"], "nm": "nm0430107",
                     "imdb_url": "https://www.imdb.com/name/nm0430107/"},
                    {"qid": "Q11", "title": "Michael B. Jordan", "born": 1990,
                     "description": "American basketball player", "occupations": [], "nm": "",
                     "imdb_url": "", "wikidata_url": "https://www.wikidata.org/wiki/Q11"}]):
            r = client.post("/api/candidates", json={
                "titles": ["Doom", "Michael B. Jordan", "Chris Evans", "Doom 2 (1994)"],
                "titles_type": {"Doom": "game", "Michael B. Jordan": "talent",
                                "Chris Evans": "talent", "Doom 2 (1994)": "game"},
                "professions": {"Chris Evans": "actor"}})
        j = r.get_json()
        self.assertEqual(set(j["candidates"]), {"Doom", "Michael B. Jordan"})
        self.assertEqual(j["modes"], {"Doom": "multi", "Michael B. Jordan": "single"})
        doom = j["candidates"]["Doom"][0]
        self.assertEqual((doom["suffix"], doom["pick"]), (" (2016)", {"tt": "tt4978540", "qid": "Q2"}))
        mbj = j["candidates"]["Michael B. Jordan"][0]
        self.assertEqual((mbj["suffix"], mbj["pick"]), ("", {"qid": "Q10"}))
        self.assertIn("born 1987", mbj["detail"])

    def test_picks_pin_the_entity(self):
        with mock.patch.object(app, "fetch_person", return_value={"imdb_id": "x"}) as fp, \
                mock.patch.object(app, "fetch_game", return_value={}) as fg:
            rows = app.build_rows_from_titles({
                "titles": ["Michael B. Jordan", "Doom (2016)"], "autoFetch": True,
                "titles_type": {"Michael B. Jordan": "talent", "Doom (2016)": "game"},
                "picks": {"Michael B. Jordan": {"qid": "Q10"},
                          "Doom (2016)": {"qid": "Q2", "tt": "tt4978540"}}})
        self.assertEqual(fp.call_args.kwargs["qid"], "Q10")
        self.assertEqual(fg.call_args.kwargs["qid"], "Q2")
        game_rows = [r for r in rows if "Doom" in r.get("title", "")]
        self.assertTrue(all("tt4978540" in str(r.get("imdb_id")) for r in game_rows))

    def test_bad_pick_ids_ignored(self):
        with mock.patch.object(app, "fetch_person", return_value={}) as fp:
            app.build_rows_from_titles({"titles": ["X Y"], "autoFetch": True,
                                        "titles_type": {"X Y": "talent"},
                                        "picks": {"X Y": {"qid": "DROP TABLE"}}})
        self.assertIsNone(fp.call_args.kwargs["qid"])


# ---------------------------------------------------------------------------
# Review page brought up to date (Sep 2026)
# ---------------------------------------------------------------------------
def _xlsx(sheets):
    b = io.BytesIO()
    with pd.ExcelWriter(b) as w:
        for n, d in sheets.items():
            pd.DataFrame(d).to_excel(w, sheet_name=n, index=False)
    return b.getvalue()


class ReviewUpdated(unittest.TestCase):
    MOVIE = {"title": "The Rescue", "title_category": "Movies", "network": "Paramount Pictures",
             "released_on": "2027-01-29", "genre": "Drama", "primary_genre": "Drama",
             "imdb_id": "http://www.imdb.com/title/tt35606013"}

    def _review(self, sheets, meta=None, auto=True):
        with mock.patch.object(app, "fetch_metadata_by_tt", return_value=dict(meta or {})), \
                mock.patch.object(app, "fetch_metadata", return_value=dict(meta or {})), \
                mock.patch.object(app, "fetch_game", return_value={}), \
                mock.patch.object(app, "fetch_person", return_value={}), \
                mock.patch.object(app, "fetch_brand", return_value={}):
            out, summ = app.build_review((_xlsx(sheets), "f.xlsx"), auto_fetch=auto)
        return pd.read_excel(io.BytesIO(out), sheet_name=None), summ

    def test_every_sheet_is_reviewed(self):
        book, summ = self._review({
            "Movies": [self.MOVIE],
            "Video Games": [{"title": "Doom", "title_category": "Video Games"}],
            "Needs Review": [{"title": "x", "why_flagged": "y"}]}, auto=False)
        self.assertIn("Reviewed - Movies", book)
        self.assertIn("Reviewed - Video Games", book)
        self.assertNotIn("Reviewed - Needs Review", book)
        self.assertEqual(summ["rows"], 2)
        self.assertIn("Sheet", book["Findings"].columns)

    def test_non_database_network_is_flagged(self):
        book, _ = self._review({"Movies": [self.MOVIE]}, meta={"network": "Paramount Pictures"})
        f = book["Findings"]
        row = f[f["Column"] == "network"].iloc[0]
        self.assertEqual((row["Type"], row["Suggested Value"]), ("Mismatch", "Paramount"))

    def test_discovery_notes_are_verify_and_keep_values(self):
        meta = {"network": "Paramount", "imdb_id": "http://www.imdb.com/title/tt35606013",
                "_year_notes": ["Release scale: sources disagree (Wide: Rotten Tomatoes; "
                                "Limited: Box Office Mojo) -- used Wide, verify.",
                                "Social handles: TikTok 'therescuemovie' was not listed by any source"],
                "_imdb_year_note": "IMDb: 4 titles are named 'The Rescue'"}
        m = dict(self.MOVIE, network="Paramount")
        book, summ = self._review({"Movies": [m]}, meta=meta)
        f = book["Findings"]
        v = f[f["Type"] == "Verify"]
        self.assertEqual(set(v["Column"]), {"title_sub_category", "tiktok_user", "imdb_id"})
        self.assertEqual(summ["verify"], 3)
        ing = book["Reviewed"].iloc[0]           # the INGESTED row
        self.assertEqual(ing["imdb_id"], "http://www.imdb.com/title/tt35606013")

    def test_lookups_run_concurrently_once_per_title(self):
        import threading, time as _t
        seen, lock, live, peak = [], threading.Lock(), [0], [0]

        def slow(title, is_movie=True, year_hint=""):
            with lock:
                seen.append(title); live[0] += 1; peak[0] = max(peak[0], live[0])
            _t.sleep(0.2)
            with lock:
                live[0] -= 1
            return {}
        mf._CACHE.clear()
        rows = [dict(self.MOVIE, title="T%d" % k, imdb_id="") for k in range(6)]
        with mock.patch.object(app, "fetch_metadata", side_effect=slow):
            app._review_prefetch(rows, {c: c for c in rows[0]})
        self.assertEqual(sorted(seen), ["T%d" % k for k in range(6)])
        self.assertGreater(peak[0], 1)
