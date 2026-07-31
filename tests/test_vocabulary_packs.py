"""Unit tests for the vocabulary packs (core + MA + keyword flags).

Pure-function tests — no I/O, no DB. Cover:
  - Core taxonomy has the 5 universal categories from spec A3a
  - MA pack merges state curricula + named orgs
  - Unknown state falls back to MA
  - Keyword flag matching: multi-word, digits, punctuation boundaries,
    case-insensitive, dedup, empty input

Run:
    poetry run pytest tests/test_vocabulary_packs.py -v
"""

from __future__ import annotations

from app.services.heatmap_ingest.vocabulary_packs import (
    get_keyword_flags_for_state,
    get_pack,
    match_keyword_flags,
)


# ---------------------------------------------------------------------------
# Loader / core taxonomy
# ---------------------------------------------------------------------------


def test_pack_has_five_core_categories():
    pack = get_pack("MA")
    categories = {c.category for c in pack.topic_taxonomy}
    assert categories == {
        "sexed",
        "lgbtq",
        "censorship",
        "governance",
        "advocacy",
    }


def test_pack_state_uppercased():
    assert get_pack("ma").state == "MA"


def test_unknown_state_falls_back_to_ma():
    pack = get_pack("XX")
    assert pack.state == "MA"
    assert pack.state_curricula  # MA-specific curricula present


def test_none_state_falls_back_to_ma():
    assert get_pack(None).state == "MA"


def test_ma_pack_has_3rs_get_real_chpe():
    subtopics = {s.subtopic for s in get_pack("MA").state_curricula}
    assert {
        "curriculum_3rs",
        "curriculum_get_real",
        "curriculum_chpe_framework",
    } <= subtopics


def test_ma_keyword_flags_count():
    # Spec A4 lists 11 keyword flags.
    assert len(get_keyword_flags_for_state("MA")) == 11


# ---------------------------------------------------------------------------
# Keyword flag matching
# ---------------------------------------------------------------------------


def test_match_multi_word_phrase():
    text = "We should teach the 3 Rs curriculum."
    assert "3 Rs" in match_keyword_flags(text, get_keyword_flags_for_state("MA"))


def test_match_case_insensitive():
    text = "CHPE FRAMEWORK is mentioned here"
    assert "CHPE Framework" in match_keyword_flags(
        text, get_keyword_flags_for_state("MA")
    )


def test_match_at_sentence_boundary():
    text = "Parents may opt-in. The policy is clear."
    assert "opt-in" in match_keyword_flags(
        text, get_keyword_flags_for_state("MA")
    )


def test_no_false_positive_inside_word():
    # "abstinence-only" should not match inside "abstinence-onlyish"
    text = "this is abstinence-onlyish"
    assert "abstinence-only" not in match_keyword_flags(
        text, get_keyword_flags_for_state("MA")
    )


def test_match_multiple_flags_dedup_and_order():
    text = (
        "pornography indoctrination abstinence-only "
        "3 Rs Get Real CHPE Framework"
    )
    flags = match_keyword_flags(text, get_keyword_flags_for_state("MA"))
    # 6 of the 11 MA flags fired here, in keyword_flags tuple order.
    assert flags == [
        "pornography",
        "indoctrination",
        "abstinence-only",
        "3 Rs",
        "Get Real",
        "CHPE Framework",
    ]


def test_match_empty_text():
    assert match_keyword_flags("", get_keyword_flags_for_state("MA")) == []


def test_match_none_text():
    assert match_keyword_flags(None, get_keyword_flags_for_state("MA")) == []


def test_match_empty_flags():
    assert match_keyword_flags("some text", ()) == []


def test_match_no_flags_present():
    text = "a totally unrelated budget discussion about buses"
    assert match_keyword_flags(text, get_keyword_flags_for_state("MA")) == []
