"""Drive template sync (Nov 2026): the Generator and Review use the newest
ingest template per category from the Ops Drive folder, validated before use,
with the bundled copies as fallback. Drive is simulated."""
import io
import os
import tempfile
import unittest

import openpyxl

import reference_data as REF
import template_sync as TS
import app as A

REF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reference')
BUNDLED = {'film': 'film_ingest_template.xlsx', 'tv': 'tv_ingest_template.xlsx',
           'talent': 'talent_ingest_template.xlsx', 'game': 'videogame_ingest_template.xlsx'}


def _bytes(cat):
    return open(os.path.join(REF_DIR, BUNDLED[cat]), 'rb').read()


def _with_studio(extra_studio):
    """The bundled film template with one extra studio row (a 'new' template)."""
    wb = openpyxl.load_workbook(os.path.join(REF_DIR, BUNDLED['film']))
    ws = wb['DropDown']
    r = ws.max_row + 1
    ws.cell(r, 10, extra_studio); ws.cell(r, 11, 'Studio - Independent')
    ws.cell(r, 13, extra_studio); ws.cell(r, 14, f'"{extra_studio}" OR @x')
    b = io.BytesIO(); wb.save(b)
    return b.getvalue()


class _Resp:
    def __init__(self, status=200, js=None, content=b''):
        self.status_code, self._js, self.content = status, js, content

    def json(self):
        return self._js

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


class FakeDrive:
    """Folder listing like the real one: one native sheet + shortcuts."""
    def __init__(self, files, targets, exports):
        self.files, self.targets, self.exports, self.downloads = files, targets, exports, 0

    def get(self, url, params=None, timeout=None):
        if url.endswith('/files'):
            return _Resp(js={'files': self.files})
        if url.endswith('/export'):
            self.downloads += 1
            fid = url.split('/files/')[1].split('/')[0]
            return _Resp(content=self.exports[fid])
        fid = url.rsplit('/', 1)[1]
        if fid in self.targets:
            return _Resp(js=self.targets[fid])
        return _Resp(status=404)


def _drive(film_bytes=None, film_newer='movies'):
    sc = lambda i, name, tid: {'id': i, 'name': name, 'mimeType': TS.SHORTCUT,
                                'shortcutDetails': {'targetId': tid}}
    files = [
        {'id': 'M26', 'name': 'NEW Ingest Template - Movies (2026)', 'mimeType': TS.GSHEET,
         'modifiedTime': '2026-10-28T10:54:21Z' if film_newer == 'movies' else '2026-01-01T00:00:00Z'},
        sc('s1', 'New Video Game - Ingest Template', 'G'),
        sc('s2', 'New Brand Definitions Ingest Template - TV', 'T'),
        sc('s3', 'Ingest Template - Talent', 'L'),
        sc('s4', 'Professional Sports Teams - Ingest Template', 'SP'),
        sc('s5', 'Ingest Template - Beverages', 'BV'),
        sc('s6', 'NEW Ingest Template - Film w/ Language', 'F'),
        sc('s7', 'Ingest Template - General', 'GE'),
        sc('s8', 'Ingest Template - Beauty', 'BE'),
    ]
    t = lambda i, m: {'id': i, 'mimeType': TS.GSHEET, 'modifiedTime': m}
    targets = {'G': t('G', '2026-09-01T00:00:00Z'), 'T': t('T', '2026-09-02T00:00:00Z'),
               'L': t('L', '2026-09-03T00:00:00Z'), 'SP': t('SP', '2026-09-04T00:00:00Z'),
               'BV': t('BV', '2026-09-05T00:00:00Z'), 'GE': t('GE', '2026-09-06T00:00:00Z'),
               'BE': t('BE', '2026-09-07T00:00:00Z'),
               'F': t('F', '2026-09-08T00:00:00Z' if film_newer == 'movies' else '2026-11-01T00:00:00Z')}
    film = film_bytes or _bytes('film')
    exports = {'M26': film, 'F': film, 'T': _bytes('tv'), 'L': _bytes('talent'), 'G': _bytes('game')}
    return FakeDrive(files, targets, exports)


class Base(unittest.TestCase):
    def setUp(self):
        self._cache = TS.CACHE_DIR
        TS.CACHE_DIR = tempfile.mkdtemp()
        for st in TS.STATUS.values():
            st.update(source='bundled', file='', file_id='', modified='', checked='', error='')

    def tearDown(self):
        TS.CACHE_DIR = self._cache
        REF.load()          # back to the bundled copies for other tests


class Selection(Base):
    def test_real_folder_names(self):
        expect = {'NEW Ingest Template - Movies (2026)': 'film',
                  'NEW Ingest Template - Film w/ Language': 'film',
                  'New Brand Definitions Ingest Template - TV': 'tv',
                  'Ingest Template - Talent': 'talent',
                  'New Video Game - Ingest Template': 'game',
                  'Ingest Template - Beauty': None, 'Ingest Template - Beverages': None,
                  'Professional Sports Teams - Ingest Template': None,
                  'Ingest Template - General': None,
                  'OLD Ingest Template - TV': None, 'Copy of Ingest Template - Talent': None}
        for name, cat in expect.items():
            with self.subTest(name=name):
                self.assertEqual(TS.category_for(name), cat)

    def test_newest_of_movies_and_film_wins(self):
        best = TS.pick_latest(TS.list_folder(_drive(film_newer='movies')))
        self.assertEqual(best['film']['name'], 'NEW Ingest Template - Movies (2026)')
        best = TS.pick_latest(TS.list_folder(_drive(film_newer='film')))
        self.assertEqual(best['film']['name'], 'NEW Ingest Template - Film w/ Language')
        self.assertEqual(best['tv']['id'], 'T')            # shortcut followed to target


class Sync(Base):
    def test_switches_to_drive_and_uses_new_rows(self):
        self.assertIsNone(REF.film_studio('Brand New Pictures'))
        drive = _drive(film_bytes=_with_studio('Brand New Pictures'))
        TS.sync(sess=drive)
        st = TS.status()['categories']
        for cat in ('film', 'tv', 'talent', 'game'):
            self.assertEqual(st[cat]['source'], 'drive', cat)
            self.assertEqual(st[cat]['error'], '', cat)
        self.assertEqual(st['film']['file'], 'NEW Ingest Template - Movies (2026)')
        self.assertIsNotNone(REF.film_studio('Brand New Pictures'))
        # the Generator picks the new studio up with no code change
        r = A.make_row('Some Film', True, 'Brand New Pictures', {'network': 'Brand New Pictures'})
        self.assertEqual(r['network'], 'Brand New Pictures')

    def test_unchanged_files_not_downloaded_again(self):
        drive = _drive()
        TS.sync(sess=drive)
        n = drive.downloads
        TS.sync(sess=drive)
        self.assertEqual(drive.downloads, n)
        TS.sync(sess=drive, force=True)
        self.assertGreater(drive.downloads, n)

    def test_broken_template_keeps_previous_copy(self):
        good = REF.counts()['FILM_STUDIOS']
        wb = openpyxl.Workbook(); wb.active.title = 'Something else'
        b = io.BytesIO(); wb.save(b)
        TS.sync(sess=_drive(film_bytes=b.getvalue()))
        st = TS.status()['categories']['film']
        self.assertEqual(st['source'], 'bundled')
        self.assertIn('missing sheet', st['error'])
        self.assertEqual(REF.counts()['FILM_STUDIOS'], good)

    def test_too_few_rows_rejected(self):
        wb = openpyxl.load_workbook(os.path.join(REF_DIR, BUNDLED['film']))
        ws = wb['DropDown']
        ws.delete_rows(3, ws.max_row)
        b = io.BytesIO(); wb.save(b)
        TS.sync(sess=_drive(film_bytes=b.getvalue()))
        self.assertIn('FILM_STUDIOS has only', TS.status()['categories']['film']['error'])

    def test_no_credentials_stays_on_bundled(self):
        old = os.environ.pop('GOOGLE_SERVICE_ACCOUNT_JSON', None)
        try:
            TS.sync()
            self.assertFalse(TS.STATE['enabled'])
            self.assertIn('not set', TS.STATE['last_error'])
        finally:
            if old is not None:
                os.environ['GOOGLE_SERVICE_ACCOUNT_JSON'] = old


class Endpoints(Base):
    def test_status_endpoint(self):
        r = A.app.test_client().get('/api/templates/status')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.get_json()['categories']), {'film', 'tv', 'talent', 'game'})

    def test_pages_show_templates_line(self):
        c = A.app.test_client()
        for page in ('/', '/review'):
            self.assertIn(b'tplInfo', c.get(page).data)


if __name__ == '__main__':
    unittest.main()
