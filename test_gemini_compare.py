"""Gemini side-by-side comparison: sanitising, match labelling, row wiring and
workbook shape. No network -- the resolver's single call site is stubbed.

The sanitiser cases are taken from the real NYFW SS27 sheet run, including the
prose answer the model gave for Wiederhoeft's IMDb column.
"""
import unittest
from unittest import mock

import openpyxl

import gemini_resolver as gr
import app


class Sanitise(unittest.TestCase):
    def test_refusal_prose_becomes_blank(self):
        prose = ("I do not have enough information to answer the query. I am "
                 "missing an official verified IMDb profile or ID for the "
                 "fashion brand Wiederhoeft.")
        self.assertEqual(gr.sanitize('imdb_id', prose), '')

    def test_other_non_answers_blank(self):
        for v in ('', '   ', 'N/A', 'none', 'null', 'Unknown',
                  "I'm sorry, I could not find an official account.",
                  'No official TikTok account could be confirmed.',
                  'not applicable'):
            self.assertEqual(gr.sanitize('instagram_user', v), '', repr(v))

    def test_instagram_handle_forms(self):
        for v in ('agmesnyc', '@agmesnyc', 'https://www.instagram.com/agmesnyc',
                  'https://instagram.com/agmesnyc/', 'AGMESNYC'):
            self.assertEqual(gr.sanitize('instagram_user', v), 'agmesnyc', repr(v))

    def test_instagram_rejects_sentence(self):
        self.assertEqual(gr.sanitize('instagram_user', 'the official account is agmesnyc'), '')

    def test_twitter_handle_forms_and_limits(self):
        self.assertEqual(gr.sanitize('twitter_handle', 'https://x.com/magdabutrym'),
                         'magdabutrym')
        self.assertEqual(gr.sanitize('twitter_handle', '@zaldynyc'), 'zaldynyc')
        # >15 chars is not a valid X username
        self.assertEqual(gr.sanitize('twitter_handle', 'a' * 16), '')

    def test_facebook_must_be_a_facebook_url(self):
        self.assertEqual(gr.sanitize('facebook_page', 'http://www.facebook.com/agmesnyc'),
                         'https://www.facebook.com/agmesnyc')
        self.assertEqual(gr.sanitize('facebook_page', 'agmesnyc'), '')
        self.assertEqual(gr.sanitize('facebook_page',
                                     'https://www.facebook.com/profile.php'), '')

    def test_youtube_normalises_to_canonical_url(self):
        self.assertEqual(gr.sanitize('youtube_channel_username', '@magda_butrym'),
                         'https://www.youtube.com/@magda_butrym')
        self.assertEqual(
            gr.sanitize('youtube_channel_username',
                        'http://www.youtube.com/channel/UCwk3O80_zasUO7AD427gCcA'),
            'https://www.youtube.com/channel/UCwk3O80_zasUO7AD427gCcA')

    def test_wikipedia_keeps_language_subdomain(self):
        self.assertEqual(
            gr.sanitize('wikipedia_page', 'https://pl.wikipedia.org/wiki/Magda_Butrym'),
            'https://pl.wikipedia.org/wiki/Magda_Butrym')
        self.assertEqual(gr.sanitize('wikipedia_page', 'Magda_Butrym'), '')

    def test_imdb_extracts_id(self):
        self.assertEqual(gr.sanitize('imdb_id', 'nm2814897'), 'nm2814897')
        self.assertEqual(gr.sanitize('imdb_id', 'https://www.imdb.com/name/nm2814897/'),
                         'nm2814897')
        self.assertEqual(gr.sanitize('imdb_id', 'Zaldy'), '')


class MatchLabels(unittest.TestCase):
    def test_protocol_and_www_differences_still_match(self):
        self.assertEqual(
            app._gemini_match_label('facebook_page',
                                    'http://www.facebook.com/agmesnyc',
                                    'https://facebook.com/agmesnyc/'),
            'match')

    def test_case_and_at_sign_differences_still_match(self):
        self.assertEqual(
            app._gemini_match_label('twitter_handle', 'MagdaButrym', '@magdabutrym'),
            'match')

    def test_multi_value_cell_matches_on_any_line(self):
        existing = ('https://www.youtube.com/@JamieHaller-wt4jd\n'
                    'https://www.youtube.com/@shopjamiehaller')
        self.assertEqual(
            app._gemini_match_label('youtube_channel_username', existing,
                                    'https://www.youtube.com/@shopjamiehaller'),
            'match')

    def test_one_sided_and_blank_states(self):
        self.assertEqual(app._gemini_match_label('instagram_user', 'a', ''),
                         'existing only')
        self.assertEqual(app._gemini_match_label('instagram_user', '', 'a'),
                         'gemini only')
        self.assertEqual(app._gemini_match_label('instagram_user', '', ''),
                         'both blank')
        self.assertEqual(app._gemini_match_label('instagram_user', 'a', 'b'),
                         'mismatch')


class _FakeResp:
    """Minimal stand-in for the SDK response object _ask reads."""

    def __init__(self, text):
        self.text = text
        self.candidates = []
        self.usage_metadata = None


def _fake_generate(answers):
    """A _generate stand-in (the only network-touching function) that answers
    per entity name found in the prompt. Request accounting stays real."""
    def _gen(prompt):
        for name, payload in answers.items():
            if f"Name: {name} " in prompt:
                return _FakeResp(payload)
        return _FakeResp('')
    return _gen


class RowWiring(unittest.TestCase):
    def setUp(self):
        gr.clear_cache()
        gr.reset_stats()
        self.rows = [
            {'title': 'AGMES', 'title_category': 'Fashion',
             'title_sub_category': 'Unknown', 'instagram_user': 'agmesnyc',
             'facebook_page': '', 'twitter_handle': '', 'tiktok_user': '',
             'youtube_channel_username': '', 'wikipedia_page': '', 'imdb_id': ''},
            {'title': 'AGMES - DAR', 'title_category': 'Fashion',
             'title_sub_category': 'Unknown', 'instagram_user': 'agmesnyc',
             'facebook_page': '', 'twitter_handle': '', 'tiktok_user': '',
             'youtube_channel_username': '', 'wikipedia_page': '', 'imdb_id': ''},
        ]
        self.answer = ('{"facebook": "https://www.facebook.com/agmesnyc", '
                       '"twitter": "", "instagram": "@AGMESNYC", "youtube": "", '
                       '"tiktok": "agmesnyc", "wikipedia": "", '
                       '"imdb": "I do not have enough information."}')

    def _attach(self):
        with mock.patch.object(gr, 'available', return_value=True), \
             mock.patch.object(gr, '_generate', _fake_generate({'AGMES': self.answer})):
            return app.attach_gemini_comparison([dict(r) for r in self.rows])

    def test_disabled_resolver_leaves_rows_untouched(self):
        with mock.patch.object(gr, 'available', return_value=False):
            out = app.attach_gemini_comparison([dict(r) for r in self.rows])
        self.assertFalse(any(k.startswith('_gemini') for r in out for k in r))

    def test_values_are_sanitised_onto_underscore_keys(self):
        out = self._attach()
        self.assertEqual(out[0]['_gemini_instagram_user'], 'agmesnyc')
        self.assertEqual(out[0]['_gemini_facebook_page'],
                         'https://www.facebook.com/agmesnyc')
        # prose answer never reaches a data column
        self.assertEqual(out[0]['_gemini_imdb_id'], '')

    def test_existing_columns_are_never_modified(self):
        out = self._attach()
        for before, after in zip(self.rows, out):
            for col in app.GEMINI_COMPARE_FIELDS:
                self.assertEqual(before.get(col, ''), after.get(col, ''), col)

    def test_dar_twin_gets_identical_answers_from_cache(self):
        """The sheet gave the same entity different answers on its DAR and
        non-DAR rows; one resolve per normalised name removes that."""
        out = self._attach()
        for col in app.GEMINI_COMPARE_FIELDS:
            self.assertEqual(out[0][f'_gemini_{col}'], out[1][f'_gemini_{col}'], col)
        # both rows normalise to the same entity, so it is resolved once
        self.assertEqual(gr.stats()['requests'], 1)

    def test_repeat_resolve_is_served_from_cache(self):
        with mock.patch.object(gr, 'available', return_value=True), \
             mock.patch.object(gr, '_generate',
                               _fake_generate({'AGMES': self.answer})):
            first = gr.resolve('AGMES', 'Fashion')
            second = gr.resolve('AGMES - DAR', 'Fashion')
        self.assertEqual(first, second)
        self.assertEqual(gr.stats()['requests'], 1)
        self.assertEqual(gr.stats()['cached'], 1)

    def test_compare_records_and_summary(self):
        recs = app._gemini_compare_records(self._attach())
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]['instagram_user_match'], 'match')
        self.assertEqual(recs[0]['facebook_page_match'], 'gemini only')
        self.assertEqual(recs[0]['imdb_id_match'], 'both blank')
        summary = {s['field']: s for s in app._gemini_summary_records(recs)}
        self.assertEqual(summary['instagram_user']['match'], 2)
        self.assertEqual(summary['instagram_user']['agreement_when_both_filled'],
                         '100.0%')
        self.assertEqual(summary['facebook_page']['gemini_only'], 2)
        self.assertEqual(summary['imdb_id']['agreement_when_both_filled'], '')

    def test_columns_are_paired_triples(self):
        cols = app._gemini_compare_columns()
        i = cols.index('instagram_user')
        self.assertEqual(cols[i:i + 3],
                         ['instagram_user', 'instagram_user_gemini',
                          'instagram_user_match'])


class WorkbookShape(unittest.TestCase):
    def setUp(self):
        gr.clear_cache()
        gr.reset_stats()

    def _rows(self):
        base = {'title': 'AGMES', 'title_category': 'Fashion',
                'title_sub_category': 'Unknown', 'instagram_user': 'agmesnyc'}
        answer = ('{"facebook": "", "twitter": "", "instagram": "agmesnyc", '
                  '"youtube": "", "tiktok": "", "wikipedia": "", "imdb": ""}')
        with mock.patch.object(gr, 'available', return_value=True), \
             mock.patch.object(gr, '_generate', _fake_generate({'AGMES': answer})):
            return app.attach_gemini_comparison([dict(base)])

    def test_comparison_sheets_added(self):
        wb = openpyxl.load_workbook(app._rows_to_workbook(self._rows()))
        self.assertIn('Gemini Compare', wb.sheetnames)
        self.assertIn('Gemini Summary', wb.sheetnames)

    def test_ingest_sheet_keeps_its_exact_columns(self):
        wb = openpyxl.load_workbook(app._rows_to_workbook(self._rows()))
        ws = wb['Sheet1']
        header = [c.value for c in ws[1]]
        self.assertEqual(header, app.COLUMNS)
        self.assertFalse([h for h in header if h and 'gemini' in str(h)])

    def test_no_comparison_sheets_without_the_flag(self):
        plain = [{'title': 'AGMES', 'title_category': 'Fashion',
                  'instagram_user': 'agmesnyc'}]
        wb = openpyxl.load_workbook(app._rows_to_workbook(plain))
        self.assertNotIn('Gemini Compare', wb.sheetnames)


class Prompt(unittest.TestCase):
    def test_shared_rule_block_matches_the_sheet_wording(self):
        """The consolidated and per-platform prompts must carry the sheet's
        rule block unchanged -- that is what makes the comparison fair."""
        for tmpl in (gr._PER_PLATFORM_PROMPT, gr._CONSOLIDATED_PROMPT):
            self.assertIn('Never return a fan, parody, tribute, news, '
                          'aggregator, unofficial or unrelated account.', tmpl)
            self.assertIn('If no official account/page can be confidently '
                          'confirmed, return nothing.', tmpl)
            self.assertIn("please ignore the ' - DAR' suffix if present", tmpl)

    def test_consolidated_prompt_asks_for_all_seven_platforms(self):
        for label in ('Facebook', 'Twitter/X', 'Instagram', 'YouTube',
                      'TikTok', 'Wikipedia', 'IMDb'):
            self.assertIn(label, gr._CONSOLIDATED_PROMPT)

    def test_per_platform_mode_makes_one_request_per_platform(self):
        gr.clear_cache()
        gr.reset_stats()
        with mock.patch.object(gr, 'available', return_value=True), \
             mock.patch.object(gr, 'MODE', 'per_platform'), \
             mock.patch.object(gr, '_generate', lambda p: _FakeResp('')):
            gr.resolve('AGMES', 'Fashion')
        self.assertEqual(gr.stats()['requests'], len(gr.FIELDS))

    def test_consolidated_mode_makes_one_request(self):
        gr.clear_cache()
        gr.reset_stats()
        with mock.patch.object(gr, 'available', return_value=True), \
             mock.patch.object(gr, '_generate', lambda p: _FakeResp('{}')):
            gr.resolve('AGMES', 'Fashion')
        self.assertEqual(gr.stats()['requests'], 1)

    def test_request_budget_caps_a_run(self):
        gr.clear_cache()
        gr.reset_stats()
        with mock.patch.object(gr, 'available', return_value=True), \
             mock.patch.object(gr, 'MAX_REQUESTS', 2), \
             mock.patch.object(gr, '_generate', return_value=None):
            for n in range(5):
                gr.resolve(f'Brand {n}', 'Fashion')
        # 2 attempts allowed through, the rest short-circuited
        self.assertEqual(gr.stats()['requests'], 2)
        self.assertEqual(gr.stats()['errors'], 2)
        self.assertEqual(gr.stats()['capped'], 3)


class StatusEndpoint(unittest.TestCase):
    def test_status_reports_a_reason_when_unavailable(self):
        client = app.app.test_client()
        with mock.patch.object(gr, 'API_KEY', ''):
            body = client.get('/api/gemini_status').get_json()
        self.assertFalse(body['available'])
        self.assertIn('reason', body)


if __name__ == '__main__':
    unittest.main()
