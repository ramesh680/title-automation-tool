import unittest
from app import build_youtube_username
import metadata_fetcher as mf

class BuildYouTubeUsername(unittest.TestCase):
    def test_multiple_own_channels_one_per_line_then_network(self):
        cell = build_youtube_username("http://www.youtube.com/@studio", "Aurora",
            own_channel="http://www.youtube.com/channel/UC1\nhttp://www.youtube.com/channel/UC2")
        lines = cell.split("\n")
        self.assertEqual(lines[0], "http://www.youtube.com/channel/UC1")
        self.assertEqual(lines[1], "http://www.youtube.com/channel/UC2")
        self.assertEqual(lines[2], "http://www.youtube.com/@studio|aurora")
    def test_duplicate_own_collapsed(self):
        self.assertEqual(build_youtube_username("", "Aurora",
            own_channel="http://x/UC1\nhttp://x/UC1"), "http://x/UC1")
    def test_distributor_not_repeated(self):
        self.assertEqual(build_youtube_username("http://www.youtube.com/@studio","Aurora",
            own_channel="http://www.youtube.com/@studio"), "http://www.youtube.com/@studio|aurora")

class YouTubeChannelMulti(unittest.TestCase):
    def setUp(self):
        self._gj, self._k = mf._get_json, mf.YOUTUBE_API_KEY
        mf.YOUTUBE_API_KEY = "k"
    def tearDown(self):
        mf._get_json, mf.YOUTUBE_API_KEY = self._gj, self._k
    def test_two_valid_channels_returned(self):
        mf._get_json = lambda *a, **k: {"items":[
            {"snippet":{"title":"Aurora","channelId":"UC%022d"%1}},
            {"snippet":{"title":"Aurora Movie","channelId":"UC%022d"%2}}]}
        got = mf.youtube_channel("Aurora")["youtube_own_channel"].split("\n")
        self.assertEqual(len(got),2)
    def test_band_suffix_rejected(self):
        mf._get_json = lambda *a, **k: {"items":[
            {"snippet":{"title":"Aurora Music","channelId":"UCx"}}]}
        self.assertEqual(mf.youtube_channel("Aurora"), {})
if __name__=="__main__": unittest.main()
