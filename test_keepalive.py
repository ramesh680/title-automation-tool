"""Keep-alive while a job runs (Nov 2026): Render's free tier stopped the
instance 15 min after the last inbound request, killing long reviews whose
browser tab had stopped polling. While a job runs the app pings its own public
URL; with no job running it sends nothing."""
import threading
import time
import unittest
from unittest import mock

import app as A


class KeepAlive(unittest.TestCase):
    def setUp(self):
        self._url, self._every = A._KEEPALIVE_URL, A._KEEPALIVE_EVERY
        A._KEEPALIVE_URL, A._KEEPALIVE_EVERY = 'https://example.test', 0.05
        A._keepalive_running = False

    def tearDown(self):
        with A._JOBS_LOCK:
            A._JOBS.pop('ka-test', None)
        deadline = time.time() + 2
        while A._keepalive_running and time.time() < deadline:
            time.sleep(0.02)
        A._KEEPALIVE_URL, A._KEEPALIVE_EVERY = self._url, self._every

    def test_pings_only_while_a_job_runs(self):
        hits = []
        with mock.patch('requests.get', side_effect=lambda u, **k: hits.append(u)):
            with A._JOBS_LOCK:
                A._JOBS['ka-test'] = {'status': 'running', 'created': time.time()}
            self.assertTrue(A._ensure_keepalive())
            self.assertTrue(A._ensure_keepalive())          # no second thread
            self.assertEqual(sum(t.name == 'keepalive' for t in threading.enumerate()), 1)
            time.sleep(0.3)
            self.assertTrue(hits)
            self.assertTrue(all(u == 'https://example.test/api/ping' for u in hits))
            A._job_set('ka-test', status='done')
            time.sleep(0.3)
            self.assertFalse(A._keepalive_running)          # stops when idle
            n = len(hits)
            time.sleep(0.2)
            self.assertEqual(len(hits), n)

    def test_no_url_no_thread(self):
        A._KEEPALIVE_URL = ''
        self.assertFalse(A._ensure_keepalive())

    def test_ping_endpoint(self):
        r = A.app.test_client().get('/api/ping')
        self.assertEqual(r.status_code, 204)


if __name__ == '__main__':
    unittest.main()
