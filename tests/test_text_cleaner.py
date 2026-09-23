"""
tests/test_text_cleaner.py
===========================
Unit tests for preprocessing.text_cleaner.clean_text.

Run with:
    python -m pytest tests/test_text_cleaner.py -v
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path so the import works whether tests are run
# from the project root or from the tests/ directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from preprocessing.text_cleaner import clean_text

# ─────────────────────────────────────────────────────────────────────────────
# 1. Null / non-string safety
# ─────────────────────────────────────────────────────────────────────────────

class TestNullSafety:
    def test_none_returns_empty_string(self):
        assert clean_text(None) == ""

    def test_empty_string_returns_empty_string(self):
        assert clean_text("") == ""

    def test_whitespace_only_returns_empty_string(self):
        assert clean_text("   \t\n  ") == ""

    def test_integer_input_handled(self):
        # Non-string inputs should not raise
        result = clean_text(42)
        assert isinstance(result, str)

    def test_list_input_handled(self):
        result = clean_text(["a", "b"])
        assert isinstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Normal tweet — must not destroy meaningful content
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalTweet:
    def test_basic_sentence_lowercased(self):
        result = clean_text("Rescue teams arrived in Karachi after the flood.")
        assert result == "rescue teams arrived in karachi after the flood."

    def test_content_is_preserved(self):
        result = clean_text("Flooding in District 4 — 300 families displaced.")
        # Numbers and core words should survive
        assert "300" in result
        assert "families" in result
        assert "displaced" in result


# ─────────────────────────────────────────────────────────────────────────────
# 3. URL removal
# ─────────────────────────────────────────────────────────────────────────────

class TestURLRemoval:
    def test_http_url_removed(self):
        result = clean_text("More info at http://example.com/relief stay safe")
        assert "http" not in result
        assert "example.com" not in result

    def test_https_url_removed(self):
        result = clean_text("See https://reliefweb.int/report/12345 for details")
        assert "https" not in result
        assert "reliefweb" not in result

    def test_www_url_removed(self):
        result = clean_text("Visit www.redcross.org to donate")
        assert "www" not in result

    def test_tco_shortlink_removed(self):
        result = clean_text("Update: https://t.co/abc123XYZ situation critical")
        assert "t.co" not in result

    def test_surrounding_text_preserved(self):
        result = clean_text("Flood update https://t.co/abc in Sindh")
        assert "flood update" in result
        assert "in sindh" in result


# ─────────────────────────────────────────────────────────────────────────────
# 4. @mention removal
# ─────────────────────────────────────────────────────────────────────────────

class TestMentionRemoval:
    def test_single_mention_removed(self):
        result = clean_text("@RedCross Please send aid to sector 4")
        assert "@redcross" not in result
        assert "redcross" not in result   # the handle itself gone

    def test_multiple_mentions_removed(self):
        result = clean_text("@WHO @UNICEF confirmed outbreak in 3 districts")
        assert "@who" not in result
        assert "@unicef" not in result

    def test_surrounding_text_preserved(self):
        result = clean_text("@AidOrg volunteers needed in flood zones")
        assert "volunteers" in result
        assert "flood zones" in result


# ─────────────────────────────────────────────────────────────────────────────
# 5. Hashtag word preservation
# ─────────────────────────────────────────────────────────────────────────────

class TestHashtag:
    def test_hash_symbol_removed_word_kept(self):
        result = clean_text("#FloodAlert in Karachi")
        assert "#" not in result
        assert "floodalert" in result

    def test_multiple_hashtags_words_kept(self):
        result = clean_text("#Relief #Earthquake aid needed")
        assert "relief" in result
        assert "earthquake" in result

    def test_hashtag_word_lowercased(self):
        result = clean_text("#CYCLONEIDAI hits Mozambique")
        assert "cycloneidai" in result

    def test_only_hash_removed_not_surrounding(self):
        result = clean_text("Emergency #Flood response activated")
        assert "emergency" in result
        assert "flood" in result
        assert "response activated" in result


# ─────────────────────────────────────────────────────────────────────────────
# 6. Whitespace normalisation
# ─────────────────────────────────────────────────────────────────────────────

class TestWhitespace:
    def test_multiple_spaces_collapsed(self):
        result = clean_text("5000   families   need   shelter")
        assert "  " not in result   # no double-spaces remain
        assert result == "5000 families need shelter"

    def test_tabs_collapsed(self):
        result = clean_text("aid\tarrived\tin\tKarachi")
        assert "\t" not in result
        assert "aid arrived in karachi" == result

    def test_newlines_collapsed(self):
        result = clean_text("flood\nwarning\nissued")
        assert "\n" not in result

    def test_leading_trailing_stripped(self):
        result = clean_text("   rescue operation underway   ")
        assert result == "rescue operation underway"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Repeated punctuation normalisation
# ─────────────────────────────────────────────────────────────────────────────

class TestRepeatedPunctuation:
    def test_many_exclamations_collapsed_to_two(self):
        result = clean_text("SOS!!!!!! Need water NOW")
        assert "!!!" not in result
        assert "!!" in result

    def test_many_question_marks_collapsed(self):
        result = clean_text("Where is the shelter???????????")
        assert "???" not in result
        assert "??" in result

    def test_double_exclamation_preserved(self):
        # Two is already acceptable — should not be touched
        result = clean_text("Help needed!!")
        assert "!!" in result

    def test_single_punctuation_preserved(self):
        result = clean_text("Rescue confirmed.")
        assert result.endswith(".")


# ─────────────────────────────────────────────────────────────────────────────
# 8. Numbers preserved
# ─────────────────────────────────────────────────────────────────────────────

class TestNumbersPreserved:
    def test_casualty_numbers_preserved(self):
        result = clean_text("At least 235 dead, 800 injured after quake")
        assert "235" in result
        assert "800" in result

    def test_resource_numbers_preserved(self):
        result = clean_text("3000 food packets distributed to 12 camps")
        assert "3000" in result
        assert "12" in result

    def test_coordinates_preserved(self):
        result = clean_text("Location 24.8 N 67.0 E — flood depth 2 meters")
        assert "24.8" in result
        assert "2" in result


# ─────────────────────────────────────────────────────────────────────────────
# 9. Unicode and emoji
# ─────────────────────────────────────────────────────────────────────────────

class TestUnicodeAndEmoji:
    def test_emoji_removed_by_default(self):
        result = clean_text("Prayers for Nepal 🙏🌊 #earthquake")
        assert "🙏" not in result
        assert "🌊" not in result

    def test_text_around_emoji_preserved(self):
        result = clean_text("Prayers for Nepal 🙏 earthquake survivors")
        assert "prayers for nepal" in result
        assert "earthquake survivors" in result

    def test_emoji_preserved_when_flag_false(self):
        result = clean_text("SOS 🚨 flood warning", remove_emojis=False)
        assert "🚨" in result

    def test_unicode_normalisation_applied(self):
        # Full-width Latin characters should normalise to ASCII equivalents
        result = clean_text("Ａｉｄ ａｒｒｉｖｅｄ")   # full-width A, i, d …
        assert result == "aid arrived"

    def test_accented_characters_preserved(self):
        # Location names like "Iquique" (Chile), "Pokhara" (Nepal) use accents
        result = clean_text("Aid convoy reached Iquique, región de Tarapacá")
        assert "iquique" in result
        assert "región" in result or "region" in result   # NFKC keeps é

    def test_html_entity_decoded(self):
        result = clean_text("Aid workers &amp; volunteers needed")
        assert "&amp;" not in result
        assert "&" in result

    def test_html_lt_gt_decoded(self):
        result = clean_text("Status &lt;critical&gt;")
        assert "&lt;" not in result
        assert "&gt;" not in result


# ─────────────────────────────────────────────────────────────────────────────
# 10. RT prefix removal
# ─────────────────────────────────────────────────────────────────────────────

class TestRTPrefix:
    def test_rt_prefix_stripped(self):
        result = clean_text("RT @WHO: Outbreak confirmed in 3 districts.")
        assert not result.startswith("rt")

    def test_content_after_rt_preserved(self):
        result = clean_text("RT @WHO: Outbreak confirmed in 3 districts.")
        assert "outbreak confirmed in 3 districts." == result

    def test_non_rt_not_affected(self):
        result = clean_text("Rescue teams active")
        assert result == "rescue teams active"


# ─────────────────────────────────────────────────────────────────────────────
# 11. Stopwords are NOT removed
# ─────────────────────────────────────────────────────────────────────────────

class TestStopwordsPreserved:
    def test_negation_preserved(self):
        result = clean_text("No aid has arrived. Not enough food.")
        assert "no" in result
        assert "not" in result

    def test_common_stopwords_preserved(self):
        result = clean_text("The rescue team is at the shelter with the survivors")
        assert "the" in result
        assert "is" in result
        assert "with" in result


# ─────────────────────────────────────────────────────────────────────────────
# 12. Combined / real-world tweet
# ─────────────────────────────────────────────────────────────────────────────

class TestRealWorldTweets:
    def test_full_noisy_tweet(self):
        tweet = "RT @AidOrg: #Relief effort https://t.co/xyz @volunteer needed!!!!!! 500 people displaced"
        result = clean_text(tweet)
        assert "relief" in result            # hashtag word kept
        assert "t.co" not in result          # URL removed
        assert "@" not in result             # mentions removed
        assert "500" in result               # number preserved
        assert "displaced" in result         # content preserved
        assert "!!!" not in result           # excessive punctuation collapsed

    def test_humanitarian_tweet(self):
        tweet = "#CycloneIdai: at least 750 dead &amp; 110,000 displaced. https://t.co/news"
        result = clean_text(tweet)
        assert "750" in result
        assert "110,000" in result
        assert "displaced" in result
        assert "&amp;" not in result
        assert "https" not in result
