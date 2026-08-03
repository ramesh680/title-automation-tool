"""Release-date-first IMDb resolution (no network; the suggestion/enrich calls
are stubbed). Verifies the year drives IMDb selection for movies and TV."""
import unittest
import metadata_fetcher as mf


class YearFrom(unittest.TestCase):
    def test_from_iso_date(self): self.assertEqual(mf._year_from("2026-08-05"), 2026)
    def test_from_bare_year(self): self.assertEqual(mf._year_from("1978"), 1978)
    def test_none_when_absent(self): self.assertIsNone(mf._year_from("Netflix"))
    def test_none_on_blank(self): self.assertIsNone(mf._year_from(""))


SUGG = {"d": [
    {"id": "tt0078346", "l": "Superman", "y": 1978, "qid": "movie"},
    {"id": "tt5950044", "l": "Superman", "y": 2025, "qid": "movie"},
    {"id": "tt0106057", "l": "Superman", "y": 1988, "qid": "tvSeries"},
]}


class ImdbSuggest(unittest.TestCase):
    def setUp(self):
        self._gj = mf._get_json
        mf._get_json = lambda *a, **k: SUGG

    def tearDown(self):
        mf._get_json = self._gj

    def test_picks_entry_matching_release_year(self):
        self.assertEqual(mf.imdb_suggest_item("Superman", True, "2025")["id"], "tt5950044")

    def test_picks_older_entry_when_that_is_the_release_year(self):
        self.assertEqual(mf.imdb_suggest_item("Superman", True, "1978")["id"], "tt0078346")

    def test_accepts_within_one_year_tolerance(self):
        self.assertEqual(mf.imdb_suggest_item("Superman", True, "2024")["id"], "tt5950044")

    def test_rejects_when_no_candidate_fits_the_year(self):
        self.assertIsNone(mf.imdb_suggest_item("Superman", True, "2050", require_year=True))

    def test_without_require_year_falls_back_to_most_recent(self):
        self.assertEqual(mf.imdb_suggest_item("Superman", True, "")["id"], "tt5950044")

    def test_movie_request_skips_the_tv_series_entry(self):
        got = mf.imdb_suggest_item("Superman", True, "1988")
        self.assertNotEqual(got["id"], "tt0106057")  # tv item not chosen for a movie

    def test_tv_request_can_pick_the_series(self):
        self.assertEqual(mf.imdb_suggest_item("Superman", False, "1988")["id"], "tt0106057")


class TmdbPick(unittest.TestCase):
    R = [{"title": "Dune", "release_date": "1984-12-14"},
         {"title": "Dune", "release_date": "2021-10-22"},
         {"title": "Dune", "release_date": "2000-12-03"}]

    def test_picks_year_matching_result(self):
        self.assertEqual(mf._tmdb_pick(self.R, "Dune", 2021)["release_date"][:4], "2021")

    def test_picks_older_when_that_year_wanted(self):
        self.assertEqual(mf._tmdb_pick(self.R, "Dune", 1984)["release_date"][:4], "1984")

    def test_no_year_takes_most_recent(self):
        self.assertEqual(mf._tmdb_pick(self.R, "Dune")["release_date"][:4], "2021")


class ByTtNote(unittest.TestCase):
    def setUp(self):
        self._enrich = mf._enrich_by_tt
        mf._CACHE.clear()

    def tearDown(self):
        mf._enrich_by_tt = self._enrich
        mf._CACHE.clear()

    def test_flags_year_mismatch_on_explicit_tt(self):
        mf._enrich_by_tt = lambda tt, im, th, **k: {
            "imdb_id": "http://www.imdb.com/title/tt0078346", "released_on": "1978-12-15"}
        m = mf.fetch_metadata_by_tt("tt0078346", True, "Superman", year_hint="2025-07-11")
        self.assertIn("_imdb_year_note", m)

    def test_no_flag_when_year_agrees(self):
        mf._enrich_by_tt = lambda tt, im, th, **k: {
            "imdb_id": "http://www.imdb.com/title/tt5950044", "released_on": "2025-07-11"}
        m = mf.fetch_metadata_by_tt("tt5950044", True, "Superman", year_hint="2025-07-11")
        self.assertNotIn("_imdb_year_note", m)


if __name__ == "__main__":
    unittest.main()
