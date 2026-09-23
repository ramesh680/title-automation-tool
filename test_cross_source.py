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
