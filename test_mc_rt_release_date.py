"""Release-date-first Metacritic & Rotten Tomatoes resolution (no network:
_url_status is stubbed to a fixed set of 'existing' pages)."""
import unittest
import metadata_fetcher as mf


class Base(unittest.TestCase):
    EXIST = set()  # https URLs (no trailing slash) that return 200
    def setUp(self):
        self._us, self._v = mf._url_status, mf.VALIDATE_URLS
        mf.VALIDATE_URLS = True
        exist = self.EXIST
        def fake(url):
            u = url.replace("http://", "https://").rstrip("/")
            return 200 if u in exist else 404
        mf._url_status = fake
    def tearDown(self):
        mf._url_status, mf.VALIDATE_URLS = self._us, self._v


class Metacritic(Base):
    # both the bare (older) and the 2025 page exist
    EXIST = {"https://www.metacritic.com/movie/superman",
             "https://www.metacritic.com/movie/superman-2025"}

    def test_year_suffixed_page_wins_when_year_known(self):
        got = mf.resolve_metacritic("Superman", True, year=2025)
        self.assertEqual(got, "http://www.metacritic.com/movie/superman-2025/")

    def test_bare_slug_used_when_no_year(self):
        got = mf.resolve_metacritic("Superman", True)
        self.assertEqual(got, "http://www.metacritic.com/movie/superman/")

    def test_falls_back_to_bare_when_year_page_absent(self):
        mf._url_status = lambda u: 200 if u.rstrip("/") == "https://www.metacritic.com/movie/superman" else 404
        got = mf.resolve_metacritic("Superman", True, year=1978)
        self.assertEqual(got, "http://www.metacritic.com/movie/superman/")

    def test_curated_url_is_trusted(self):
        got = mf.resolve_metacritic("Whatever", True,
                                    candidate="http://www.metacritic.com/movie/superman", curated=True)
        self.assertEqual(got, "http://www.metacritic.com/movie/superman")

    def test_nothing_when_no_page_exists(self):
        mf._url_status = lambda u: 404
        self.assertEqual(mf.resolve_metacritic("No Such Film", True, year=2030), "")


class RottenTomatoes(Base):
    EXIST = {"https://www.rottentomatoes.com/m/superman",
             "https://www.rottentomatoes.com/m/superman_2025"}

    def test_year_suffixed_page_wins(self):
        got = mf.resolve_rottentomatoes("Superman", True, year=2025)
        self.assertEqual(got, "http://www.rottentomatoes.com/m/superman_2025")

    def test_bare_slug_without_year(self):
        got = mf.resolve_rottentomatoes("Superman", True)
        self.assertEqual(got, "http://www.rottentomatoes.com/m/superman")

    def test_tv_yields_nothing(self):
        self.assertEqual(mf.resolve_rottentomatoes("Some Show", False, year=2025), "")

    def test_tv_candidate_dropped(self):
        got = mf.resolve_rottentomatoes("Show", False,
                                        candidate="http://www.rottentomatoes.com/tv/show", curated=True)
        self.assertEqual(got, "")

    def test_curated_movie_url_trusted(self):
        got = mf.resolve_rottentomatoes("X", True,
                                        candidate="http://www.rottentomatoes.com/m/superman_2025", curated=True)
        self.assertEqual(got, "http://www.rottentomatoes.com/m/superman_2025")

    def test_generated_url_dropped_when_absent(self):
        mf._url_status = lambda u: 404
        self.assertEqual(mf.resolve_rottentomatoes("No Film", True, year=2030), "")

    def test_underscore_slug_for_multiword_title(self):
        mf._url_status = lambda u: 200 if u.rstrip("/") == "https://www.rottentomatoes.com/m/dune_part_two_2024" else 404
        got = mf.resolve_rottentomatoes("Dune Part Two", True, year=2024)
        self.assertEqual(got, "http://www.rottentomatoes.com/m/dune_part_two_2024")


if __name__ == "__main__":
    unittest.main()
