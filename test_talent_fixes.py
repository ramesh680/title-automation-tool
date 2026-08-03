"""Talent classification & IMDb nm base-name fixes (Aug 2026)."""
import unittest
import app
import metadata_fetcher as mf


class TalentTypeDemotesModel(unittest.TestCase):
    def ttype(self, occs, gender='', sports=None):
        line, _sub = app._talent_classify({'occupations': occs, 'gender': gender,
                                            'sports': sports or []})
        return line

    def test_dove_cameron_actor_singer_model_becomes_actress(self):
        # Wikidata may list 'model' first; a primary trade must still win.
        self.assertEqual(self.ttype(['model', 'actor', 'singer'], 'Gender - Woman'),
                         'Talent Type - Actress')

    def test_singer_before_model_is_musician(self):
        self.assertEqual(self.ttype(['singer', 'model']), 'Talent Type - Musician')

    def test_model_only_stays_model(self):
        self.assertEqual(self.ttype(['model']), 'Talent Type - Model')

    def test_model_only_woman_still_model(self):
        self.assertEqual(self.ttype(['model'], 'Gender - Woman'), 'Talent Type - Model')

    def test_wahlberg_actor_before_rapper_unchanged(self):
        self.assertEqual(self.ttype(['actor', 'rapper']), 'Talent Type - Actor')

    def test_actor_woman_is_actress(self):
        self.assertEqual(self.ttype(['actor'], 'Gender - Woman'), 'Talent Type - Actress')


class PersonBaseName(unittest.TestCase):
    def ent(self, enwiki):
        return {'sitelinks': {'enwiki': {'title': enwiki}}} if enwiki else None

    def test_wikipedia_title_wins_over_provided(self):
        self.assertEqual(mf._person_base_name(self.ent('Kara Young'), {}, 'kara yong'),
                         'Kara Young')

    def test_disambiguator_stripped(self):
        self.assertEqual(mf._person_base_name(self.ent('Kara Young (actress)'), {}, 'Kara Young'),
                         'Kara Young')

    def test_wikipedia_page_url_used_when_no_entity(self):
        meta = {'wikipedia_page': 'https://en.wikipedia.org/wiki/Kara_Young_(actress)'}
        self.assertEqual(mf._person_base_name(None, meta, 'someone'), 'Kara Young')

    def test_falls_back_to_provided_when_no_wikipedia(self):
        self.assertEqual(mf._person_base_name(None, {}, 'Jane Doe'), 'Jane Doe')


class TwitterHandleFormats(unittest.TestCase):
    """#3 -- already handled; lock the three accepted formats."""
    def test_three_formats_equivalent(self):
        k = mf.__dict__  # noqa
        from app import _tw_handle_key
        keys = {_tw_handle_key('http://twitter.com/matthewmercer'),
                _tw_handle_key('matthewmercer'),
                _tw_handle_key('http://x.com/matthewmercer')}
        self.assertEqual(keys, {'matthewmercer'})

    def test_review_not_flagged_across_formats(self):
        from app import _review_compare
        for cur in ('http://twitter.com/matthewmercer', 'matthewmercer', 'http://x.com/matthewmercer'):
            for exp in ('http://twitter.com/matthewmercer', 'matthewmercer', 'http://x.com/matthewmercer'):
                self.assertTrue(_review_compare('twitter_handle', cur, exp)[0], (cur, exp))


if __name__ == '__main__':
    unittest.main()


class NotableNamesakeWins(unittest.TestCase):
    """No profession hint: among same-name people, the one WITH a Wikipedia
    page (the notable talent) must be chosen over an obscure namesake."""
    def _snak(self, v): return {"mainsnak": {"snaktype": "value", "datavalue": {"value": v}}}
    def _human(self, p106, imdb, enwiki=None):
        c = {"P31": [self._snak({"id": "Q5"})],
             "P106": [self._snak({"id": q}) for q in p106],
             "P345": [self._snak(imdb)]}
        e = {"claims": c, "labels": {"en": {"value": "Kevin Hart"}}, "aliases": {"en": []}}
        if enwiki: e["sitelinks"] = {"enwiki": {"title": enwiki}}
        return e

    def setUp(self):
        self._e, self._c, self._l = mf._entity, mf._search_candidates, mf._labels
        mf._CACHE.clear()
        comedian = self._human(["Qcomedian", "Qactor"], "nm2076834", enwiki="Kevin Hart (comedian)")
        commentator = self._human(["Qcommentator"], "nm0366389")  # no Wikipedia
        items = {"Qcomm": commentator, "Qcom": comedian}
        mf._entity = lambda q: items.get(q)
        mf._search_candidates = lambda t, limit=6: ["Qcomm", "Qcom"]  # obscure one first
        mf._labels = lambda qs: {"Qcomedian": "comedian", "Qactor": "actor",
                                 "Qcommentator": "sports commentator"}
        self._gj = mf._get_json
        # IMDb suggestion for "Kevin Hart" lists the comedian's nm -> P345 kept
        mf._get_json = lambda url, **k: {"d": [{"id": "nm2076834", "l": "Kevin Hart"}]}

    def tearDown(self):
        mf._entity, mf._search_candidates, mf._labels = self._e, self._c, self._l
        mf._get_json = self._gj
        mf._CACHE.clear()

    def test_prominence_helper_ranks_wikipedia_person_higher(self):
        comedian = self._human(["Qcomedian"], "nm1", enwiki="Kevin Hart")
        commentator = self._human(["Qcommentator"], "nm2")
        self.assertGreater(mf._person_prominence(comedian), mf._person_prominence(commentator))

    def test_resolver_picks_the_comedian_not_the_commentator(self):
        meta = mf.fetch_person("Kevin Hart")
        self.assertEqual(meta.get("imdb_id"), "https://www.imdb.com/name/nm2076834")
        self.assertIn("comedian", meta.get("occupations", []))
        self.assertTrue(str(meta.get("wikipedia_page", "")).endswith("Kevin_Hart_(comedian)"))


class ImdbNmByName(unittest.TestCase):
    """nm must be the person IMDb actually names; a mislinked P345 is replaced."""
    KARA = [{"id": "nm16990294", "l": "Kara Young"},
            {"id": "nm0000001", "l": "Kara Young Smith"}]

    def test_mislinked_p345_replaced_by_correctly_named_nm(self):
        # nm0949743 (IMDb: 'Mary Young') is NOT among the 'Kara Young' results
        self.assertEqual(mf._pick_person_imdb("Kara Young", "nm0949743", self.KARA),
                         "nm16990294")

    def test_p345_present_as_alias_but_wrong_name_is_replaced(self):
        # nm0949743 DOES surface for "Kara Young" but IMDb names it "Mary Young"
        cands = [{"id": "nm0949743", "l": "Mary Young"},
                 {"id": "nm16990294", "l": "Kara Young"}]
        self.assertEqual(mf._pick_person_imdb("Kara Young", "nm0949743", cands),
                         "nm16990294")

    def test_corroborated_p345_is_kept(self):
        self.assertEqual(mf._pick_person_imdb("Kara Young", "nm16990294", self.KARA),
                         "nm16990294")

    def test_no_p345_uses_exact_named_suggestion(self):
        self.assertEqual(mf._pick_person_imdb("Kara Young", None, self.KARA), "nm16990294")

    def test_no_exact_match_keeps_p345_failopen(self):
        self.assertEqual(mf._pick_person_imdb("Nobody Here", "nm55", []), "nm55")

    def test_blank_base_returns_p345(self):
        self.assertEqual(mf._pick_person_imdb("", "nm55", self.KARA), "nm55")


class AmbiguousDisambiguation(unittest.TestCase):
    """Two Wikipedia-notable people share the name + no profession -> flag,
    don't guess (leave IMDb/details blank)."""
    def _snak(self, v): return {"mainsnak": {"snaktype": "value", "datavalue": {"value": v}}}
    def _human(self, imdb, enwiki):
        return {"claims": {"P31": [self._snak({"id": "Q5"})],
                           "P345": [self._snak(imdb)]},
                "labels": {"en": {"value": "Kara Young"}}, "aliases": {"en": []},
                "sitelinks": {"enwiki": {"title": enwiki}}}
    def setUp(self):
        self._e, self._c, self._l, self._gj = mf._entity, mf._search_candidates, mf._labels, mf._get_json
        mf._CACHE.clear()
        items = {"Qactress": self._human("nm16990294", "Kara Young (actress)"),
                 "Qmodel":   self._human("nm0949743",  "Kara Young (model)")}
        mf._entity = lambda q: items.get(q)
        mf._search_candidates = lambda t, limit=6: ["Qmodel", "Qactress"]
        mf._labels = lambda qs: {}
        mf._get_json = lambda *a, **k: {"d": []}
    def tearDown(self):
        mf._entity, mf._search_candidates, mf._labels, mf._get_json = self._e, self._c, self._l, self._gj
        mf._CACHE.clear()

    def test_ambiguous_name_is_flagged_not_guessed(self):
        meta = mf.fetch_person("Kara Young")
        self.assertTrue(meta.get("needs_review"))
        self.assertIn("more than one", meta.get("review_reason", ""))

    def test_ambiguous_name_emits_no_imdb_id(self):
        meta = mf.fetch_person("Kara Young")
        self.assertNotIn("imdb_id", meta)
