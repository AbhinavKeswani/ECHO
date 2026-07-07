"""The extraction pipeline: for each un-analyzed track, fetch audio and extract features.

Resumable by construction — work is driven off `features.status`, so an interrupted
backfill picks up exactly where it stopped, and re-running only touches pending/failed
rows. Kept single-process and sequential: the Essentia TF graphs and ffmpeg decode are
already multi-threaded internally, and a personal library (<3k tracks) finishes in one
overnight pass without the complexity of a worker pool.
"""

from __future__ import annotations

import logging
import time

from . import audio, features, models
from .bus import EventBus
from .config import KEEP_AUDIO
from .store import Store

log = logging.getLogger("echo.ingest")


def ingest_one(store: Store, track: dict) -> tuple[bool, str]:
    """Fetch + analyze a single track, persisting the result. Returns (ok, message)."""
    tid = track["id"]
    label = f"{track['artist']} — {track['title']}"
    audio_path = None
    try:
        audio_path, source_id = audio.acquire(track)
        y44 = audio.load_mono(audio_path)
        bundle = features.analyze(audio_path, y44)
        bundle["audio_source"] = source_id
        bundle["audio_query"] = audio.search_query(track["artist"], track["title"])
        store.save_features(tid, bundle)
        return True, label
    except Exception as e:  # noqa: BLE001 — never let one bad track kill the backfill
        store.mark_feature_failed(tid, str(e))
        return False, f"{label}: {e}"
    finally:
        if audio_path is not None:
            audio.cleanup(audio_path, keep=KEEP_AUDIO)


def run(limit: int | None = None, bus: EventBus | None = None) -> dict:
    """Process pending/failed tracks. Returns a summary dict."""
    missing = models.missing()
    if missing:
        raise RuntimeError(
            f"{len(missing)} Essentia model(s) not downloaded (e.g. {missing[0]}). "
            "Run `echo models` first."
        )

    store = Store()
    queue = store.pending_features(limit=limit)
    total = len(queue)
    ok = 0
    failed = 0
    started = time.time()
    log.info("ingest: %d track(s) to process", total)

    for i, track in enumerate(queue, 1):
        success, msg = ingest_one(store, track)
        if success:
            ok += 1
            log.info("[%d/%d] ✓ %s", i, total, msg)
        else:
            failed += 1
            log.warning("[%d/%d] ✗ %s", i, total, msg)
        if bus:
            bus.publish("track_ingested", {"done": i, "total": total, "ok": success})

    elapsed = time.time() - started
    store.close()
    return {"total": total, "ok": ok, "failed": failed, "seconds": round(elapsed, 1)}
