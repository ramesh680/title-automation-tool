"""Genres must be IMDb / Box Office Mojo genres (Pendulum 2027 case)."""
import unittest

import metadata_fetcher as mf


class GenreVocabulary(unittest.TestCase):
    def test_pendulum_wikidata_genres(self):
        # Wikidata Q134114137 genre (P136): horror film, mystery film,
        # thriller film, horror fiction -> IMDb says Horror, Mystery, Thriller
        notes = []
        got = mf.normalize_genres(
            ["horror film", "mystery film", "thriller film", "horror fiction"], notes)
        self.assertEqual(got, ["Horror", "Mystery", "Thriller"])
        self.assertTrue(any("Horror Fiction" in n or "horror fiction" in n for n in notes))

    def test_already_leaked_value_is_cleaned(self):
        self.assertEqual(mf.normalize_genres("Horror\nMystery\nThriller\nHorror Fiction"),
                         ["Horror", "Mystery", "Thriller"])

    def test_lf_spellings_and_compounds(self):
        self.assertEqual(mf.normalize_genres(["Sci-Fi", "Science Fiction", "Film-Noir"]),
                         ["Sci Fi", "Film Noir"])
        self.assertEqual(mf.normalize_genres(["romantic comedy film", "Action & Adventure"]),
                         ["Romance", "Comedy", "Action", "Adventure"])

    def test_non_imdb_labels_dropped(self):
        self.assertEqual(mf.normalize_genres(["TV Movie", "LGBTQ-related film",
                                              "speculative fiction"]), [])

    def test_apply_sets_primary(self):
        meta = {}
        mf._apply_genres(meta, "Horror Fiction\nHorror\nThriller")
        self.assertEqual(meta["genre"], "Horror\nThriller")
        self.assertEqual(meta["primary_genre"], "Horror")




class ValidatorGenreRule(unittest.TestCase):
    def test_horror_fiction_fails(self):
        import validator as v
        rule = {"applies_to": ["Movies"], "message": "genre must use IMDb genres only."}
        row = {"title_category": "Movies", "genre": "Horror\nMystery\nThriller\nHorror Fiction"}
        sev, msg = v._chk_imdb_genre(row["genre"], row, rule)
        self.assertEqual(sev, v.SEV_FAIL)
        self.assertIn("Horror Fiction", msg)
        row["genre"] = "Horror\nMystery\nThriller"
        self.assertEqual(v._chk_imdb_genre(row["genre"], row, rule), (None, ""))


class WikidataGenreReferences(unittest.TestCase):
    def test_zero_reference_genre_skipped(self):
        ref = [{"snaks": {"P143": []}}]
        def claim(q, refs):
            c = {"rank": "normal",
                 "mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": q}}}}
            if refs:
                c["references"] = refs
            return c
        claims = {"P136": [claim("Q200092", ref), claim("Q1200678", ref),
                           claim("Q2484376", ref), claim("Q193606", None)]}
        self.assertEqual(mf._claim_values(claims, "P136", referenced=True),
                         ["Q200092", "Q1200678", "Q2484376"])
        # default behaviour (other properties) unchanged
        self.assertEqual(len(mf._claim_values(claims, "P136")), 4)


class GameGenreReferences(unittest.TestCase):
    def test_game_path_asks_for_referenced_genres(self):
        import inspect
        src = inspect.getsource(mf.fetch_game)
        self.assertIn('_claim_values(claims, "P136", referenced=True)', src)


if __name__ == "__main__":
    unittest.main()
