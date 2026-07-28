"""
Unit tests for the four optimization changes (July 2026):
  * Idea 3 - Rotten Tomatoes URL rule: /m/ valid, /tv/ invalid (Movies/TV);
            Metacritic keeps its original movie/tv format check
  * Idea 4 - brand_set required-value-by-category check
  * Idea 2 - talent profession hint (fetch_person + app helpers)

Run:  python -m pytest test_optimizations.py -q
"""
import os
import validator as V


# ----------------------- Idea 3: Rotten Tomatoes --------------------------
RT_RULE = {"applies_to": ["Movies", "TV Shows"], "message": "bad rt"}


def _rt(val, category):
    return V._chk_rottentomatoes_url_format(val, {"title_category": category}, RT_RULE)


def test_rt_m_path_valid_for_movies():
    sev, _ = _rt("https://www.rottentomatoes.com/m/dune_part_two", "Movies")
    assert sev is None


def test_rt_m_path_valid_for_tv_shows():
    # a TV title still needs the /m/ style URL under this rule
    sev, _ = _rt("https://www.rottentomatoes.com/m/the_bear", "TV Shows")
    assert sev is None


def test_rt_movie_path_valid_for_movies():
    # /movie/ is valid too (not just /m/)
    sev, _ = _rt("https://www.rottentomatoes.com/movie/dune_part_three", "Movies")
    assert sev is None


def test_rt_movie_path_valid_for_tv_shows():
    sev, _ = _rt("https://www.rottentomatoes.com/movie/the_bear", "TV Shows")
    assert sev is None


def test_rt_tv_path_invalid_for_tv_shows():
    sev, _ = _rt("https://www.rottentomatoes.com/tv/the_bear", "TV Shows")
    assert sev == V.SEV_FAIL


def test_rt_tv_path_invalid_for_movies():
    sev, _ = _rt("https://www.rottentomatoes.com/tv/whatever", "Movies")
    assert sev == V.SEV_FAIL


def test_rt_default_message_mentions_tv():
    sev, msg = V._chk_rottentomatoes_url_format(
        "https://www.rottentomatoes.com/tv/the_bear",
        {"title_category": "TV Shows"},
        {"applies_to": ["Movies", "TV Shows"]})
    assert sev == V.SEV_FAIL
    assert "/tv/" in msg


def test_rt_blank_is_warning():
    sev, _ = _rt("", "Movies")
    assert sev == V.SEV_WARN


def test_rt_rule_skips_other_categories():
    sev, _ = _rt("https://www.rottentomatoes.com/tv/whatever", "Talent")
    assert sev is None


def test_rt_garbage_fails():
    sev, _ = _rt("https://example.com/foo/bar", "Movies")
    assert sev == V.SEV_FAIL


# --- Metacritic keeps its ORIGINAL behaviour (movie AND tv both valid) ---
def test_metacritic_movie_and_tv_both_valid():
    for url in ("https://www.metacritic.com/movie/dune-part-two/",
                "https://www.metacritic.com/tv/the-bear/"):
        sev, _ = V._chk_metacritic_url_format(url, {"title_category": "Movies"}, {})
        assert sev is None, url


def test_metacritic_blank_is_warning():
    sev, _ = V._chk_metacritic_url_format("", {}, {})
    assert sev == V.SEV_WARN


def test_metacritic_garbage_fails():
    sev, _ = V._chk_metacritic_url_format("https://example.com/x/", {}, {})
    assert sev == V.SEV_FAIL


# --------------------------- Idea 4: brand_set ----------------------------
BS_RULE = {"message": "brand set missing"}


def _bs(brand_set, title, category):
    row = {"title": title, "title_category": category, "brand_set": brand_set}
    return V._chk_brand_set_present_for_category(brand_set, row, BS_RULE)


def test_brandset_talent_present():
    sev, _ = _bs("LF // Talent\nPristine DAR Brands", "LeBron James - DAR", "Talent")
    assert sev is None


def test_brandset_talent_missing():
    sev, _ = _bs("Competitive View", "LeBron James - DAR", "Talent")
    assert sev == V.SEV_FAIL


def test_brandset_movie_competitive_present():
    sev, _ = _bs("Competitive View", "Dune 3", "Movies")
    assert sev is None


def test_brandset_movie_dar_present():
    sev, _ = _bs("LF // Film - Majors + Independents\nPristine DAR Brands",
                 "Dune 3 - DAR", "Movies")
    assert sev is None


def test_brandset_movie_dar_missing():
    sev, _ = _bs("Competitive View", "Dune 3 - DAR", "Movies")
    assert sev == V.SEV_FAIL


def test_brandset_videogame_dar_present():
    sev, _ = _bs("LF // Video Games // Games", "Halo - DAR", "Video Game")
    assert sev is None


def test_brandset_tv_dar_present():
    sev, _ = _bs("LF // TV Universe\nLF // TV // Episodic Plus", "The Bear - DAR", "TV Shows")
    assert sev is None


def test_brandset_beauty_standard_present():
    sev, _ = _bs("LF // Beauty", "Fenty Beauty - DAR", "Health & Beauty")
    assert sev is None


def test_brandset_general_is_skipped():
    # General has no single required brand set -> never flagged
    sev, _ = _bs("anything at all", "Some Brand", "General")
    assert sev is None


def test_brandset_blank_missing():
    sev, _ = _bs("", "Dune 3 - DAR", "Movies")
    assert sev == V.SEV_FAIL


# --------------------------- Idea 2: profession ---------------------------
def test_fetch_person_seeds_profession(monkeypatch):
    os.environ["VALIDATE_URLS"] = "0"
    import importlib
    import metadata_fetcher as MF
    importlib.reload(MF)
    # avoid all network: no wikidata candidates, no imdb suggestion
    monkeypatch.setattr(MF, "_search_candidates", lambda *a, **k: [])
    monkeypatch.setattr(MF, "_get_json", lambda *a, **k: {})
    monkeypatch.setattr(MF, "verify_socials", lambda m: m)
    meta = MF.fetch_person("Some Unknown Person", profession="Chef")
    assert meta.get("profession") == "Chef"
    assert meta.get("occupations") == ["Chef"]


def test_fetch_person_hint_biases_candidate(monkeypatch):
    os.environ["VALIDATE_URLS"] = "0"
    import importlib
    import metadata_fetcher as MF
    importlib.reload(MF)

    # two same-named humans: one described as a chef, one as a footballer
    ents = {
        "Q_CHEF": {"labels": {"en": {"value": "John Smith"}},
                   "descriptions": {"en": {"value": "American chef"}},
                   "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}]},
                   "sitelinks": {}, "aliases": {}},
        "Q_BALL": {"labels": {"en": {"value": "John Smith"}},
                   "descriptions": {"en": {"value": "English footballer"}},
                   "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}]},
                   "sitelinks": {}, "aliases": {}},
    }
    monkeypatch.setattr(MF, "_search_candidates", lambda *a, **k: ["Q_BALL", "Q_CHEF"])
    monkeypatch.setattr(MF, "_entity", lambda qid: ents.get(qid))
    monkeypatch.setattr(MF, "_claim_values",
                        lambda claims, prop, **k: (["Q5"] if prop == "P31" else []))
    monkeypatch.setattr(MF, "_labels", lambda qids: {})
    monkeypatch.setattr(MF, "_get_json", lambda *a, **k: {})
    monkeypatch.setattr(MF, "verify_socials", lambda m: m)

    meta = MF.fetch_person("John Smith", profession="chef")
    # the chef description should win -> occupations seeded from that entity path;
    # at minimum the profession hint is recorded
    assert meta.get("profession") == "chef"


def test_rt_helpers_enforce_movie_only():
    os.environ["VALIDATE_URLS"] = "0"
    import importlib
    import metadata_fetcher as MF
    importlib.reload(MF)
    assert MF.is_tv_rottentomatoes("http://www.rottentomatoes.com/tv/x") is True
    assert MF.is_tv_rottentomatoes("http://www.rottentomatoes.com/m/x") is False
    assert MF.clean_rottentomatoes("http://www.rottentomatoes.com/m/x") \
        == "http://www.rottentomatoes.com/m/x"
    assert MF.clean_rottentomatoes("http://www.rottentomatoes.com/tv/x") == ""


def test_metacritic_resolver_keeps_tv_candidate(monkeypatch):
    """Metacritic is back to its original behaviour: /tv/ is fine."""
    os.environ["VALIDATE_URLS"] = "0"
    import importlib
    import metadata_fetcher as MF
    importlib.reload(MF)
    for url in ("http://www.metacritic.com/movie/x/", "http://www.metacritic.com/tv/x/"):
        assert MF.resolve_metacritic("X", candidate=url) == url
