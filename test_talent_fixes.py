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
