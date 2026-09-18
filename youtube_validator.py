"""
youtube_validator.py
--------------------
Decide whether a YouTube channel is a title's OFFICIAL channel or a fan /
clips / reaction / tribute channel.

Used by metadata_fetcher.youtube_channel(): a channel that merely NAMES itself
after the title is not enough -- fan channels do that too, which is how
unofficial channels reached the youtube_channel_username column. Each candidate
is scored on several independent signals and must clear OFFICIAL_THRESHOLD.

Signals (additive):
    verified badge / official artist channel ......... +100
    "official" wording in the channel title .......... +15
    "official" wording in the channel description .....+10
    studio / distributor / publisher named in the
      description (Warner Bros., Netflix, AMC, ...) ... +10
    1,000,000+ subscribers ............................ +12
      100,000+ subscribers ............................ +10
       25,000+ subscribers ............................ + 4
    a custom URL / handle (@name) ..................... + 3

Disqualifiers (score forced to 0, channel rejected outright):
    fan, fanpage, unofficial, tribute, clips, highlights, reaction, recap,
    edits, parody, archive, shorts-only, "not affiliated", "we do not own"

No external dependencies: standard library only, safe to import anywhere.
"""

import os
import re

__all__ = [
    "YouTubeChannelValidator",
    "YouTubeResultProcessor",
    "filter_youtube_channels",
    "OFFICIAL_THRESHOLD",
]

#: Minimum score for a channel to count as official.
try:
    OFFICIAL_THRESHOLD = int(os.getenv("YOUTUBE_OFFICIAL_THRESHOLD", "15"))
except ValueError:
    OFFICIAL_THRESHOLD = 15

# ---- signal vocabularies ---------------------------------------------------

#: Wording that marks a channel as the rights-holder's own.
_OFFICIAL_WORDS = (
    "official", "officiel", "oficial", "verified",
    "official channel", "official page", "official account",
    "subscribe for official", "the official home",
)

#: Studios / networks / distributors / publishers. A channel whose description
#: names one is very likely to be run by (or on behalf of) the rights holder.
_RIGHTS_HOLDERS = (
    "warner bros", "warner brothers", "warnermedia", "hbo", "max",
    "universal pictures", "nbcuniversal", "focus features",
    "paramount", "sony pictures", "columbia pictures", "tristar",
    "20th century", "searchlight", "walt disney", "disney", "pixar",
    "marvel", "lucasfilm", "netflix", "amazon mgm", "prime video", "mgm",
    "apple tv", "a24", "neon", "lionsgate", "amc", "fx networks", "amc networks",
    "bbc", "itv", "channel 4", "sky", "hulu", "peacock", "starz", "showtime",
    "nintendo", "playstation", "xbox", "bandai namco", "square enix",
    "ubisoft", "electronic arts", "ea games", "activision", "bethesda",
    "fromsoftware", "capcom", "sega", "rockstar games", "2k games",
    "devolver digital", "annapurna",
)

#: Anything here means the channel is NOT the rights holder's.
_DISQUALIFIERS = (
    "fan channel", "fan page", "fanpage", "fan account", "fanmade", "fan made",
    "fan edit", "fanedit", "fandom", " fan ", "fans of", "unofficial",
    "not official", "not affiliated", "no affiliation", "not associated with",
    "we do not own", "i do not own", "do not own any", "no copyright intended",
    "tribute", "homage channel", "clips", "clip channel", "best scenes",
    "best moments", "highlights", "compilation", "supercut",
    "reaction", "reacts", "reacting", "review channel", "recap", "recaps",
    "explained", "theory", "theories", "edits", "edit channel", "amv",
    "parody", "spoof", "meme", "memes", "archive channel", "unboxing",
    "walkthrough", "let's play", "lets play", "gameplay channel", "speedrun",
    "leak", "leaks", "concept trailer", "fan trailer", "fan film",
)

_WS = re.compile(r"\s+")


def _text(value):
    """Lowercase, whitespace-collapsed text with a leading/trailing space, so a
    phrase such as ' fan ' can be matched on word boundaries."""
    return " " + _WS.sub(" ", str(value or "").strip().lower()) + " "


class YouTubeChannelValidator:
    """Scores YouTube channels and decides which ones are official.

    A `channel` is a plain dict. Recognised keys (all optional except one of
    title / description):

        channel_id, title, description, url, custom_url,
        subscriber_count (int), is_verified (bool)
    """

    def __init__(self, threshold=None):
        self.threshold = OFFICIAL_THRESHOLD if threshold is None else int(threshold)

    # -- individual checks --------------------------------------------------

    @staticmethod
    def is_disqualified(channel):
        """True when the channel's title or description marks it as a fan /
        clips / reaction channel. Such channels are rejected whatever else
        they score."""
        blob = _text(channel.get("title")) + _text(channel.get("description"))
        return any(bad in blob for bad in _DISQUALIFIERS)

    @staticmethod
    def _title_is_bare_match(channel, title):
        """True when the channel name IS the title with nothing else attached.
        A bare exact name is weak evidence on its own (fan channels use it too),
        so it earns no points -- it only stops an official channel that omits
        the word 'official' from being rejected out of hand."""
        norm = lambda s: re.sub(r"[^a-z0-9]", "", str(s or "").lower())  # noqa: E731
        return bool(title) and norm(channel.get("title")) == norm(title)

    def score(self, channel, title=None):
        """Total score for a channel. 0 means disqualified or no evidence."""
        if not channel:
            return 0
        if self.is_disqualified(channel):
            return 0

        points = 0
        name = _text(channel.get("title"))
        desc = _text(channel.get("description"))

        # strongest signal: YouTube's own verification / artist badge
        if channel.get("is_verified"):
            points += 100

        if any(w in name for w in _OFFICIAL_WORDS):
            points += 15
        if any(w in desc for w in _OFFICIAL_WORDS):
            points += 10

        if any(h in desc for h in _RIGHTS_HOLDERS) or \
                any(h in name for h in _RIGHTS_HOLDERS):
            points += 10

        try:
            subs = int(channel.get("subscriber_count") or 0)
        except (TypeError, ValueError):
            subs = 0
        if subs >= 1_000_000:
            points += 12
        elif subs >= 100_000:
            points += 10
        elif subs >= 25_000:
            points += 4

        if channel.get("custom_url"):
            points += 3

        # an exact-name channel with real reach and no fan markers is accepted
        if self._title_is_bare_match(channel, title) and subs >= 100_000:
            points += 5

        return points

    def is_official(self, channel, title=None):
        """True when the channel clears the official threshold."""
        return self.score(channel, title=title) >= self.threshold

    def filter(self, channels, title=None):
        """Keep only the official channels from an iterable of channel dicts,
        highest-scoring first."""
        scored = []
        for ch in channels or []:
            s = self.score(ch, title=title)
            if s >= self.threshold:
                scored.append((s, ch))
        scored.sort(key=lambda p: p[0], reverse=True)
        return [ch for _s, ch in scored]


class YouTubeResultProcessor:
    """Turns raw YouTube Data API search/channel items into channel dicts and
    filters them to official channels only."""

    def __init__(self, validator=None):
        self.validator = validator or YouTubeChannelValidator()

    @staticmethod
    def to_channel(item):
        """Normalise a YouTube API `search.list` or `channels.list` item."""
        item = item or {}
        snip = item.get("snippet", {}) or {}
        stats = item.get("statistics", {}) or {}
        cid = (snip.get("channelId")
               or (item.get("id") if isinstance(item.get("id"), str) else None)
               or (item.get("id", {}) or {}).get("channelId"))
        try:
            subs = int(stats.get("subscriberCount") or 0)
        except (TypeError, ValueError):
            subs = 0
        return {
            "channel_id": cid,
            "title": snip.get("title") or "",
            "description": snip.get("description") or "",
            "custom_url": snip.get("customUrl") or "",
            "url": ("http://www.youtube.com/channel/" + cid) if cid else "",
            "subscriber_count": subs,
            "is_verified": False,
        }

    def process(self, items, title=None):
        """Official channel dicts from a list of API items."""
        return self.validator.filter(
            [self.to_channel(i) for i in (items or [])], title=title)

    def official_urls(self, items, title=None):
        """Just the URLs of the official channels, de-duplicated, in order."""
        urls = []
        for ch in self.process(items, title=title):
            u = ch.get("url")
            if u and u not in urls:
                urls.append(u)
        return urls


def filter_youtube_channels(channels, title=None):
    """Convenience wrapper accepting a single URL, a list of URLs, or a list of
    channel dicts, and returning the same shape with unofficial channels
    removed.

    A bare URL carries no title, description or subscriber count, so there is
    nothing to score: URLs are passed through unchanged unless the URL itself
    contains a disqualifying word. Prefer passing channel dicts (or use
    YouTubeResultProcessor on raw API items) for real filtering.
    """
    validator = YouTubeChannelValidator()

    def keep_url(u):
        return not any(bad.strip() in _text(u) for bad in _DISQUALIFIERS)

    if channels is None:
        return channels
    if isinstance(channels, str):
        return channels if keep_url(channels) else ""
    if isinstance(channels, dict):
        return channels if validator.is_official(channels, title=title) else None

    out = []
    for ch in channels:
        if isinstance(ch, str):
            if keep_url(ch):
                out.append(ch)
        elif isinstance(ch, dict):
            if validator.is_official(ch, title=title):
                out.append(ch)
    return out
