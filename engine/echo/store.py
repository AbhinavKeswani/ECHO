"""SQLite persistence for ECHO.

Full schema for every phase is created up front — sync (tracks), extraction
(features + embeddings), the vibe model (pairs, models), and the graph (clusters,
edges) — so later phases are purely additive, no migrations. A generic `settings`
key/value table holds JSON config (the Spotify sync cursor, active model id, etc.).

Single-writer model copied from Atlas/Vein's store.py: one connection, we serialize
access ourselves from the asyncio loop / CLI. Embeddings are stored as JSON text —
at a personal library's scale (<3k songs) a brute-force numpy scan is instant, so no
vector-index extension is needed; sqlite-vec can be layered on later if it ever grows.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .config import DB_PATH, ensure_dirs

_SCHEMA = """
-- ---------- Tracks (Spotify library: metadata only) ----------
CREATE TABLE IF NOT EXISTS tracks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    spotify_id  TEXT UNIQUE NOT NULL,
    title       TEXT NOT NULL,
    artist      TEXT NOT NULL,               -- primary artist (display)
    artists     TEXT,                        -- JSON array of all artist names
    album       TEXT,
    duration_ms INTEGER,
    isrc        TEXT,                         -- stable cross-catalog id (helps yt-dlp match)
    popularity  INTEGER,
    spotify_url TEXT,
    preview_url TEXT,                          -- 30s snippet (extended-quota apps only)
    added_at    REAL,                         -- when it entered Liked Songs (epoch)
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tracks_added ON tracks(added_at);

-- ---------- Features (local audio analysis: Librosa + Essentia) ----------
-- One row per track. `status` drives the resumable ingest pipeline.
CREATE TABLE IF NOT EXISTS features (
    track_id      INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending|fetching|analyzed|failed|skipped
    audio_source  TEXT,                             -- e.g. 'youtube:<id>'
    audio_query   TEXT,                             -- the search string yt-dlp used
    librosa       TEXT,                             -- JSON: MFCC/chroma/spectral/tonnetz/onset summary stats
    essentia      TEXT,                             -- JSON: mood/danceability/key/bpm/loudness scalar features
    effnet_emb    TEXT,                             -- JSON float[]: Discogs-EffNet embedding (vibe signal)
    musicnn_emb   TEXT,                             -- JSON float[]: MSD-MusiCNN embedding
    bpm           REAL,
    key           TEXT,                             -- e.g. 'A minor'
    loudness      REAL,                             -- integrated LUFS
    cluster_id    INTEGER REFERENCES clusters(id),  -- assigned in M3 / on ingest (M6)
    cluster_score REAL,                             -- similarity to assigned cluster centroid
    error         TEXT,                             -- last failure reason (status='failed')
    attempts      INTEGER NOT NULL DEFAULT 0,
    analyzed_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_features_status ON features(status);
CREATE INDEX IF NOT EXISTS idx_features_cluster ON features(cluster_id);

-- ---------- Vibe pairs (Phase 1 labels — ECHO pitches, user judges) ----------
CREATE TABLE IF NOT EXISTS pairs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    song_a_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    song_b_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    label        TEXT NOT NULL DEFAULT 'pending',  -- pending|yes|no|skip
    source       TEXT NOT NULL DEFAULT 'bootstrap',-- bootstrap|uncertainty|manual
    model_conf   REAL,                             -- model's P(same-vibe) when pitched
    created_at   REAL NOT NULL,
    labeled_at   REAL,
    UNIQUE(song_a_id, song_b_id)
);
CREATE INDEX IF NOT EXISTS idx_pairs_label ON pairs(label);

-- ---------- Vibe model versions (learned feature weights) ----------
CREATE TABLE IF NOT EXISTS models (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    weights       TEXT NOT NULL,                    -- JSON: {feature_name: weight}
    feature_names TEXT NOT NULL,                    -- JSON array (column order)
    metrics       TEXT,                             -- JSON: {auc, n_pairs, cv_auc, ...}
    n_pairs       INTEGER NOT NULL DEFAULT 0,
    active        INTEGER NOT NULL DEFAULT 0,
    trained_at    REAL NOT NULL
);

-- ---------- Vibe clusters (Phase 2) ----------
CREATE TABLE IF NOT EXISTS clusters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,                                -- LLM-generated ('Rainy 3am', 'Sunlit drive')
    description TEXT,
    size        INTEGER NOT NULL DEFAULT 0,
    centroid    TEXT,                                -- JSON: mean feature/embedding vector
    exemplars   TEXT,                                -- JSON: [track_id, ...] most-central songs
    color       TEXT,                                -- hex, for the graph
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

-- ---------- Graph edges (learned-metric kNN + confirmed labels) ----------
CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    song_a_id   INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    song_b_id   INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    weight      REAL NOT NULL,                       -- learned similarity (higher = closer)
    edge_type   TEXT NOT NULL DEFAULT 'knn',         -- knn|label
    created_at  REAL NOT NULL,
    UNIQUE(song_a_id, song_b_id, edge_type)
);
CREATE INDEX IF NOT EXISTS idx_edges_a ON edges(song_a_id);

-- ---------- Enrichment (external feature DBs, joined by ISRC/MBID) ----------
-- Precomputed features from open databases, fused with local analysis for coverage
-- + cross-validation. One row per track; each source stored as its own JSON blob.
CREATE TABLE IF NOT EXISTS enrichment (
    track_id      INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending|enriched|partial|failed
    mbid          TEXT,                             -- resolved MusicBrainz recording id
    ab_highlevel  TEXT,                             -- JSON: AcousticBrainz Essentia high-level (mood/genre/danceability)
    deezer        TEXT,                             -- JSON: {deezer_id, bpm, gain, preview_url}
    lastfm_tags   TEXT,                             -- JSON: [{tag, count}, ...]
    error         TEXT,
    updated_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_enrichment_status ON enrichment(status);

-- ---------- Generic settings (JSON values) ----------
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        ensure_dirs()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL;")
        self._db.execute("PRAGMA synchronous=NORMAL;")
        self._db.execute("PRAGMA foreign_keys=ON;")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    # --- Settings (JSON kv) --------------------------------------------------

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self._db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return default

    def set_setting(self, key: str, value: Any) -> None:
        self._db.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
        self._db.commit()

    # --- Tracks --------------------------------------------------------------

    def upsert_track(self, t: dict) -> int:
        """Insert a liked track by spotify_id (or refresh mutable metadata).

        Creates the paired `features` row (status='pending') on first insert so the
        ingest pipeline has a work item. Returns the internal track id.
        """
        now = time.time()
        row = self._db.execute(
            "SELECT id FROM tracks WHERE spotify_id=?", (t["spotify_id"],)
        ).fetchone()
        if row:
            self._db.execute(
                "UPDATE tracks SET title=?, artist=?, artists=?, album=?, duration_ms=?, "
                "isrc=COALESCE(?,isrc), popularity=?, spotify_url=?, preview_url=COALESCE(?,preview_url), "
                "added_at=COALESCE(?,added_at), updated_at=? WHERE id=?",
                (t["title"], t["artist"], json.dumps(t.get("artists") or [t["artist"]]),
                 t.get("album"), t.get("duration_ms"), t.get("isrc"), t.get("popularity"),
                 t.get("spotify_url"), t.get("preview_url"), t.get("added_at"), now, row["id"]),
            )
            tid = row["id"]
        else:
            cur = self._db.execute(
                "INSERT INTO tracks(spotify_id, title, artist, artists, album, duration_ms, "
                "isrc, popularity, spotify_url, preview_url, added_at, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (t["spotify_id"], t["title"], t["artist"], json.dumps(t.get("artists") or [t["artist"]]),
                 t.get("album"), t.get("duration_ms"), t.get("isrc"), t.get("popularity"),
                 t.get("spotify_url"), t.get("preview_url"), t.get("added_at"), now, now),
            )
            tid = int(cur.lastrowid)
            self._db.execute(
                "INSERT OR IGNORE INTO features(track_id, status) VALUES (?, 'pending')", (tid,)
            )
            self._db.execute(
                "INSERT OR IGNORE INTO enrichment(track_id, status) VALUES (?, 'pending')", (tid,)
            )
        self._db.commit()
        return tid

    def get_track(self, track_id: int) -> dict | None:
        row = self._db.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
        return dict(row) if row else None

    def known_spotify_ids(self) -> set[str]:
        return {r["spotify_id"] for r in self._db.execute("SELECT spotify_id FROM tracks")}

    def list_tracks(self, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM tracks ORDER BY added_at DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self._db.execute(sql).fetchall()]

    def count_tracks(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0])

    # --- Features / ingest queue --------------------------------------------

    def pending_features(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Tracks whose audio hasn't been analyzed yet (status pending|failed), newest first."""
        sql = (
            "SELECT t.* FROM tracks t JOIN features f ON f.track_id=t.id "
            "WHERE f.status IN ('pending','failed') ORDER BY t.added_at DESC"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self._db.execute(sql).fetchall()]

    def save_features(self, track_id: int, feats: dict) -> None:
        """Persist an analyzed feature bundle and mark the row analyzed."""
        self._db.execute(
            "UPDATE features SET status='analyzed', audio_source=?, audio_query=?, "
            "librosa=?, essentia=?, effnet_emb=?, musicnn_emb=?, bpm=?, key=?, loudness=?, "
            "error=NULL, attempts=attempts+1, analyzed_at=? WHERE track_id=?",
            (feats.get("audio_source"), feats.get("audio_query"),
             json.dumps(feats.get("librosa")), json.dumps(feats.get("essentia")),
             json.dumps(feats.get("effnet_emb")), json.dumps(feats.get("musicnn_emb")),
             feats.get("bpm"), feats.get("key"), feats.get("loudness"),
             time.time(), track_id),
        )
        self._db.commit()

    def mark_feature_failed(self, track_id: int, error: str) -> None:
        self._db.execute(
            "UPDATE features SET status='failed', error=?, attempts=attempts+1 WHERE track_id=?",
            (error[:500], track_id),
        )
        self._db.commit()

    def feature_counts(self) -> dict[str, int]:
        rows = self._db.execute("SELECT status, COUNT(*) n FROM features GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    def analyzed_features(self) -> list[dict[str, Any]]:
        """All analyzed rows joined to track metadata — the modeling/graph working set."""
        rows = self._db.execute(
            "SELECT t.id, t.title, t.artist, f.librosa, f.essentia, f.effnet_emb, f.musicnn_emb, "
            "f.bpm, f.key, f.loudness, f.cluster_id "
            "FROM tracks t JOIN features f ON f.track_id=t.id WHERE f.status='analyzed'"
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Pairs ---------------------------------------------------------------

    def add_pair(self, a: int, b: int, source: str = "bootstrap", model_conf: float | None = None) -> int | None:
        """Queue a candidate vibe pair for the user to judge. Ignores dupes/self-pairs.

        Pairs are stored order-normalized (a < b) so (x,y) and (y,x) can't both queue.
        Returns the pair id, or None if it already existed / was invalid.
        """
        if a == b:
            return None
        a, b = (a, b) if a < b else (b, a)
        cur = self._db.execute(
            "INSERT OR IGNORE INTO pairs(song_a_id, song_b_id, source, model_conf, created_at) "
            "VALUES (?,?,?,?,?)",
            (a, b, source, model_conf, time.time()),
        )
        self._db.commit()
        return int(cur.lastrowid) if cur.rowcount else None

    def label_pair(self, pair_id: int, label: str) -> dict | None:
        self._db.execute(
            "UPDATE pairs SET label=?, labeled_at=? WHERE id=?",
            (label, time.time(), pair_id),
        )
        self._db.commit()
        row = self._db.execute("SELECT * FROM pairs WHERE id=?", (pair_id,)).fetchone()
        return dict(row) if row else None

    def next_pending_pair(self) -> dict | None:
        """The next pitched pair awaiting a verdict, with both tracks' display info."""
        row = self._db.execute(
            "SELECT p.id, p.song_a_id, p.song_b_id, p.source, p.model_conf, "
            "a.title AS a_title, a.artist AS a_artist, b.title AS b_title, b.artist AS b_artist "
            "FROM pairs p JOIN tracks a ON a.id=p.song_a_id JOIN tracks b ON b.id=p.song_b_id "
            "WHERE p.label='pending' ORDER BY p.model_conf IS NULL, ABS(COALESCE(p.model_conf,0.5)-0.5), p.id "
            "LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def labeled_pairs(self) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM pairs WHERE label IN ('yes','no') ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def pair_counts(self) -> dict[str, int]:
        rows = self._db.execute("SELECT label, COUNT(*) n FROM pairs GROUP BY label").fetchall()
        return {r["label"]: r["n"] for r in rows}

    # --- Enrichment ----------------------------------------------------------

    def tracks_needing_enrichment(self, limit: int | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT t.* FROM tracks t JOIN enrichment e ON e.track_id=t.id "
            "WHERE e.status IN ('pending','failed') ORDER BY t.added_at DESC"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self._db.execute(sql).fetchall()]

    def save_enrichment(self, track_id: int, data: dict) -> None:
        """Persist enrichment blobs. status is 'enriched' if any source hit, else 'partial'."""
        hit = any(data.get(k) for k in ("ab_highlevel", "deezer", "lastfm_tags"))
        self._db.execute(
            "UPDATE enrichment SET status=?, mbid=?, ab_highlevel=?, deezer=?, lastfm_tags=?, "
            "error=NULL, updated_at=? WHERE track_id=?",
            ("enriched" if hit else "partial", data.get("mbid"),
             json.dumps(data.get("ab_highlevel")) if data.get("ab_highlevel") else None,
             json.dumps(data.get("deezer")) if data.get("deezer") else None,
             json.dumps(data.get("lastfm_tags")) if data.get("lastfm_tags") else None,
             time.time(), track_id),
        )
        self._db.commit()

    def mark_enrichment_failed(self, track_id: int, error: str) -> None:
        self._db.execute(
            "UPDATE enrichment SET status='failed', error=?, updated_at=? WHERE track_id=?",
            (error[:500], time.time(), track_id),
        )
        self._db.commit()

    def get_enrichment(self, track_id: int) -> dict | None:
        row = self._db.execute("SELECT * FROM enrichment WHERE track_id=?", (track_id,)).fetchone()
        return dict(row) if row else None

    def enrichment_counts(self) -> dict[str, int]:
        rows = self._db.execute("SELECT status, COUNT(*) n FROM enrichment GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    def close(self) -> None:
        self._db.close()
