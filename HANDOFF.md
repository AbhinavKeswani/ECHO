# ECHO — Agent handoff

For the next Claude Code agent picking up ECHO. Read this, then `README.md`,
`REQUIREMENTS.md`, and the plan file, before touching code.

## Mission

Build ECHO: a personalized knowledge graph of the user's musical taste where songs
connect by **learned vibe similarity**, not genre. Sibling app to Atlas (`~/Atlas`) and
Vein (`~/Vein-MacOS`) — match their stack and conventions exactly.

Plan of record: `~/.claude/plans/wobbly-marinating-lake.md`.

## Current state (built + verified, 2026-07)

**Done — M0 (scaffold + sync), M1 (extraction), + enrichment layer.** All verified end-to-end.

- Python 3.12/uv env fully installed incl. `essentia-tensorflow==2.1b6.dev1389` (arm64).
  ffmpeg installed. All 10 Essentia models downloaded. Git repo on `main`, 2 commits, no remote.
- Pipeline proven on real audio: yt-dlp → decode → 47 Librosa feats + 1280-d EffNet + 200-d
  MusiCNN + mood/key/BPM/loudness, ~14s/track (120s window).
- Enrichment proven live: MusicBrainz→AcousticBrainz (18 Essentia high-level classifiers/track)
  + Deezer (BPM + preview) + Last.fm (optional). `echo enrich`.
- Three audio backends: `ytdlp` (default, verified), `preview` (Spotify/Deezer 30s, verified
  ~7s/track), `loopback` (jarvis stub — see docs/JARVIS.md).

**Blocking on the user:** Spotify credentials in `~/ECHO/.env`. Until then, no live sync.
Everything else (enrichment APIs, audio backends, analysis) is keyless and testable.

## Your immediate job: orchestrate the backfill

Once the user has added credentials (they'll tell you, or `~/ECHO/.env` will have a
non-empty `ECHO_SPOTIFY_CLIENT_ID`):

1. `uv run echo probe` — record whether preview_url/audio-features are available; if
   preview works, suggest `ECHO_AUDIO_SOURCE=preview` (faster, cleaner than yt-dlp).
2. `uv run echo sync` — pull the library. Confirm track count is sane (~3k expected).
3. `uv run echo enrich` — fast API sweep first (independent of ingest). Report AB/Deezer hit rates.
4. `uv run echo ingest` (or `backfill`) — the long analysis. **Run it in the background**
   (`run_in_background`) and poll `uv run echo status`; it's resumable, so a stall isn't fatal —
   just re-run. Report progress + failure rate; investigate if failures are >~20%.
5. When enough tracks are analyzed (a few hundred), you can start M2 in parallel.

Do not re-run destructive steps or bump the Essentia pin (see gotchas).

## Architecture map (`engine/echo/`)

| File | Responsibility |
|---|---|
| `config.py` | Paths, ports, Spotify creds, `.env` autoload, `AUDIO_SOURCE`, enrichment endpoints. |
| `store.py` | SQLite schema (full, up front) + all data access. Single-writer, WAL. |
| `bus.py` | asyncio.Queue event pub/sub (WebSocket fan-out). |
| `spotify.py` | spotipy OAuth, Liked Songs sync, `probe_access`, playlist writing. |
| `models.py` | Essentia model registry + downloader. |
| `audio.py` | `acquire(track)` dispatcher → ytdlp / preview(+Deezer) / loopback backends; decode. |
| `features.py` | Librosa DSP + Essentia embeddings/mood/key/BPM. `analyze(path, y44)`. |
| `enrich.py` | MusicBrainz/AcousticBrainz/Deezer/Last.fm clients + `enrich_track(track)`. |
| `ingest.py` | Resumable acquire→analyze→persist orchestrator. |
| `cli.py` | `echo` entrypoint: init/probe/sync/models/ingest/backfill/enrich/status. |

Data model (`echo.db`): `tracks`, `features`, `enrichment`, `pairs`, `models`, `clusters`,
`edges`, `settings`. Work queues are driven off `features.status` / `enrichment.status`.

## What to build next (roadmap)

- **M2 — vibe model** (`pairs.py`, `model.py`; `echo pairs`, `echo train`): generate stratified
  candidate pairs from EffNet cosine similarity; a labeling flow (CLI now, MCP tool later) where
  ECHO *pitches* and the user judges yes/no/skip; train a pairwise logistic-regression on
  |feature-diff| vectors → interpretable weights; uncertainty-sample the next pitches. Fuse in
  enrichment views (AB high-level, tags) as extra features. Tables `pairs`/`models` already exist.
- **M3 — graph + clusters** (`graph.py`): learned-metric kNN + HDBSCAN (`uv sync --extra cluster`);
  multi-modal edges (acoustic + AB agreement + tag overlap); LLM cluster naming via a Claude bridge
  (copy Atlas's `claude_bridge.py`). Tables `clusters`/`edges` exist.
- **M4 — MCP server** (`mcp_server.py`; `uv sync --extra mcp`): vibe_search/similar/next_song/
  make_playlist/label_pair.
- **M5 — viz + FastAPI** (`server.py`, `web/`): force-graph SPA (glass UI like Atlas) + `/echo/*`
  API + Obsidian vault export. `echo serve` on :8771.
- **M6 — watcher + Atlas bridge** (`watch.py`): launchd poll, auto-classify, Atlas "Music" tab
  reading ECHO (mirror `~/Atlas/engine/atlas/meetings.py`), `source='echo'` todos into Atlas.

## Conventions (match Atlas/Vein — non-negotiable)

- SQLite single-writer, WAL; JSON blobs for feature/embedding vectors; `created_at`/`updated_at`/
  `source`/`status` columns; full schema up front, additive only (no migrations).
- `from __future__ import annotations`; type hints + docstrings; functions over classes.
- asyncio.Queue event bus (`bus.py`) for live updates. Bind 127.0.0.1 only.
- Reference files to copy patterns from: `~/Atlas/engine/atlas/{store,server,bus,meetings,claude_bridge}.py`.

## Gotchas (will bite you)

- **Essentia pin**: `essentia-tensorflow==2.1b6.dev1389` is the ONLY cp312 arm64 wheel. Newer =
  cp314-only; can't use 3.14 (librosa/numba lags). Don't bump without checking wheels.
- **numba floor** `>=0.61` in the audio extra prevents a resolver backtrack to an ancient numba.
- **Librosa 0.11**: tempo is `librosa.feature.tempo`, not `librosa.feature.rhythm.tempo`.
- **Enrichment rate limits**: MusicBrainz 50/min (UA required), AcousticBrainz ~10/10s. Delays
  are in `enrich.py`; don't parallelize these or you'll get throttled/banned.
- **AcousticBrainz coverage is per-MBID and partial** — treat as bonus, not baseline.
- **Web Playback SDK can't give audio samples** (EME/DRM) — it's only the M5 playback surface,
  never an analysis source. Analysis audio = ytdlp / preview / loopback.
- **Essentia mood heads**: class index 0 is the positive label ("happy", "danceable").

## Verification

- Imports: `uv run python -c "from echo import audio,features,models,ingest,spotify,store,bus,config,cli,enrich; print('OK')"`
- Enrichment (keyless, no creds): run `enrich.enrich_track({...isrc,title,artist...})` — expect
  an MBID + AcousticBrainz classifiers + Deezer BPM for a well-known track.
- Ingest one track: insert a track via `Store.upsert_track`, `ingest.ingest_one(store, track)` —
  expect `features.status='analyzed'`, 1280-d effnet_emb.
- Full flow: `echo sync → enrich → ingest → status`.
- Use a scratch DB via `ECHO_DB=/tmp/echo_test.db` so you never dirty the real one.

## Key references

- Plan: `~/.claude/plans/wobbly-marinating-lake.md`
- Requirements: `REQUIREMENTS.md` · Startup: `docs/STARTUP.md` · Enrichment research: `docs/ENRICHMENT.md`
- Jarvis port: `docs/JARVIS.md` · Quick runbook: `TOMORROW.md`
- Atlas (integration target + convention source): `~/Atlas/engine/atlas/`
