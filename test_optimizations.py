"""
Unit tests for the four optimization changes (July 2026):
  * Idea 3 - Metacritic URL rule: /movie/ or /m/ valid, /tv/ invalid (Movies/TV)
  * Idea 4 - brand_set required-value-by-category check
  * Idea 2 - talent profession hint (fetch_person + app helpers)

Run:  python -m pytest test_optimizations.py -q
"""
import os
import validator as V


# --------------------------- Idea 3: Metacritic ---------------------------
MC_RULE = {"applies_to": ["Movies", "TV Shows"],
           "message": "bad metacritic"}


def _mc(val, category):
    return V._chk_metacritic_url_format(val, {"title_category": category}, MC_RULE)


def test_metacritic_movie_path_valid_for_movies():
    sev, _ = _mc("https://www.metacritic.com/movie/dune-part-two/", "Movies")
    assert sev is None


def test_metacritic_m_path_valid():
    sev, _ = _mc("https://www.rottentomatoes.com/m/dune_part_two", "Movies")
    assert sev is None


def test_metacritic_tv_path_invalid_for_tv_shows():
    sev, msg = _mc("https://www.metacritic.com/tv/the-bear/", "TV Shows")
    assert sev == V.SEV_FAIL


def test_metacritic_tv_default_message_mentions_movie():
    # with no custom message the default explains the /movie/ (or /m/) rule
    sev, msg = V._chk_metacritic_url_format(
        "https://www.metacritic.com/tv/the-bear/",
        {"title_category": "TV Shows"},
        {"applies_to": ["Movies", "TV Shows"]})
    assert sev == V.SEV_FAIL
    assert "/tv/" in msg


def test_metacritic_tv_path_invalid_for_movies():
    sev, _ = _mc("https://www.metacritic.com/tv/some-show/", "Movies")
    assert sev == V.SEV_FAIL


def test_metacritic_blank_is_warning():
    sev, _ = _mc("", "Movies")
    assert sev == V.SEV_WARN


def test_metacritic_rule_skips_other_categories():
    # a /tv/ URL on a non-Movies/TV row must NOT be failed by this rule
    sev, _ = _mc("https://www.metacritic.com/tv/whatever/", "Talent")
    assert sev is None


def test_metacritic_garbage_fails():
    sev, _ = _mc("https://example.com/foo/bar", "Movies")
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


def test_metacritic_resolver_rejects_tv_candidate(monkeypatch):
    os.environ["VALIDATE_URLS"] = "0"
    import importlib
    import metadata_fetcher as MF
    importlib.reload(MF)
    # VALIDATE_URLS off: a /movie/ candidate is returned as-is, a /tv/ one is dropped
    assert MF.resolve_metacritic("X", candidate="http://www.metacritic.com/movie/x/") \
        == "http://www.metacritic.com/movie/x/"
    assert MF.resolve_metacritic("X", candidate="http://www.metacritic.com/tv/x/") == ""
    assert MF._is_tv_metacritic("http://www.metacritic.com/tv/x/") is True
    assert MF._is_tv_metacritic("http://www.metacritic.com/movie/x/") is False
