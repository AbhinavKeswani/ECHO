# ECHO — Requirements

## 1. Purpose

A personalized knowledge graph of the user's musical taste. Songs connect by **learned
vibe similarity** — how they feel to *this* user — not by genre. Deliverables: vibe-matched
playlists, a "perfect next song" recommender, natural-language library query ("find songs
that feel like a rainy 3am"), and an interactive Obsidian/Graphify-style graph.

## 2. System requirements

| Requirement | Detail |
|---|---|
| OS | macOS (arm64 verified). Linux works for the non-loopback parts. |
| Python | 3.12 (via `uv`; system 3.9 is too old). Pinned in `.python-version`. |
| Package manager | `uv` (Homebrew). Reproducible via `uv.lock`. |
| ffmpeg | Required (audio decode + yt-dlp). `brew install ffmpeg`. Verified 8.1.2. |
| Disk | Models ~1 GB; audio cache transient (purged per track unless `ECHO_KEEP_AUDIO=1`). |
| Network | Spotify API, yt-dlp/Deezer (audio), MusicBrainz/AcousticBrainz/Deezer (enrichment). |
| Critical pin | `essentia-tensorflow==2.1b6.dev1389` — the only cp312 arm64 wheel. Do **not** bump (newer = cp314-only; can't move to 3.14 because librosa/numba lags). |

## 3. External services

| Service | Role | Auth | Notes |
|---|---|---|---|
| Spotify Web API | Library sync, metadata, playlist writing | OAuth (Client ID/Secret) | audio-features endpoints are DEAD — sync/playlists only. |
| yt-dlp / YouTube | Default audio source for analysis | none | Gray-area; personal use only. |
| Deezer | Alt audio source (30s preview) + BPM | none | Keyless; legit preview path. |
| MusicBrainz | ISRC→MBID join key | none (UA required) | 50 req/min. |
| AcousticBrainz | Essentia high-level features | none | Read-only 2022 dump; per-MBID coverage. |
| Last.fm | Crowd tags (optional) | free API key | Semantic layer. |
| Claude CLI | LLM cluster naming (M3) | existing `claude` login | Same bridge pattern as Atlas. |

## 4. Functional requirements

### 4.1 Library sync (M0 — done)
- Pull all Spotify Liked Songs (metadata + ISRC + preview_url) into `echo.db`, incremental.
- Store: title, artist(s), album, duration, ISRC, popularity, Spotify URL, preview_url, added_at.

### 4.2 Audio extraction (M1 — done)
- Per track: acquire audio (pluggable backend), decode to mono, extract features:
  - **Librosa** (~47 scalars): MFCCs, chroma, tonnetz, spectral shape, tempo, onset, RMS.
  - **Essentia**: Discogs-EffNet embedding (1280-d, primary vibe signal), MSD-MusiCNN (200-d),
    8 mood/danceability heads, key/scale, BPM, integrated loudness.
- **Pluggable audio source** (`ECHO_AUDIO_SOURCE`): `ytdlp` (default) | `preview` (Spotify or
  Deezer 30s) | `loopback` (jarvis device capture — see docs/JARVIS.md).
- Analyze a central window (`ECHO_ANALYZE_SECONDS`, default 120) for ~4x speed at equal fidelity.
- **Resumable** (driven off `features.status`); failures logged and retried, never fatal.

### 4.3 Enrichment / confluence (built — see docs/ENRICHMENT.md)
- Fuse external feature DBs by ISRC: MusicBrainz→MBID→AcousticBrainz high-level, Deezer BPM/preview,
  Last.fm tags. API-only, fast, runs independent of local ingest. `echo enrich`.
- Purpose: coverage fill + cross-validation + a semantic (tag) view → multi-modal graph.

### 4.4 Vibe model (M2 — next)
- ECHO **pitches** candidate pairs (stratified from EffNet similarity); user confirms/rejects
  (`yes`/`no`/`skip`). No cold hand-labeling.
- Train a pairwise model on |feature-diff| vectors → interpretable per-feature weights
  ("what makes a vibe for you") ⇒ a learned weighted distance.
- **Uncertainty sampling**: next pitches are the model's least-confident pairs.
- Success: held-out pair AUC beats raw-cosine baseline; weights are legible.

### 4.5 Graph + clusters (M3)
- Learned-metric kNN graph + HDBSCAN clusters in the metric space. Multi-modal edges
  (acoustic + AB agreement + tag overlap). LLM names clusters from exemplars via Claude bridge.

### 4.6 Query + output (M4/M5)
- MCP server: `vibe_search`, `similar_songs`, `next_song`, `make_playlist` (writes real Spotify
  playlist), `label_pair`.
- FastAPI server (`:8771`) hosting a force-directed graph SPA (glass UI, matches Atlas) +
  `/echo/*` API. Obsidian vault export (one note/song with `[[links]]`).
- Web Playback SDK (Premium) as the graph's playback surface + real-time now-playing.

### 4.7 Ongoing + integration (M6)
- launchd poll of Liked Songs; new songs auto-ingested/enriched/classified into nearest cluster
  (or flagged "new vibe territory").
- Atlas "Music" tab reads ECHO read-only (cross-DB + HTTP fallback, mirroring `meetings.py`);
  ECHO files `source='echo'` todos into Atlas ("confirm N pitched pairs").

## 5. Data model (`echo.db`, full schema up front)

`tracks` · `features` (+embeddings, cluster assignment) · `enrichment` (mbid, ab_highlevel,
deezer, lastfm_tags) · `pairs` (labels) · `models` (weights) · `clusters` · `edges` · `settings`.

## 6. Non-functional

- **Local-first**: binds 127.0.0.1; nothing leaves the box except the intentional API calls.
- **Resumable & idempotent**: every long job re-runnable; work driven off status columns.
- **Portable**: git repo + `uv.lock` → clones to the jarvis device unchanged (loopback backend).
- **Conventions**: match Atlas/Vein — SQLite single-writer, JSON blobs, `created_at`/`updated_at`/
  `source`, asyncio.Queue event bus, type hints + docstrings, functions over classes.

## 7. Constraints / risks

- Spotify audio-features permanently gone → local analysis is mandatory, not optional.
- yt-dlp matching can fetch the wrong track; Deezer-preview and loopback are exacter.
- AcousticBrainz coverage is partial (per-MBID) — treat as bonus, not baseline.
- Essentia wheel pin is fragile; document before any dependency bump.
