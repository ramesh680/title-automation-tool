# Metacritic URL & Social Account Validation — Changes and Recommendations

*July 8, 2026 — addresses the two issues observed on title-automation-tool.onrender.com: invalid Metacritic URLs and suspended/dead social accounts in generated metadata.*

## Root causes found

**Metacritic.** The upcoming-release-movies calendar service *guesses* every Metacritic URL by slugifying the title (`metacritic.com/movie/<slug>/`) without ever checking the page exists. In `metadata_fetcher.py` that guess had the highest merge priority, so it overrode the curated Wikidata URL (P1712) even when the guess was a dead link. Nothing in the pipeline ever verified a Metacritic URL — the validator only checks the URL *format*.

**Socials.** Twitter/Instagram/Facebook handles come from Wikidata and TMDB with no filtering. The code even ignored Wikidata's own signals that an account is dead: claims marked with an "end time" (P582) qualifier or deprecated rank were used just like current ones, and no live check was ever made.

## What changed (metadata_fetcher.py)

**Metacritic — only verified URLs survive.**

- New `resolve_metacritic()` verifies candidates with a real HTTP check (final status after redirects).
- A slug **guess** (from the calendar service) is kept only when the page verifiably returns 200. On 404 it is discarded and a slugged fallback is tried under both `/movie/` and `/tv/` — again only accepted on a verified 200.
- A **curated** URL (Wikidata P1712) is trusted: dropped only on a definitive 404/410, kept when Metacritic blocks/throttles the check.
- If nothing verifies, the field is left blank — a blank cell Ops can fill is better than a broken link.
- Applied to films, TV and video games (`fetch_game`).

**Socials — three layers.**

1. `_claim_values()` now skips deprecated-rank Wikidata claims and social claims carrying an end-time qualifier (how Wikidata marks closed/renamed/suspended accounts), and prefers preferred-rank claims (the current account).
2. New `verify_socials()` live-checks each handle before output: Twitter via the keyless oEmbed endpoint (404 = nonexistent or suspended), Instagram and Facebook via direct page status (404/410 = gone).
3. Checks **fail open**: a handle is removed only on a definitive 404 — login walls, bot blocks and rate limits (403/429/timeouts) never strip a valid account.

Applied to titles, talent (`fetch_person`) and games.

**Operational details.**

- Results are cached per process, so repeated titles in one workbook cost one check each.
- Kill switch: set env `VALIDATE_URLS=0` to restore the old (unvalidated) behavior; `VALIDATE_TIMEOUT_SECONDS` (default 6) tunes the check timeout.
- Expect roughly 1–4 extra HTTP requests per new title (~2–5 s worst case); no API keys needed.

## Known limitations

- X/Twitter suspension detection depends on the oEmbed endpoint returning 404; if X changes that behavior, suspended accounts may pass again. A paid X API lookup is the only fully reliable check.
- Instagram/Facebook sometimes serve login walls instead of 404 from datacenter IPs; those accounts are kept (fail-open), so an occasional dead account can still slip through — but valid accounts are never lost.
- Metacritic occasionally bot-blocks (403); in that case a guessed URL is dropped (safe) and a curated one is kept (safe).

## Recommended follow-ups

1. **Fix the guess at the source**: in `media-tools-hub/upcoming_movies/upcoming_release_movies_app.py`, `metacritic_url_for_title()` should verify the slug (or emit empty) instead of shipping unverified guesses to every consumer. Cheap to do at scrape time since results are cached.
2. **Redeploy** title-automation-tool on Render to pick up these changes.
3. Optionally add a liveness check (not just format check) to `validator.py`'s `metacritic_url_format` rule so the Validator tab also flags dead links in uploaded workbooks.

## Verification

23 unit tests (mocked HTTP) cover: slug building (punctuation, `&`, accents), guess-404 dropped, verified fallback found, curated kept/dropped correctly, Wikidata end-time/deprecated/preferred filtering, dead-account removal, fail-open behavior, and the `VALIDATE_URLS=0` kill switch. All pass; the module compiles clean.
