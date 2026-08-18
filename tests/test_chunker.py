"""Tests for the generic Chunker, focused on the sentence strategy the
pipeline now uses for PDF/DOCX-fallback/PPTX/Markdown-fallback chunking,
and the token-mode strategy used for school_scraper documents.
"""

from __future__ import annotations

from app.services.document_processing.chunker import Chunker

# ---------------------------------------------------------------------------
# Sentence strategy — oversized-sentence guard (the bug this branch fixes)
# ---------------------------------------------------------------------------


def test_sentence_strategy_splits_oversized_sentence():
    """A single 'sentence' longer than chunk_size must not be left unbounded.

    Without the guard, `current_chunk` is empty when the size check runs, so
    the whole oversized sentence would be appended whole — a regression
    relative to `_chunk_fixed`, which always caps size.
    """
    long_sentence = "word " * 500  # no terminal punctuation, ~2500 chars
    chunker = Chunker(chunk_size=200, chunk_overlap=20)
    chunks = chunker.chunk_text(long_sentence, strategy="sentence")

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 200


def test_sentence_strategy_handles_mixed_normal_and_oversized_sentences():
    normal = "Short sentence one. Short sentence two. "
    oversized = "no punctuation here just one giant run on clause " * 20
    text = normal + oversized + " Final short sentence."

    chunker = Chunker(chunk_size=150, chunk_overlap=20)
    chunks = chunker.chunk_text(text, strategy="sentence")

    assert chunks
    for chunk in chunks:
        assert len(chunk) <= 150
    rebuilt = " ".join(chunks)
    assert "Short sentence one." in rebuilt
    assert "Final short sentence." in rebuilt


def test_sentence_strategy_basic_packing_and_overlap():
    text = " ".join(f"Sentence number {i}." for i in range(20))
    chunker = Chunker(chunk_size=100, chunk_overlap=30)
    chunks = chunker.chunk_text(text, strategy="sentence")

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 100
    # Every sentence must survive somewhere in the output.
    rebuilt = " ".join(chunks)
    for i in range(20):
        assert f"Sentence number {i}." in rebuilt


def test_sentence_strategy_on_text_with_no_punctuation_at_all():
    text = "word " * 10  # well under chunk_size, no punctuation
    chunker = Chunker(chunk_size=1000, chunk_overlap=100)
    chunks = chunker.chunk_text(text, strategy="sentence")
    assert len(chunks) == 1
    assert chunks[0].strip() == text.strip()


def test_sentence_strategy_empty_text_returns_no_chunks():
    chunker = Chunker(chunk_size=100, chunk_overlap=10)
    assert chunker.chunk_text("   ", strategy="sentence") == []


# ---------------------------------------------------------------------------
# Token-fixed mode — used for school_scraper documents
# ---------------------------------------------------------------------------


def test_token_fixed_mode_respects_token_budget():
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    text = " ".join(f"token{i}" for i in range(2000))

    chunker = Chunker(chunk_size=500, chunk_overlap=100, mode="token")
    chunks = chunker.chunk_text(text)  # default strategy="fixed"

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(enc.encode(chunk)) <= 500


def test_token_fixed_mode_only_applies_to_fixed_strategy():
    """Sentence/paragraph strategies size by len(), ignoring `mode` entirely.

    This is a documented, deliberate limitation the pipeline relies on: the
    school_scraper branch must stay on strategy="fixed" to get real
    token-bounded chunks. Proof here isn't a strict char bound (the sentence
    packer's size estimate doesn't account for join-separator overhead, an
    existing, unrelated looseness) — it's that the chunk count tracks the
    *character* length of the text, not a token count, which for
    `"A. " * 5000` would compress to far fewer BPE tokens than characters.
    """
    text = "A. " * 5000  # 15000 chars, but very few distinct BPE tokens
    chunker = Chunker(chunk_size=50, chunk_overlap=10, mode="token")
    chunks = chunker.chunk_text(text, strategy="sentence")
    # A true token-bounded chunker would produce very few chunks for this
    # repetitive text. Sizing by len() instead produces roughly
    # len(text) / chunk_size chunks — an order of magnitude more.
    assert len(chunks) > len(text) / (chunker.chunk_size * 2)
