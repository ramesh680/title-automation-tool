"""pytest config: keep the legacy suites hermetic.

The Sep 2026 cross-source checks (Rotten Tomatoes / Metacritic page reads,
trailer-description socials, handle probes) make live requests. The older
suites mock only the calls they know about, so these extra lookups are
switched off for them; test_cross_source.py switches them on explicitly with
every network call mocked."""
import os

os.environ.setdefault("VERIFY_PAGE_CONTENT", "0")
os.environ.setdefault("SOCIAL_DISCOVERY", "0")
os.environ.setdefault("SOCIAL_GUESSING", "0")
