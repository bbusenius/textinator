"""The shared spine: normalize → chunk → TTS (cached) → stitch → feed.

This is the whole product; inputs, backends, and outputs are just adapters
around this module.
"""

from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .backends import Backend
from .cache import AudioCache
from .chunk import chunk_text
from .feed import Episode, Feed
from .inputs import Document
from .normalize import normalize
from .stitch import probe_duration, stitch


class BudgetExceeded(RuntimeError):
    """A paid backend would be sent more characters than the allowed budget."""


# Concurrent runs (web UI worker threads) must not interleave the feed's
# load -> add episode -> save sequence, or episodes get lost. Synthesis
# itself stays parallel; only the feed update is serialized.
_feed_lock = threading.Lock()


@dataclass
class PipelineResult:
    episode_path: Path
    feed_xml_path: Path
    episode: Episode
    chunk_count: int
    chunks_synthesized: int  # cache misses actually sent to the backend
    char_count: int
    duration_seconds: float


@dataclass
class DryRunReport:
    chunk_count: int
    char_count: int
    cached_chunks: int
    chars_to_synthesize: int


def _slugify(title: str, max_len: int = 40) -> str:
    ascii_title = (
        unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "episode"


def _plan(
    document: Document, backend: Backend, voice: str, cache: AudioCache
) -> list[tuple[str | None, list[tuple[str, str]]]]:
    """Shared front half: normalize, chunk, and compute cache keys.

    Returns sections of ``(chapter_title | None, [(chunk, cache_key), ...])``.
    Chunks never straddle chapter boundaries, so chapter marks always land on
    chunk edges. Flat documents are one untitled section.
    """
    if document.chapters:
        sources = [(ch.title, ch.text) for ch in document.chapters]
    else:
        sources = [(None, document.text)]

    fingerprint = backend.params_fingerprint()
    sections = []
    for title, raw in sources:
        text = normalize(raw)
        if not text:
            continue
        keyed = [
            (chunk, cache.key(backend.name, voice, fingerprint, chunk))
            for chunk in chunk_text(text, backend.max_chars)
        ]
        sections.append((title, keyed))
    if not sections:
        raise ValueError("nothing speakable left after normalization")
    return sections


def _flat_chunks(sections) -> list[tuple[str, str]]:
    return [pair for _title, keyed in sections for pair in keyed]


def dry_run(
    document: Document,
    backend: Backend,
    voice: str | None = None,
    cache_dir: Path | None = None,
) -> DryRunReport:
    """Report what a run would send to the backend, without synthesis."""
    voice = voice or backend.default_voice
    cache = AudioCache(cache_dir)
    keyed = _flat_chunks(_plan(document, backend, voice, cache))
    misses = [
        chunk for chunk, key in keyed if cache.get(key, backend.suffix) is None
    ]
    chars_to_send = sum(len(c) for c in misses)
    return DryRunReport(
        chunk_count=len(keyed),
        char_count=sum(len(c) for c, _ in keyed),
        cached_chunks=len(keyed) - len(misses),
        chars_to_synthesize=chars_to_send,
    )


def run(
    document: Document,
    backend: Backend,
    feed_dir: Path,
    voice: str | None = None,
    base_url: str | None = None,
    cache_dir: Path | None = None,
    max_chars_budget: int | None = None,
    force: bool = False,
    episode_format: str = "mp3",
    on_progress=None,
) -> PipelineResult:
    """Turn a Document into an episode file + updated feed.xml in feed_dir."""
    voice = voice or backend.default_voice
    cache = AudioCache(cache_dir)
    sections = _plan(document, backend, voice, cache)
    keyed = _flat_chunks(sections)

    # Cost guard: paid backends refuse oversized sends unless forced.
    misses = [
        (chunk, key)
        for chunk, key in keyed
        if cache.get(key, backend.suffix) is None
    ]
    chars_to_send = sum(len(chunk) for chunk, _ in misses)
    if backend.paid and max_chars_budget is not None and not force:
        if chars_to_send > max_chars_budget:
            raise BudgetExceeded(
                f"{backend.name} would be sent {chars_to_send:,} chars "
                f"over the {max_chars_budget:,}-char budget. "
                f"Re-run with --force to proceed, or --dry-run to inspect."
            )

    # Synthesize cache misses, keeping per-section chunk paths for chapters.
    synthesized = 0
    section_paths: list[tuple[str | None, list[Path]]] = []
    i = 0
    for title, section_keyed in sections:
        paths: list[Path] = []
        for chunk, key in section_keyed:
            i += 1
            cached = cache.get(key, backend.suffix)
            if cached is None:
                if on_progress:
                    on_progress(
                        f"synthesizing chunk {i}/{len(keyed)} ({len(chunk)} chars)"
                    )
                target = cache.path_for(key, backend.suffix)
                # keep the audio extension last so encoders can infer the format
                tmp = target.parent / f".tmp-{target.name}"
                backend.synthesize(chunk, voice, tmp)
                if not tmp.is_file() or tmp.stat().st_size == 0:
                    tmp.unlink(missing_ok=True)
                    raise RuntimeError(
                        f"backend {backend.name} produced no audio for chunk {i}"
                    )
                tmp.replace(target)
                cached = target
                synthesized += 1
            elif on_progress:
                on_progress(f"chunk {i}/{len(keyed)} cached")
            paths.append(cached)
        section_paths.append((title, paths))
    chunk_paths = [p for _t, paths in section_paths for p in paths]

    # Chapter markers: only meaningful with real chapters and an mp4 container.
    want_chapters = (
        document.chapters and episode_format in ("m4a", "m4b")
    )

    # Stitch into the episode file, straight into the feed directory.
    # The whole feed update (load -> pick filename -> add -> save) holds the
    # lock so concurrent runs can't lose each other's episodes.
    with _feed_lock:
        feed = Feed.load(feed_dir)
        if base_url:
            feed.base_url = base_url
        date_prefix = datetime.now(timezone.utc).strftime("%Y%m%d")
        stem = f"{date_prefix}-{_slugify(document.title)}"
        episode_path = feed.episodes_dir / f"{stem}.{episode_format}"
        n = 2
        while episode_path.exists():
            episode_path = feed.episodes_dir / f"{stem}-{n}.{episode_format}"
            n += 1
        if on_progress:
            on_progress(f"stitching {len(chunk_paths)} chunk(s)")
        metadata_path = None
        if want_chapters:
            if on_progress:
                on_progress("computing chapter marks")
            metadata_path = episode_path.with_suffix(".ffmeta")
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            metadata_path.write_text(
                _chapter_metadata(section_paths), encoding="utf-8"
            )
        try:
            stitch(chunk_paths, episode_path, metadata_path=metadata_path)
        finally:
            if metadata_path:
                metadata_path.unlink(missing_ok=True)
        duration = probe_duration(episode_path)
        _tag_episode(episode_path, document.title, feed.title, voice)

        description = f"Generated by textinator ({backend.name}, {voice})."
        if document.chapters:
            toc = " · ".join(t for t, _p in section_paths if t)
            if toc:
                description += f" Chapters: {toc}"
        episode = feed.add_episode(
            episode_path,
            title=document.title,
            duration_seconds=duration,
            description=description,
        )
        feed_xml = feed.save()

    return PipelineResult(
        episode_path=episode_path,
        feed_xml_path=feed_xml,
        episode=episode,
        chunk_count=len(keyed),
        chunks_synthesized=synthesized,
        char_count=sum(len(c) for c, _ in keyed),
        duration_seconds=duration,
    )


def _chapter_metadata(
    section_paths: list[tuple[str | None, list[Path]]]
) -> str:
    """Build an ffmetadata document with one [CHAPTER] per section.

    Chapter edges are the cumulative durations of each section's chunks,
    which is exact because chunks never straddle chapter boundaries.
    """

    def _escape(value: str) -> str:
        for char in "\\=;#\n":
            value = value.replace(char, f"\\{char}")
        return value

    lines = [";FFMETADATA1"]
    cursor_ms = 0
    for index, (title, paths) in enumerate(section_paths, 1):
        section_ms = round(sum(probe_duration(p) for p in paths) * 1000)
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={cursor_ms}",
            f"END={cursor_ms + section_ms}",
            f"title={_escape(title or f'Part {index}')}",
        ]
        cursor_ms += section_ms
    return "\n".join(lines) + "\n"


def _tag_episode(path: Path, title: str, album: str, voice: str) -> None:
    """Best-effort ID3/MP4 tags so files look right in players."""
    try:
        if path.suffix.lower() == ".mp3":
            from mutagen.id3 import ID3, TALB, TIT2, TPE1
            from mutagen.mp3 import MP3

            audio = MP3(path)
            if audio.tags is None:
                audio.add_tags()
            tags: ID3 = audio.tags
            tags.add(TIT2(encoding=3, text=title))
            tags.add(TALB(encoding=3, text=album))
            tags.add(TPE1(encoding=3, text=voice))
            audio.save()
        elif path.suffix.lower() in (".m4a", ".m4b"):
            from mutagen.mp4 import MP4

            audio = MP4(path)
            audio["\xa9nam"] = [title]
            audio["\xa9alb"] = [album]
            audio["\xa9ART"] = [voice]
            audio.save()
    except Exception:
        pass  # tags are cosmetic; never fail the run over them
