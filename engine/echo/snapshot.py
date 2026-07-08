"""Portable library snapshot — a single git-syncable file for multi-device workflows.

Lets one machine act as a compute node: a storage-light box (without Atlas or the rest
of the local ecosystem) runs the heavy Librosa/Essentia analysis + enrichment, exports
the results here, and syncs them via git to whichever device queries/visualizes the
graph. Import merges by Spotify id, so the compute node's features and your local pair
labels both survive a round trip.

The file is gzipped JSON — compact enough to commit, and self-describing (format version).

PRIVACY: a snapshot contains your actual liked-songs library + analysis. Keep it OUT of a
public repo — sync it through a PRIVATE repo/branch (see docs/AGENTS.md). ECHO's .gitignore
excludes `*.snapshot.json.gz` by default so it never lands in the public code repo by accident.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from .store import Store


def write_snapshot(store: Store, path: Path) -> dict:
    """Export the library to a gzipped-JSON snapshot. Returns summary counts."""
    data = store.export_snapshot()
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(data, f)
    analyzed = sum(1 for t in data["tracks"] if (t.get("features") or {}).get("status") == "analyzed")
    return {"tracks": len(data["tracks"]), "analyzed": analyzed, "pairs": len(data["pairs"])}


def read_snapshot(store: Store, path: Path) -> dict[str, int]:
    """Merge a gzipped-JSON snapshot into the local DB. Returns import counts."""
    if not path.exists():
        raise FileNotFoundError(path)
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("format") != 1:
        raise ValueError(f"unsupported snapshot format: {data.get('format')}")
    return store.import_snapshot(data)
