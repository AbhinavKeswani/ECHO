"""Acquire source audio for a track and hand back a decoded mono waveform.

Spotify serves no audio to standard apps, so ECHO acquires audio through a pluggable
backend selected by config.AUDIO_SOURCE — so the same pipeline runs unchanged whether
audio comes from YouTube, Spotify previews, or a loopback capture on another machine:

  * ytdlp    — search "<artist> <title>" and download the best audio-only stream.
  * preview  — download the track's 30s Spotify preview_url (extended-quota apps only).
  * loopback — play the track on this device and capture system audio (the jarvis
               backend; not implemented here — see docs/JARVIS.md).

Every backend returns a path to a decodable file in AUDIO_CACHE plus a source id; the
caller decodes it with load_mono(). Nothing here is destructive: files are purged after
analysis unless ECHO_KEEP_AUDIO=1.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from . import config
from .config import AUDIO_CACHE

log = logging.getLogger("echo.audio")

SAMPLE_RATE = 44100


class AudioError(RuntimeError):
    pass


def search_query(artist: str, title: str) -> str:
    # "audio" nudges yt-dlp toward the track upload rather than a live/video version.
    return f"{artist} {title} audio".strip()


def _cached(track_key: str) -> Path | None:
    """Return an already-downloaded file for this track, if any (skips .part)."""
    for existing in AUDIO_CACHE.glob(f"{track_key}.*"):
        if existing.suffix != ".part":
            return existing
    return None


def acquire(track: dict) -> tuple[Path, str]:
    """Get audio for a track via the configured backend. Returns (path, source_id)."""
    AUDIO_CACHE.mkdir(parents=True, exist_ok=True)
    src = config.AUDIO_SOURCE
    if src == "ytdlp":
        return _acquire_ytdlp(track)
    if src == "preview":
        return _acquire_preview(track)
    if src == "loopback":
        return _acquire_loopback(track)
    raise AudioError(f"unknown ECHO_AUDIO_SOURCE={src!r} (use ytdlp|preview|loopback)")


def _acquire_ytdlp(track: dict) -> tuple[Path, str]:
    """Download best audio from YouTube by searching '<artist> <title> audio'."""
    import yt_dlp

    track_key = track["spotify_id"]
    cached = _cached(track_key)
    if cached:
        return cached, f"youtube:{track_key}"

    query = search_query(track["artist"], track["title"])
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(AUDIO_CACHE / track_key) + ".%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch1",
        "match_filter": _reject_long,  # skip hour-long mixes/uploads
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=True)
    except Exception as e:  # noqa: BLE001
        raise AudioError(f"yt-dlp failed for '{query}': {e}") from e

    if info.get("entries"):
        info = info["entries"][0]
    if not info:
        raise AudioError(f"no result for '{query}'")

    got = _cached(track_key)
    if got:
        return got, f"youtube:{info.get('id', track_key)}"
    raise AudioError(f"download produced no file for '{query}'")


def _acquire_preview(track: dict) -> tuple[Path, str]:
    """Download the Spotify 30s preview_url (only present on extended-quota apps)."""
    import urllib.request

    track_key = track["spotify_id"]
    cached = _cached(track_key)
    if cached:
        return cached, "spotify_preview"

    url = track.get("preview_url")
    if not url:
        raise AudioError(
            "no preview_url for this track — the app may lack preview access "
            "(run `echo probe`) or this track has no preview."
        )
    dest = AUDIO_CACHE / f"{track_key}.mp3"  # Spotify previews are MP3
    tmp = dest.with_suffix(".mp3.part")
    try:
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(dest)
    except Exception as e:  # noqa: BLE001
        if tmp.exists():
            tmp.unlink()
        raise AudioError(f"preview download failed: {e}") from e
    return dest, "spotify_preview"


def _acquire_loopback(track: dict) -> tuple[Path, str]:
    """Play the track on this device and capture system audio (jarvis backend).

    Not implemented in this repo state — this is the port target for the idle
    'jarvis' device. It reuses Vein's CoreAudio tap to capture the muted system
    output while Spotify plays a central window of the track. See docs/JARVIS.md for
    the contract this must satisfy (return a decodable WAV in AUDIO_CACHE + source id).
    """
    raise AudioError(
        "loopback capture is the jarvis backend and isn't implemented yet — "
        "see docs/JARVIS.md. Use ECHO_AUDIO_SOURCE=ytdlp or preview until then."
    )


def _reject_long(info: dict, *, incomplete: bool = False):
    """yt-dlp match filter: skip results longer than 15 min (mixes, full albums)."""
    dur = info.get("duration")
    if dur and dur > 15 * 60:
        return f"too long ({dur}s)"
    return None


def load_mono(audio_path: Path, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Decode a file to a mono float32 waveform at `sr` (via librosa/soundfile+ffmpeg)."""
    import librosa

    try:
        y, _ = librosa.load(str(audio_path), sr=sr, mono=True)
    except Exception as e:  # noqa: BLE001
        raise AudioError(f"decode failed for {audio_path.name}: {e}") from e
    if y.size == 0:
        raise AudioError(f"empty audio: {audio_path.name}")
    return y.astype(np.float32)


def cleanup(audio_path: Path, keep: bool) -> None:
    if keep or not audio_path.exists():
        return
    try:
        audio_path.unlink()
    except OSError:
        pass
