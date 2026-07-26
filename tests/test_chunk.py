import re

import pytest

from textinator.chunk import chunk_text


def _words(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def test_short_text_is_one_chunk():
    assert chunk_text("Hello world.", 100) == ["Hello world."]


def test_respects_max_chars():
    text = " ".join(f"Sentence number {i}." for i in range(100))
    chunks = chunk_text(text, 150)
    assert all(len(c) <= 150 for c in chunks)
    assert len(chunks) > 1


def test_no_words_lost():
    text = "\n\n".join(
        " ".join(f"Paragraph {p} sentence {s}." for s in range(20))
        for p in range(5)
    )
    chunks = chunk_text(text, 120)
    assert _words("\n\n".join(chunks)) == _words(text)


def test_packs_small_paragraphs_together():
    text = "Para one.\n\nPara two.\n\nPara three."
    assert chunk_text(text, 1000) == ["Para one.\n\nPara two.\n\nPara three."]


def test_splits_on_sentence_boundaries():
    text = "First sentence is here. Second sentence is here. Third one."
    chunks = chunk_text(text, 30)
    assert chunks[0] == "First sentence is here."
    assert all(len(c) <= 30 for c in chunks)


def test_hard_splits_monster_sentence():
    text = "word " * 100  # one 500-char "sentence", no terminal punctuation
    chunks = chunk_text(text.strip(), 50)
    assert all(len(c) <= 50 for c in chunks)
    assert _words(" ".join(chunks)) == _words(text)


def test_rejects_bad_max():
    with pytest.raises(ValueError):
        chunk_text("hi", 0)
