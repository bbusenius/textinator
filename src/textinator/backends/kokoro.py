"""Kokoro backend: Kokoro-82M running locally on CPU. The true-offline option.

Free, private, no network needed after the one-time model download (~330MB,
fetched automatically to the Hugging Face cache on first use). Emits 24kHz WAV
chunks; the stitcher encodes the final episode to mp3.

Voice names are like ``af_heart``: first letter = language ('a' American,
'b' British English), second = gender (f/m).
"""

from __future__ import annotations

from pathlib import Path

from . import Backend

SAMPLE_RATE = 24_000
KNOWN_VOICES = (
    "af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky",
    "am_adam", "am_michael", "am_fenrir", "am_puck",
    "bf_emma", "bf_isabella", "bm_george", "bm_lewis",
)


class KokoroBackend(Backend):
    name = "kokoro"
    suffix = "wav"
    # local + free, so chunk size only affects memory/latency, not cost
    max_chars = 3000
    paid = False

    def __init__(self):
        self._pipelines: dict[str, object] = {}

    @property
    def default_voice(self) -> str:
        return "af_heart"

    def _get_pipeline(self, lang_code: str):
        """One KPipeline per language, created lazily (model loads here)."""
        if lang_code not in self._pipelines:
            from kokoro import KPipeline

            self._pipelines[lang_code] = KPipeline(
                lang_code=lang_code, repo_id="hexgrad/Kokoro-82M"
            )
        return self._pipelines[lang_code]

    def synthesize(self, text: str, voice: str, out_path: Path) -> None:
        import numpy as np
        import soundfile as sf

        # voice prefix encodes the language: af_heart -> 'a' (American English)
        pipeline = self._get_pipeline(voice[0])
        segments = []
        for _graphemes, _phonemes, audio in pipeline(text, voice=voice):
            segments.append(np.asarray(audio))
        if not segments:
            raise RuntimeError(f"kokoro produced no audio for voice {voice!r}")
        sf.write(out_path, np.concatenate(segments), SAMPLE_RATE)
