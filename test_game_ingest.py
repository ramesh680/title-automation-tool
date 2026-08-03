"""Tests for the Video Game ingest row.

    python -m unittest test_game_ingest -v

The expected values are taken verbatim from the 2026-07-31 ingest
(20260731ApplyBrandDefinitionReport) - Akatori, ReStory, Skatesterre,
Aliens: Fireteam Elite 2, NBA 2K27 and Echo Weaver - which is the reference for
what a Video Game row must look like.
"""
from __future__ import annotations

import unittest

from app import (
    GAME_CONFIRM_DEVELOPER,
    GAME_CONFIRM_NETWORK,
    GAME_CONFIRM_PLATFORMS,
    GAME_DAR_BRAND_SET,
    GAME_PLATFORM_VOCAB,
    _game_platform_lines,
    _game_search_terms,
    _game_youtube_lines,
    create_game_row,
)


AKATORI = {
    "developer": "Contrast Games",
    "network": "Contrast Games",
    "platforms": ["PC", "PS5", "PS4", "Switch 2", "Xbox One", "Xbox Series X"],
    "genre": "Metroidvania",
    "primary_genre": "Platformer",
    "released_on": "2026-08-05",
    "facebook_page": "https://www.facebook.com/Akatorigame",
    "twitter_handle": "https://x.com/AKATORIGAME",
    "instagram_user": "akatorigame",
    "metacritic": "https://www.metacritic.com/game/akatori/",
}


class BrandSetTests(unittest.TestCase):
    def test_dar_row_carries_both_brand_sets_one_per_line(self):
        row = create_game_row("Akatori - DAR", dict(AKATORI))
        self.assertEqual(row["brand_set"], "LF // Video Games // Games\nPristine DAR Brands")

    def test_pristine_dar_brands_is_the_second_line(self):
        self.assertEqual(GAME_DAR_BRAND_SET.split("\n")[1], "Pristine DAR Brands")

    def test_an_explicit_brand_set_still_wins(self):
        row = create_game_row("Akatori - DAR", dict(AKATORI, brand_set="LF // Custom"))
        self.assertEqual(row["brand_set"], "LF // Custom")

    def test_non_dar_row_is_unaffected(self):
        self.assertEqual(create_game_row("Akatori", dict(AKATORI))["brand_set"],
                         "Competitive View")


class SubCategoryTests(unittest.TestCase):
    def test_developer_line_comes_first(self):
        sub = create_game_row("Akatori - DAR", dict(AKATORI))["title_sub_category"]
        self.assertEqual(sub.split("\n")[0], "Developer - Contrast Games")

    def test_all_six_platforms_are_listed_in_vocabulary_order(self):
        sub = create_game_row("Akatori - DAR", dict(AKATORI))["title_sub_category"]
        self.assertEqual(
            sub,
            "Developer - Contrast Games\nPlatform - PC\nPlatform - PS5\nPlatform - PS4\n"
            "Platform - Switch 2\nPlatform - Xbox One\nPlatform - Xbox Series X",
        )

    def test_a_pc_only_game_gets_one_platform_line(self):
        sub = create_game_row(
            "ReStory: Chill Electronics Repairs - DAR",
            {"developer": "Mandragora", "network": "tinyBuild", "platforms": ["PC"]},
        )["title_sub_category"]
        self.assertEqual(sub, "Developer - Mandragora\nPlatform - PC")

    def test_platform_order_is_independent_of_input_order(self):
        forward = _game_platform_lines(["PC", "PS5", "Switch 2", "Xbox Series X"])
        shuffled = _game_platform_lines(["Xbox Series X", "Switch 2", "PS5", "PC"])
        self.assertEqual(forward, shuffled)
        self.assertEqual(forward, ["Platform - PC", "Platform - PS5",
                                   "Platform - Switch 2", "Platform - Xbox Series X"])

    def test_long_platform_names_are_normalised(self):
        self.assertEqual(
            _game_platform_lines(["Microsoft Windows", "PlayStation 5", "Nintendo Switch 2",
                                  "Xbox Series X/S"]),
            ["Platform - PC", "Platform - PS5", "Platform - Switch 2",
             "Platform - Xbox Series X"],
        )

    def test_vocabulary_is_exactly_six_values(self):
        self.assertEqual(len(GAME_PLATFORM_VOCAB), 6)

    def test_never_emits_more_than_six_platform_lines(self):
        lines = _game_platform_lines(GAME_PLATFORM_VOCAB + ["PS2", "Game Boy", "Mobile"])
        self.assertEqual(len(lines), 6)

    def test_platforms_outside_the_vocabulary_are_dropped(self):
        self.assertEqual(_game_platform_lines(["PS2", "Game Boy", "iOS"]), [])

    def test_duplicate_platforms_collapse(self):
        self.assertEqual(_game_platform_lines(["PC", "Windows", "Microsoft Windows"]),
                         ["Platform - PC"])

    def test_missing_developer_is_flagged_not_silently_dropped(self):
        sub = create_game_row("Mystery Game - DAR", {"platforms": ["PC"], "network": "X"})
        self.assertEqual(sub["title_sub_category"].split("\n")[0], GAME_CONFIRM_DEVELOPER)

    def test_missing_platforms_are_flagged_not_silently_dropped(self):
        sub = create_game_row("Mystery Game - DAR", {"developer": "Dev", "network": "X"})
        self.assertIn(GAME_CONFIRM_PLATFORMS, sub["title_sub_category"])

    def test_sub_category_is_never_blank(self):
        self.assertTrue(create_game_row("Mystery Game - DAR", {})["title_sub_category"].strip())

    def test_an_explicit_sub_category_is_passed_through_untouched(self):
        row = create_game_row("Akatori - DAR",
                              dict(AKATORI, title_sub_category="Developer - Someone"))
        self.assertEqual(row["title_sub_category"], "Developer - Someone")

    def test_a_prefixed_developer_value_is_not_double_prefixed(self):
        row = create_game_row("Akatori - DAR",
                              {"developer": "Developer - Contrast Games", "platforms": ["PC"]})
        self.assertEqual(row["title_sub_category"].split("\n")[0], "Developer - Contrast Games")


class NetworkTests(unittest.TestCase):
    def test_network_is_the_publisher(self):
        self.assertEqual(create_game_row("Skatesterre - DAR",
                                         {"developer": "Goon Squad", "network": "Headup Games",
                                          "platforms": ["PC"]})["network"],
                         "Headup Games")

    def test_a_self_published_game_falls_back_to_the_developer(self):
        row = create_game_row("Akatori - DAR",
                              {"developer": "Contrast Games", "platforms": ["PC"]})
        self.assertEqual(row["network"], "Contrast Games")

    def test_network_is_never_blank(self):
        self.assertEqual(create_game_row("Mystery Game - DAR", {})["network"],
                         GAME_CONFIRM_NETWORK)

    def test_a_missing_network_is_flagged_for_ops(self):
        self.assertIn("CONFIRM", create_game_row("Mystery Game - DAR", {})["network"])


class TwitterSearchTermTests(unittest.TestCase):
    def test_matches_the_july_31_format(self):
        row = create_game_row("Akatori - DAR", dict(AKATORI))
        self.assertEqual(row["twitter_search_terms"], "#Akatori|DAR\n#AkatorivideoGame|DAR")

    def test_capitalisation_of_the_title_is_preserved(self):
        self.assertEqual(_game_search_terms("NBA 2K27", True),
                         "#NBA2K27|DAR\n#NBA2K27videoGame|DAR")

    def test_punctuation_is_stripped_from_the_hashtag(self):
        self.assertEqual(
            _game_search_terms("ReStory: Chill Electronics Repairs", True).split("\n")[0],
            "#ReStoryChillElectronicsRepairs|DAR",
        )

    def test_colon_titles_match_the_july_31_output(self):
        row = create_game_row("Aliens: Fireteam Elite 2 - DAR",
                              {"developer": "Cold Iron Studios", "network": "Daybreak Games",
                               "platforms": ["PS5", "Xbox Series X", "PC"]})
        self.assertEqual(row["twitter_search_terms"],
                         "#AliensFireteamElite2|DAR\n#AliensFireteamElite2videoGame|DAR")

    def test_the_publisher_name_is_not_used_in_the_hashtag(self):
        terms = create_game_row("Echo Weaver - DAR",
                                {"developer": "Moonlight Kids", "network": "Akupara Games",
                                 "platforms": ["PC"]})["twitter_search_terms"]
        self.assertNotIn("akupara", terms.lower())
        self.assertEqual(terms, "#EchoWeaver|DAR\n#EchoWeavervideoGame|DAR")

    def test_non_dar_rows_use_the_core_title_label(self):
        self.assertEqual(_game_search_terms("Akatori", False),
                         "#Akatori|Operations - Core Title\n"
                         "#AkatorivideoGame|Operations - Core Title")

    def test_an_explicit_value_still_wins(self):
        row = create_game_row("Akatori - DAR", dict(AKATORI, twitter_search_terms="#Custom|DAR"))
        self.assertEqual(row["twitter_search_terms"], "#Custom|DAR")


class KeywordTests(unittest.TestCase):
    def test_known_developer_clause_matches_july_31(self):
        row = create_game_row("NBA 2K27 - DAR",
                              {"developer": "Visual Concepts", "network": "2K Games",
                               "platforms": ["PS5", "Xbox Series X", "PC", "Switch 2"]})
        self.assertEqual(
            row["twitter_search_term_keywords"],
            '("NBA 2K27") (#VisualConcepts OR "Visual Concepts" OR "Video Game" OR '
            'Playstation OR iOS OR PS4 OR PS5 OR Xbox OR Switch OR Swtich 2 OR PC)'
            "|DAR|DAR|2021-01-01",
        )

    def test_reddit_clause_matches_july_31(self):
        row = create_game_row("NBA 2K27 - DAR",
                              {"developer": "Visual Concepts", "network": "2K Games",
                               "platforms": ["PC"]})
        self.assertEqual(
            row["reddit_search_terms"],
            '("NBA 2K27") (#VisualConcepts | "Visual Concepts" | "Video Game" | '
            "Playstation | iOS | PS4 | PS5 | Xbox | Switch | Switch 2 | PC)|2021-01-01",
        )


class SocialMetadataTests(unittest.TestCase):
    def test_facebook_twitter_instagram_are_carried_through(self):
        row = create_game_row("Akatori - DAR", dict(AKATORI))
        self.assertEqual(row["facebook_page"], "https://www.facebook.com/Akatorigame")
        self.assertEqual(row["twitter_handle"], "https://x.com/AKATORIGAME")
        self.assertEqual(row["instagram_user"], "akatorigame")

    def test_tiktok_is_carried_through(self):
        row = create_game_row("Akatori - DAR", dict(AKATORI, tiktok_user="akatorigame"))
        self.assertEqual(row["tiktok_user"], "akatorigame")

    def test_instagram_is_lowercased(self):
        row = create_game_row("Akatori - DAR", dict(AKATORI, instagram_user="AkatoriGame"))
        self.assertEqual(row["instagram_user"], "akatorigame")

    def test_the_games_own_youtube_channel_is_used(self):
        row = create_game_row(
            "Skatesterre - DAR",
            {"developer": "Goon Squad", "network": "Headup Games", "platforms": ["PC"],
             "youtube_own_channel": "https://www.youtube.com/channel/UCgQ4dlqbcfZ_mYuX1qXk0CA"},
        )
        self.assertEqual(row["youtube_channel_username"],
                         "https://www.youtube.com/channel/UCgQ4dlqbcfZ_mYuX1qXk0CA|Skatesterre")

    def test_a_colon_title_gets_both_spellings_matching_july_31(self):
        row = create_game_row(
            "ReStory: Chill Electronics Repairs - DAR",
            {"developer": "Mandragora", "network": "tinyBuild", "platforms": ["PC"],
             "youtube_own_channel": "https://www.youtube.com/channel/UCHLuN_JL66bD8fdJWmQ-gNw"},
        )
        self.assertEqual(
            row["youtube_channel_username"],
            "https://www.youtube.com/channel/UCHLuN_JL66bD8fdJWmQ-gNw|"
            "ReStory: Chill Electronics Repairs\n"
            "https://www.youtube.com/channel/UCHLuN_JL66bD8fdJWmQ-gNw|"
            "ReStory Chill Electronics Repairs",
        )

    def test_two_channels_share_the_cell_one_per_line(self):
        cell = _game_youtube_lines(
            ["https://www.youtube.com/channel/UC1", "https://www.youtube.com/@franchise"],
            "Echo Weaver",
        )
        self.assertEqual(cell.split("\n"),
                         ["https://www.youtube.com/channel/UC1|Echo Weaver",
                          "https://www.youtube.com/@franchise|Echo Weaver"])

    def test_no_channel_leaves_the_cell_blank(self):
        row = create_game_row("Akatori - DAR", dict(AKATORI))
        self.assertEqual(row["youtube_channel_username"], "")

    def test_a_trailing_pipe_channel_is_not_double_piped(self):
        self.assertEqual(_game_youtube_lines(["https://www.youtube.com/@pub|"], "Echo Weaver"),
                         "https://www.youtube.com/@pub|Echo Weaver")


class RowShapeTests(unittest.TestCase):
    def test_category_is_video_game(self):
        self.assertEqual(create_game_row("Akatori - DAR", dict(AKATORI))["title_category"],
                         "Video Game")

    def test_dar_defaults_match_july_31(self):
        row = create_game_row("Akatori - DAR", dict(AKATORI))
        self.assertEqual(row["companies"], "Pristine Brand")
        self.assertEqual(row["active"], "t")
        self.assertEqual(row["brand_listing_hidden"], "f")

    def test_genre_and_primary_genre_are_kept_separate(self):
        row = create_game_row("Akatori - DAR", dict(AKATORI))
        self.assertEqual(row["genre"], "Metroidvania")
        self.assertEqual(row["primary_genre"], "Platformer")

    def test_release_date_is_carried_through(self):
        self.assertEqual(create_game_row("Akatori - DAR", dict(AKATORI))["released_on"],
                         "2026-08-05")


if __name__ == "__main__":
    unittest.main()
