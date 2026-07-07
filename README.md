# ECHO — Emotive Cognitive Harmonic Organization

A personalized knowledge graph of your musical taste. Songs connect by learned
*vibe* similarity — how they feel to **you** — rather than by genre. Think "Obsidian
vault for music."

Sibling app to Atlas/Vein; same stack (Python 3.12, FastAPI, SQLite single-writer,
in-process event bus), so it wires into the Atlas dashboard the same way Atlas reads
Vein — read-only, no coupling.

## Why local audio analysis

Spotify deprecated its `audio-features` / `audio-analysis` / `recommendations`
endpoints (403 for apps created after Nov 2024, more removed Feb 2026). So ECHO uses
Spotify **only** for library sync + metadata + playlist writing, and does all acoustic
analysis locally with **Librosa** (DSP) and **Essentia** (ML embeddings + mood/key/BPM).
Audio is sourced per-track via **yt-dlp** for personal analysis.

## Pipeline

```
Spotify Liked Songs ──► sync ──► yt-dlp fetch ──► Librosa + Essentia ──► echo.db
                                                    (features + embeddings)
        ┌───────────────────────────────────────────────────┘
        ▼
   pitch vibe pairs → you judge → learn feature weights → cluster → graph + playlists
```

## Quick start

See **[TOMORROW.md](TOMORROW.md)** for the step-by-step runbook. In short:

```bash
uv sync --extra audio        # install (done)
cp .env.example .env         # add Spotify credentials
uv run echo init             # one-time Spotify auth
uv run echo backfill         # sync + fetch + analyze the whole library (overnight)
uv run echo status           # progress
```

## Commands

| Command | What it does |
|---|---|
| `echo init` | One-time Spotify OAuth consent |
| `echo probe` | Check if this app retains `audio-features` / `preview_url` access |
| `echo sync` | Pull Liked Songs metadata into `echo.db` (incremental) |
| `echo enrich` | Fuse open feature DBs (MusicBrainz/AcousticBrainz/Deezer/Last.fm) by ISRC |
| `echo models` | Download Essentia pretrained models (idempotent) |
| `echo ingest` | Fetch audio + extract features for pending tracks (resumable) |
| `echo backfill` | One-shot: models + sync + ingest (the overnight run) |
| `echo status` | Library / pipeline counts |

Later milestones add `echo pairs`, `echo train`, `echo graph`, `echo serve`, `echo watch`.

## Audio sources & enrichment

Audio for analysis is pluggable (`ECHO_AUDIO_SOURCE=ytdlp|preview|loopback`) — see
[docs/STARTUP.md](docs/STARTUP.md). Beyond local Librosa/Essentia extraction, ECHO fuses
precomputed features from open databases (AcousticBrainz, Deezer, MusicBrainz, Last.fm) for
coverage + cross-validation — see [docs/ENRICHMENT.md](docs/ENRICHMENT.md). Porting to another
machine for loopback capture: [docs/JARVIS.md](docs/JARVIS.md).

Docs: [REQUIREMENTS.md](REQUIREMENTS.md) · [docs/STARTUP.md](docs/STARTUP.md) · [HANDOFF.md](HANDOFF.md) (for a new agent).

## Layout

```
engine/echo/
  config.py    paths, ports, Spotify creds, .env autoload
  store.py     SQLite schema (full, up front) + data access
  bus.py       in-process event pub/sub (WebSocket fan-out)
  spotify.py   spotipy OAuth, Liked Songs sync, playlist writing
  models.py    Essentia model registry + downloader
  audio.py     yt-dlp fetch + ffmpeg/librosa decode
  features.py  Librosa DSP + Essentia embeddings/mood/key/BPM
  ingest.py    resumable fetch→analyze→persist orchestrator
  cli.py       `echo` entrypoint
```

Data lives in `~/Library/Application Support/ECHO/` (DB, audio cache, models, logs).
