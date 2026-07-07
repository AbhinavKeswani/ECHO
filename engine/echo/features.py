"""Extract ECHO's per-track feature bundle from a decoded waveform.

Two complementary strata, matching the plan:

  * Librosa (DSP): fine-grained timbre/rhythm/tonality summary statistics — MFCCs,
    chroma, spectral shape, tonnetz, onset/tempo, dynamics. ~60 interpretable scalars.
  * Essentia (ML + DSP): Discogs-EffNet + MSD-MusiCNN embeddings (the strongest vibe
    signal), plus mood/danceability heads and key/BPM/loudness — the replacement for
    Spotify's dead audio-features endpoint.

`analyze()` returns the dict shape that Store.save_features() persists. Essentia model
graphs are loaded lazily and memoized so a full backfill instantiates each once.
"""

from __future__ import annotations

import logging
import statistics
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import models
from .config import ANALYZE_SECONDS
from .audio import SAMPLE_RATE

log = logging.getLogger("echo.features")

# Essentia's TensorFlow models were trained on 16 kHz mono audio.
_ESSENTIA_SR = 16000


def _center_crop(y: np.ndarray, sr: int, seconds: int) -> np.ndarray:
    """Return the central `seconds`-long window of a signal (whole signal if shorter)."""
    if seconds <= 0:
        return y
    want = seconds * sr
    if len(y) <= want:
        return y
    start = (len(y) - want) // 2
    return y[start : start + want]


# --- Librosa DSP features ----------------------------------------------------


def librosa_features(y: np.ndarray, sr: int = SAMPLE_RATE) -> dict[str, float]:
    """Summary statistics over standard Librosa descriptors. All plain floats."""
    import librosa

    feats: dict[str, float] = {}

    def _stats(name: str, arr: np.ndarray) -> None:
        arr = np.asarray(arr, dtype=np.float64)
        feats[f"{name}_mean"] = float(np.mean(arr))
        feats[f"{name}_std"] = float(np.std(arr))

    # Timbre: MFCCs (13) + deltas summarize spectral envelope / "texture".
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    for i in range(mfcc.shape[0]):
        _stats(f"mfcc{i+1}", mfcc[i])

    # Tonality: chroma (pitch-class energy) + tonnetz (harmonic centroid space).
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    _stats("chroma", chroma)
    tonnetz = librosa.feature.tonnetz(y=librosa.effects.harmonic(y), sr=sr)
    _stats("tonnetz", tonnetz)

    # Spectral shape: brightness / bandwidth / rolloff / noisiness.
    _stats("spec_centroid", librosa.feature.spectral_centroid(y=y, sr=sr))
    _stats("spec_bandwidth", librosa.feature.spectral_bandwidth(y=y, sr=sr))
    _stats("spec_rolloff", librosa.feature.spectral_rolloff(y=y, sr=sr))
    _stats("spec_contrast", librosa.feature.spectral_contrast(y=y, sr=sr))
    _stats("spec_flatness", librosa.feature.spectral_flatness(y=y))
    _stats("zcr", librosa.feature.zero_crossing_rate(y))

    # Rhythm + dynamics.
    tempo = librosa.feature.tempo(y=y, sr=sr)
    feats["tempo"] = float(np.atleast_1d(tempo)[0])
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    _stats("onset_strength", onset_env)
    _stats("rms", librosa.feature.rms(y=y))

    return feats


# --- Essentia ML + scalar features -------------------------------------------


@lru_cache(maxsize=1)
def _effnet():
    import essentia.standard as es

    return es.TensorflowPredictEffnetDiscogs(
        graphFilename=str(models.path(models.EFFNET_MODEL)),
        output="PartitionedCall:1",  # embedding layer (not the 400-style logits)
    )


@lru_cache(maxsize=1)
def _musicnn():
    import essentia.standard as es

    return es.TensorflowPredictMusiCNN(
        graphFilename=str(models.path(models.MUSICNN_MODEL)),
        output="model/dense/BiasAdd",  # penultimate embedding layer
    )


@lru_cache(maxsize=16)
def _mood_head(model_file: str):
    import essentia.standard as es

    # Mood/danceability heads consume the EffNet embedding and emit a 2-class softmax.
    return es.TensorflowPredict2D(
        graphFilename=str(models.path(model_file)),
        output="model/Softmax",
    )


def _load_16k(audio_path: Path) -> np.ndarray:
    import essentia.standard as es

    return es.MonoLoader(filename=str(audio_path), sampleRate=_ESSENTIA_SR, resampleQuality=4)()


def essentia_features(audio_path: Path, y44: np.ndarray) -> dict:
    """Embeddings + mood/danceability + key/BPM/loudness via Essentia.

    Needs the raw file (Essentia's MonoLoader handles its own 16 kHz resample for the
    TF models); `y44` is the already-decoded 44.1 kHz signal reused for DSP estimators.
    """
    import essentia.standard as es

    out: dict = {}
    audio16 = _center_crop(_load_16k(audio_path), _ESSENTIA_SR, ANALYZE_SECONDS)

    # EffNet embedding (per-patch → time-averaged into one vector) — the vibe signal.
    effnet_patches = _effnet()(audio16)
    effnet_emb = np.mean(effnet_patches, axis=0)
    out["effnet_emb"] = [float(x) for x in effnet_emb]

    musicnn_patches = _musicnn()(audio16)
    out["musicnn_emb"] = [float(x) for x in np.mean(musicnn_patches, axis=0)]

    # Mood / danceability heads run on the EffNet patch embeddings.
    mood: dict[str, float] = {}
    for name, model_file in models.MOOD_HEADS.items():
        if not models.is_present(model_file):
            continue
        try:
            preds = _mood_head(model_file)(effnet_patches)
            # class 0 is the positive label ("happy", "danceable", ...) in Essentia heads.
            mood[name] = float(np.mean(preds, axis=0)[0])
        except Exception as e:  # noqa: BLE001
            log.warning("mood head %s failed: %s", model_file, e)
    out["mood"] = mood

    # Key / scale, BPM, integrated loudness — DSP estimators on the 44.1 kHz signal.
    try:
        key, scale, strength = es.KeyExtractor()(y44)
        out["key"] = f"{key} {scale}"
        out["key_strength"] = float(strength)
    except Exception as e:  # noqa: BLE001
        log.warning("KeyExtractor failed: %s", e)

    try:
        bpm, _, _, _, _ = es.RhythmExtractor2013(method="multifeature")(y44)
        out["bpm"] = float(bpm)
    except Exception as e:  # noqa: BLE001
        log.warning("RhythmExtractor2013 failed: %s", e)

    try:
        _, _, integrated, _ = es.LoudnessEBUR128()(np.column_stack([y44, y44]))
        out["loudness"] = float(integrated)
    except Exception as e:  # noqa: BLE001
        log.warning("LoudnessEBUR128 failed: %s", e)

    return out


# --- Top-level bundle --------------------------------------------------------


def analyze(audio_path: Path, y44: np.ndarray) -> dict:
    """Full feature bundle for one track, shaped for Store.save_features()."""
    y44 = _center_crop(y44, SAMPLE_RATE, ANALYZE_SECONDS)
    lib = librosa_features(y44)
    ess = essentia_features(audio_path, y44)
    return {
        "librosa": lib,
        "essentia": {
            "mood": ess.get("mood", {}),
            "key_strength": ess.get("key_strength"),
        },
        "effnet_emb": ess.get("effnet_emb"),
        "musicnn_emb": ess.get("musicnn_emb"),
        "bpm": ess.get("bpm"),
        "key": ess.get("key"),
        "loudness": ess.get("loudness"),
    }
