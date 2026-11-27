"""Full-video transcription with an on-disk, configuration-aware cache.

Fase 2 of the roadmap needs the whole video transcribed (not just a manually
cut segment), and CLAUDE.md requires caching to avoid re-running Whisper on
a video that was already processed.
"""
import hashlib
import json
import os
import tempfile
from functools import lru_cache
from importlib import metadata
from pathlib import Path

import ffmpeg

from . import db
from .config import config
from .core import extract_audio, probe_media, transcribe_audio


TRANSCRIPTION_CACHE_VERSION = "2"
_HASH_CHUNK_SIZE = 1024 * 1024


def _faster_whisper_version() -> str:
    """Return the installed engine version without making import-time brittle."""
    try:
        return metadata.version("faster-whisper")
    except metadata.PackageNotFoundError:
        return "unknown"


def _requested_language(language: str | None) -> str:
    return language.strip().lower() if language and language.strip() else "auto"


@lru_cache(maxsize=128)
def _hash_video_content(video_path: str, size: int, mtime_ns: int) -> str:
    digest = hashlib.sha256()
    digest.update(f"size={size};mtime_ns={mtime_ns};".encode("utf-8"))
    with open(video_path, "rb") as video_file:
        for chunk in iter(lambda: video_file.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_video_fingerprint(video_path: str) -> str:
    """Fingerprint file content plus nanosecond-resolution metadata.

    Full content hashing avoids stale cache hits when a downloader overwrites a
    file in place. Size and ``mtime_ns`` make the identity explicit and also
    distinguish metadata-only replacements, as required by the cache contract.
    """
    absolute_path = os.path.abspath(video_path)
    stat = os.stat(absolute_path)
    return _hash_video_content(absolute_path, stat.st_size, stat.st_mtime_ns)


def compute_transcription_cache_key(video_path: str, language: str = None) -> str:
    """Build a stable cache key for both media and Whisper configuration.

    The existing database API stores a single opaque fingerprint primary key,
    so folding the full identity into that key preserves database compatibility
    while allowing separate entries for language/model/engine variants.
    """
    identity = {
        "video": compute_video_fingerprint(video_path),
        "language": _requested_language(language),
        "model": str(config.WHISPER_MODEL_SIZE),
        "engine": {
            "name": "faster-whisper",
            "device": str(config.WHISPER_DEVICE),
            "compute_type": str(config.WHISPER_COMPUTE_TYPE),
        },
        "engine_version": _faster_whisper_version(),
        "cache_version": TRANSCRIPTION_CACHE_VERSION,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _silent_video_transcription(video_path: str, language: str | None, duration: float) -> dict:
    """Return the normal transcription shape without invoking Whisper."""
    return {
        "language": language,
        "language_probability": 0.0,
        "duration": max(0.0, float(duration)),
        "segments": [],
        "text": "",
        "title": Path(video_path).stem,
        "has_audio": False,
    }


def transcribe_video_file(video_path: str, db_path: str, language: str = None) -> dict:
    """Transcribes the entire video, reusing a cached result when available.

    Returns the same dict shape as `core.transcribe_audio`: language,
    language_probability, duration, segments (each with word-level timestamps),
    and the concatenated text.
    """
    requested_language = _requested_language(language)
    whisper_language = None if requested_language == "auto" else requested_language
    cache_key = compute_transcription_cache_key(video_path, language=requested_language)
    cached = db.get_cached_transcript(db_path, cache_key)
    if cached is not None:
        return cached

    # A missing audio stream is a supported media shape: return an empty
    # transcription carrying enough metadata for the static-caption fallback.
    # If probing itself fails (e.g. a mocked/non-media fixture), preserve the
    # historical behaviour and let extraction provide the authoritative error.
    try:
        media = probe_media(video_path)
    except (ffmpeg.Error, OSError):
        media = None

    if media is not None and not media["has_video"]:
        raise ValueError(f"O arquivo nao possui faixa de video: {video_path}")

    if media is not None and not media["has_audio"]:
        transcription = _silent_video_transcription(video_path, whisper_language, media["duration"])
        db.save_transcript(db_path, cache_key, transcription.get("language"), transcription)
        return transcription

    with tempfile.TemporaryDirectory(prefix="cortai_transcribe_") as temp_dir:
        audio_path = os.path.join(temp_dir, "full_audio.wav")
        extract_audio(video_path, audio_path)
        transcription = transcribe_audio(audio_path, language=whisper_language)

    db.save_transcript(db_path, cache_key, transcription.get("language"), transcription)
    return transcription
