"""Movies review rebuild (Oct 2026) -- every case comes from the reviewed file
Reviewed_20261013_093907.xlsx, where the old review raised 28 findings, none
of them correct, and missed the real problems."""
import unittest
import movie_review_rules as M


def row(**kw):
    base = dict(title_category='Movies', network='', companies='Unknown',
                released_on='2026-10-09')
    base.update(kw)
    return base


HUMAN = row(title='Human', network='Dark Sky Films', released_on='2026-10-23',
            rottentomatoes='http://www.rottentomatoes.com/m/human_2025',
            youtube_channel_username='https://www.youtube.com/user/dsf2006|Human',
            twitter_search_term_keywords='("Human") ("Dark Sky Films" OR @darkskyfilms OR '
            '#DarkSkyFilms OR "2026" OR "All New" OR Film OR Movie)|Operations - Core Title'
            '|Operations - Core Title')

FALLING = row(title='Falling (2026)', network='Brainstorm Media',
              rottentomatoes='http://www.rottentomatoes.com/m/falling_2025',
              metacritic='http://www.metacritic.com/movie/falling-2025/',
              wikipedia_page='http://en.wikipedia.org/wiki/Falling_(2026_film)',
              twitter_search_term_keywords='("Falling (2026)") ("Brainstorm Media" OR '
              '#BrainstormMedia OR "2026" OR Film)|Operations - Core Title|Operations - Core Title')

PRAHAAR = row(title='Prahaar: The Untold Story of Ujjwal Nikam', network='Marudhar Films',
              released_on='2026-10-16')

JESUS = row(title='The Jesus Tapes - DAR', network='Atlas Distribution',
            twitter_handle='https://x.com/JesusTapesMovie', instagram_user='jesustapesmovie',
            twitter_search_term_keywords='("The Jesus Tapes") ("Atlas Distribution" OR '
            '@atlasmovies OR "2026" OR Film)|DAR|DAR|2021-01-01')

BALDWIN = row(title='The Trial of Alec Baldwin', network='Abramorama',
              released_on='2026-10-16', instagram_user='radiantmediastudio',
              twitter_search_term_keywords='("The Trial of Alec Baldwin") ("Abramorama" OR '
              '@abramorama OR #Abramorama OR "2026" OR Film)|Operations - Core Title'
              '|Operations - Core Title')


def disc(col, status, cur, sugg):
    return dict(column=col, status=status, current=cur, suggested=sugg)


class DiscoveryGate(unittest.TestCase):
    def test_tv_metacritic_never_suggested_for_a_movie(self):
        out = M.gate_discovery_findings(
            [disc('metacritic', 'Gap', None, 'http://www.metacritic.com/tv/human/')], HUMAN)
        self.assertEqual(out, [])

    def test_generic_one_word_handle_guess_dropped(self):
        f = [disc('twitter_handle', 'Verify', None, "Social handles: X/Twitter 'human' was "
                  "not listed by any source; it matches the title's handle on other "
                  "platforms -- verify."),
             disc('twitter_handle', 'Gap', None, 'human')]
        self.assertEqual(M.gate_discovery_findings(f, HUMAN), [])

    def test_word_from_long_title_dropped(self):
        note = ("Social handles: %s 'prahaar' was not listed by any source; it "
                "matches the title's handle on other platforms -- verify.")
        f = [disc('facebook_page', 'Verify', None, note % 'Facebook'),
             disc('twitter_handle', 'Verify', None, note % 'X/Twitter'),
             disc('facebook_page', 'Gap', None, 'http://www.facebook.com/prahaar'),
             disc('twitter_handle', 'Gap', None, 'prahaar')]
        self.assertEqual(M.gate_discovery_findings(f, PRAHAAR), [])

    def test_bare_rt_slug_never_replaces_year_correct_page(self):
        f = [disc('rottentomatoes', 'Mismatch', FALLING['rottentomatoes'],
                  'http://www.rottentomatoes.com/m/falling')]
        self.assertEqual(M.gate_discovery_findings(f, FALLING), [])

    def test_regenerated_keyword_clause_dropped(self):
        f = [disc('twitter_search_term_keywords', 'Mismatch',
                  HUMAN['twitter_search_term_keywords'], '("human") ("2025")')]
        self.assertEqual(M.gate_discovery_findings(f, HUMAN), [])

    def test_liveness_noise_dropped(self):
        f = [disc('instagram_user', 'Verify', 'jesustapesmovie',
                  "Social handles: Instagram 'jesustapesmovie' could not be opened to "
                  "confirm it is live -- verify."),
             disc('tiktok_user', 'Verify', None, "Social handles: TikTok "
                  "'thejesustapesmovie' was removed -- the profile does not exist.")]
        self.assertEqual(M.gate_discovery_findings(f, JESUS), [])

    def test_dead_handle_on_the_row_is_a_mismatch(self):
        r = dict(JESUS, tiktok_user='thejesustapesmovie')
        f = [disc('tiktok_user', 'Verify', 'thejesustapesmovie', "Social handles: TikTok "
                  "'thejesustapesmovie' was removed -- the profile does not exist.")]
        out = M.gate_discovery_findings(f, r)
        self.assertEqual(out[0]['status'], 'Mismatch')

    def test_single_source_release_scale_is_verify(self):
        f = [disc('title_sub_category', 'Mismatch', 'Release - Wide\nStudio - Independent',
                  'Release - Limited\nStudio - Independent')]
        self.assertEqual(M.gate_discovery_findings(f, BALDWIN)[0]['status'], 'Verify')

    def test_real_title_handle_gap_kept(self):
        f = [disc('twitter_handle', 'Gap', None, 'JesusTapesMovie')]
        r = dict(JESUS, twitter_handle=None)
        self.assertEqual(len(M.gate_discovery_findings(f, r)), 1)


class RowRules(unittest.TestCase):
    def cols(self, r):
        return {(f['column'], f['status']) for f in M.movie_rule_findings(r)}

    def test_twitter_handle_stored_as_url(self):
        fs = [f for f in M.movie_rule_findings(JESUS) if f['column'] == 'twitter_handle']
        self.assertEqual(fs[0]['status'], 'Mismatch')
        self.assertEqual(fs[0]['suggested'], 'JesusTapesMovie')

    def test_company_instagram_on_film_row(self):
        self.assertIn(('instagram_user', 'Mismatch'), self.cols(BALDWIN))

    def test_distributor_handle_from_keywords_is_company(self):
        r = dict(HUMAN, twitter_handle='darkskyfilms')
        self.assertIn(('twitter_handle', 'Mismatch'), self.cols(r))

    def test_franchise_handle_accepted(self):
        r = row(title='Avengers: Endgame - DAR')
        self.assertEqual(M.handle_relevance('Avengers', r), 'franchise')

    def test_film_handles_pass(self):
        self.assertEqual(M.handle_relevance('jesustapesmovie', JESUS), 'title')
        self.assertEqual(M.handle_relevance('TrialOfAlecBaldwin', BALDWIN), 'title')

    def test_keyword_year_must_match_release(self):
        r = dict(HUMAN, released_on='2027-01-15')
        f = [x for x in M.movie_rule_findings(r)
             if x['column'] == 'twitter_search_term_keywords'][0]
        self.assertEqual(f['status'], 'Mismatch')
        self.assertIn('"2027"', f['suggested'])
        self.assertIn('"Dark Sky Films"', f['suggested'])   # case preserved

    def test_year_disambiguator_phrase_is_correct(self):
        self.assertFalse(any(f['column'] == 'twitter_search_term_keywords'
                             for f in M.movie_rule_findings(FALLING)))

    def test_phrase_missing_disambiguator_flagged(self):
        r = dict(FALLING, twitter_search_term_keywords=FALLING[
            'twitter_search_term_keywords'].replace('"Falling (2026)"', '"Falling"'))
        f = [x for x in M.movie_rule_findings(r)
             if x['column'] == 'twitter_search_term_keywords'][0]
        self.assertIn('("Falling (2026)")', f['suggested'])

    def test_correct_keywords_not_flagged(self):
        self.assertFalse(any(f['column'] == 'twitter_search_term_keywords'
                             for f in M.movie_rule_findings(HUMAN)))

    def test_festival_year_rt_is_fine(self):
        self.assertFalse(any(f['column'] == 'rottentomatoes'
                             for f in M.movie_rule_findings(HUMAN)))

    def test_wrong_year_rt_flagged(self):
        r = dict(FALLING, rottentomatoes='http://www.rottentomatoes.com/m/falling_2020')
        self.assertIn(('rottentomatoes', 'Mismatch'), self.cols(r))

    def test_tv_page_on_movie_row_flagged(self):
        r = dict(HUMAN, metacritic='http://www.metacritic.com/tv/human/')
        self.assertIn(('metacritic', 'Mismatch'), self.cols(r))

    def test_url_managers_must_reference_row_accounts(self):
        r = dict(JESUS, url_managers='instagram|someoneelse|Film Ops\n'
                                     'instagram|jesustapesmovie|Film Ops')
        f = [x for x in M.movie_rule_findings(r) if x['column'] == 'url_managers'][0]
        self.assertEqual(f['suggested'], 'instagram|jesustapesmovie|Film Ops')

    def test_one_finding_per_cell(self):
        a = dict(column='twitter_handle', status='Verify')
        b = dict(column='twitter_handle', status='Gap')
        self.assertEqual(M.merge_findings([a], [b]), [b])


if __name__ == '__main__':
    unittest.main()
