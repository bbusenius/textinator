from xml.etree import ElementTree as ET

import pytest

from textinator.inputs import Document
from textinator.pipeline import BudgetExceeded, dry_run, run

LONG_TEXT = "\n\n".join(
    " ".join(f"Paragraph {p} sentence {s} words words words." for s in range(6))
    for p in range(4)
)


def test_end_to_end_with_dummy_backend(tmp_path, dummy_backend):
    doc = Document(title="My Test Note", text=LONG_TEXT)
    result = run(
        doc,
        dummy_backend,
        feed_dir=tmp_path / "feed",
        cache_dir=tmp_path / "cache",
    )
    assert result.episode_path.is_file()
    assert result.episode_path.stat().st_size > 0
    assert result.chunk_count > 1  # max_chars=200 forces multiple chunks
    assert result.chunks_synthesized == result.chunk_count
    assert result.duration_seconds > 0
    assert "my-test-note" in result.episode_path.name

    # feed.xml exists, is well-formed, and points at the episode
    root = ET.parse(result.feed_xml_path).getroot()
    item = root.find("channel/item")
    assert item.find("title").text == "My Test Note"
    enclosure = item.find("enclosure")
    assert enclosure.get("length") == str(result.episode_path.stat().st_size)


def test_cache_prevents_resynthesis(tmp_path, dummy_backend):
    doc = Document(title="Cached", text=LONG_TEXT)
    kwargs = dict(feed_dir=tmp_path / "feed", cache_dir=tmp_path / "cache")
    first = run(doc, dummy_backend, **kwargs)
    calls_after_first = len(dummy_backend.calls)
    second = run(doc, dummy_backend, **kwargs)
    assert first.chunks_synthesized == calls_after_first
    assert second.chunks_synthesized == 0  # every chunk came from cache
    assert len(dummy_backend.calls) == calls_after_first
    # distinct episode files, no clobbering
    assert second.episode_path != first.episode_path


def test_paid_budget_guard(tmp_path, paid_backend):
    doc = Document(title="Pricey", text=LONG_TEXT)
    with pytest.raises(BudgetExceeded):
        run(
            doc,
            paid_backend,
            feed_dir=tmp_path / "feed",
            cache_dir=tmp_path / "cache",
            max_chars_budget=10,
        )
    assert paid_backend.calls == []  # nothing was sent


def test_paid_budget_force_overrides(tmp_path, paid_backend):
    doc = Document(title="Forced", text="Short text.")
    result = run(
        doc,
        paid_backend,
        feed_dir=tmp_path / "feed",
        cache_dir=tmp_path / "cache",
        max_chars_budget=1,
        force=True,
    )
    assert result.episode_path.is_file()


def test_dry_run_spends_nothing(tmp_path, paid_backend):
    doc = Document(title="Estimate", text=LONG_TEXT)
    report = dry_run(doc, paid_backend, cache_dir=tmp_path / "cache")
    assert report.chunk_count > 0
    assert report.chars_to_synthesize == report.char_count
    assert not hasattr(report, "estimated_cost_usd")
    assert paid_backend.calls == []  # dry run never synthesizes


def test_dry_run_reflects_cache(tmp_path, dummy_backend):
    doc = Document(title="Warm", text="Hello there world.")
    kwargs = dict(feed_dir=tmp_path / "feed", cache_dir=tmp_path / "cache")
    run(doc, dummy_backend, **kwargs)
    report = dry_run(doc, dummy_backend, cache_dir=tmp_path / "cache")
    assert report.cached_chunks == report.chunk_count
    assert report.chars_to_synthesize == 0


def test_empty_document_rejected(tmp_path, dummy_backend):
    doc = Document(title="Empty", text="```\ncode only\n```")
    with pytest.raises(ValueError):
        run(doc, dummy_backend, feed_dir=tmp_path / "feed", cache_dir=tmp_path / "c")
