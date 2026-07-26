"""Concatenate chunk audio into one episode file, and probe the result.

Uses ffmpeg's concat demuxer with stream copy — all chunks for an episode come
from the same backend/voice/params, so codecs match and no re-encode is needed.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


class StitchError(RuntimeError):
    pass


def _require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise StitchError(f"{binary} not found on PATH — install ffmpeg")
    return path


# encoder settings per target container, used when chunks need re-encoding
_ENCODE_ARGS = {
    ".mp3": ["-c:a", "libmp3lame", "-b:a", "64k"],
    ".m4a": ["-c:a", "aac", "-b:a", "64k"],
    ".m4b": ["-c:a", "aac", "-b:a", "64k"],
    ".wav": ["-c:a", "pcm_s16le"],
}


def stitch(
    chunk_paths: list[Path],
    out_path: Path,
    metadata_path: Path | None = None,
) -> None:
    """Concatenate ``chunk_paths`` into ``out_path``.

    Chunks sharing the output's container are stream-copied (all chunks for an
    episode come from one backend/voice, so codecs match). If the output format
    differs (e.g. wav chunks -> mp3 episode), re-encode during the concat.
    ``metadata_path`` is an ffmetadata file (chapter marks) to embed.
    """
    if not chunk_paths:
        raise StitchError("no audio chunks to stitch")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    suffix = out_path.suffix.lower()
    same_format = all(p.suffix.lower() == suffix for p in chunk_paths)
    if same_format and suffix != ".m4b":  # .m4b copy would still need remux
        codec_args = ["-c", "copy"]
    else:
        try:
            codec_args = _ENCODE_ARGS[suffix]
        except KeyError:
            raise StitchError(f"unsupported output format: {out_path.suffix}")

    # ffmpeg doesn't know the .m4b extension; it's an mp4 (ipod) container.
    if suffix in (".m4a", ".m4b"):
        codec_args = [*codec_args, "-f", "ipod"]

    if len(chunk_paths) == 1 and same_format and metadata_path is None:
        shutil.copyfile(chunk_paths[0], out_path)
        return

    ffmpeg = _require("ffmpeg")
    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, dir=out_path.parent
    ) as listfile:
        for path in chunk_paths:
            escaped = str(path.resolve()).replace("'", "'\\''")
            listfile.write(f"file '{escaped}'\n")
        list_path = Path(listfile.name)

    metadata_args: list[str] = []
    if metadata_path is not None:
        metadata_args = ["-i", str(metadata_path), "-map_metadata", "1"]

    try:
        result = subprocess.run(
            [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0",
                "-i", str(list_path),
                *metadata_args,
                "-map", "0:a",
                *codec_args,
                str(out_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise StitchError(f"ffmpeg concat failed:\n{result.stderr}")
    finally:
        list_path.unlink(missing_ok=True)


def probe_duration(path: Path) -> float:
    """Return audio duration in seconds via ffprobe."""
    ffprobe = _require("ffprobe")
    result = subprocess.run(
        [
            ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise StitchError(f"ffprobe failed on {path}:\n{result.stderr}")
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise StitchError(f"ffprobe returned no duration for {path}") from exc
