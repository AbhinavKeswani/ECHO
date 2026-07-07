"""Essentia pretrained model registry + downloader.

The TensorFlow-backed Essentia algorithms need local .pb graph files. We fetch them
once from the official Essentia model zoo (essentia.upf.edu) into the app-support dir
and reuse them offline forever after. Two kinds of model:

  * feature extractors — turn audio into an embedding (Discogs-EffNet, MSD-MusiCNN).
  * classification heads — turn an embedding into a score (mood/danceability). Each
    head is trained on ONE embedding, so the head file name encodes its backbone.

EffNet embeddings are ECHO's primary "vibe" signal; the mood/danceability heads give
interpretable, Spotify-like scalar features to replace the dead audio-features endpoint.
"""

from __future__ import annotations

import logging
import urllib.request
from pathlib import Path

from .config import APP_SUPPORT

log = logging.getLogger("echo.models")

MODELS_DIR = APP_SUPPORT / "essentia_models"
_BASE = "https://essentia.upf.edu/models"

# filename -> remote path under _BASE
_REGISTRY: dict[str, str] = {
    # --- Embedding backbones ---
    "discogs-effnet-bs64-1.pb": "feature-extractors/discogs-effnet/discogs-effnet-bs64-1.pb",
    "msd-musicnn-1.pb": "feature-extractors/musicnn/msd-musicnn-1.pb",
    # --- Classification heads (all on the discogs-effnet backbone) ---
    "danceability-discogs-effnet-1.pb": "classification-heads/danceability/danceability-discogs-effnet-1.pb",
    "mood_happy-discogs-effnet-1.pb": "classification-heads/mood_happy/mood_happy-discogs-effnet-1.pb",
    "mood_sad-discogs-effnet-1.pb": "classification-heads/mood_sad/mood_sad-discogs-effnet-1.pb",
    "mood_aggressive-discogs-effnet-1.pb": "classification-heads/mood_aggressive/mood_aggressive-discogs-effnet-1.pb",
    "mood_relaxed-discogs-effnet-1.pb": "classification-heads/mood_relaxed/mood_relaxed-discogs-effnet-1.pb",
    "mood_party-discogs-effnet-1.pb": "classification-heads/mood_party/mood_party-discogs-effnet-1.pb",
    "mood_acoustic-discogs-effnet-1.pb": "classification-heads/mood_acoustic/mood_acoustic-discogs-effnet-1.pb",
    "mood_electronic-discogs-effnet-1.pb": "classification-heads/mood_electronic/mood_electronic-discogs-effnet-1.pb",
}

# The classification heads ECHO scores each track with (name -> model file).
MOOD_HEADS: dict[str, str] = {
    "danceable": "danceability-discogs-effnet-1.pb",
    "happy": "mood_happy-discogs-effnet-1.pb",
    "sad": "mood_sad-discogs-effnet-1.pb",
    "aggressive": "mood_aggressive-discogs-effnet-1.pb",
    "relaxed": "mood_relaxed-discogs-effnet-1.pb",
    "party": "mood_party-discogs-effnet-1.pb",
    "acoustic": "mood_acoustic-discogs-effnet-1.pb",
    "electronic": "mood_electronic-discogs-effnet-1.pb",
}

EFFNET_MODEL = "discogs-effnet-bs64-1.pb"
MUSICNN_MODEL = "msd-musicnn-1.pb"


def path(filename: str) -> Path:
    return MODELS_DIR / filename


def is_present(filename: str) -> bool:
    p = path(filename)
    return p.exists() and p.stat().st_size > 1024  # guard against truncated downloads


def download_all(force: bool = False) -> dict[str, str]:
    """Fetch every registered model. Returns {filename: 'ok'|'skipped'|error-string}."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, str] = {}
    for fname, remote in _REGISTRY.items():
        if not force and is_present(fname):
            results[fname] = "skipped"
            continue
        url = f"{_BASE}/{remote}"
        dest = path(fname)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            log.info("downloading %s", fname)
            urllib.request.urlretrieve(url, tmp)
            tmp.replace(dest)
            results[fname] = "ok"
        except Exception as e:  # noqa: BLE001 — report per-file, keep going
            if tmp.exists():
                tmp.unlink()
            results[fname] = f"error: {e}"
            log.warning("failed %s: %s", fname, e)
    return results


def missing() -> list[str]:
    return [f for f in _REGISTRY if not is_present(f)]
