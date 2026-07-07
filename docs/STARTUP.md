# ECHO — First-time startup

The environment is already installed and verified (deps, ffmpeg, Essentia models, the
whole pipeline proven end-to-end). This is the canonical first-run guide. **TOMORROW.md**
is the 3-line quick version; this covers everything including enrichment and options.

## Prerequisites (already done on this machine)

- `uv` installed, Python 3.12 pinned.
- `uv sync --extra audio` complete (incl. the pinned Essentia arm64 wheel).
- `ffmpeg` installed.
- All 10 Essentia models downloaded (`~/Library/Application Support/ECHO/essentia_models`).

On a **fresh machine** (e.g. the jarvis device), reproduce with:
```bash
git clone <repo> ~/ECHO && cd ~/ECHO
brew install ffmpeg
uv sync --extra audio        # installs exact locked versions
uv run echo models           # download Essentia models
```

## Step 1 — Spotify credentials

```bash
cd ~/ECHO
cp .env.example .env
```
Edit `.env`:
- Create an app at https://developer.spotify.com/dashboard
- Add Redirect URI **exactly**: `http://127.0.0.1:8899/callback`
- Paste `ECHO_SPOTIFY_CLIENT_ID` and `ECHO_SPOTIFY_CLIENT_SECRET`.

## Step 2 — Authenticate (once)

```bash
uv run echo init
```
Browser opens → approve. Token cached; not asked again. Expect `✓ Connected as <you>`.

## Step 3 — Probe access (10s, decides the audio path)

```bash
uv run echo probe
```
Reports whether your app still has `audio-features` and/or `preview_url`. Guidance:
- `preview_url` works → set `ECHO_AUDIO_SOURCE=preview` in `.env` (uses Spotify's own snippets).
- neither → default `ytdlp` is fine; or `preview` still works via Deezer fallback (keyless).

## Step 4 — Sync the library

```bash
uv run echo sync
```
Pulls all Liked Songs (metadata + ISRC) into `echo.db`. Incremental on re-runs.

## Step 5 — Enrich (fast, do this before/alongside ingest)

```bash
uv run echo enrich
```
API-only sweep (MusicBrainz → AcousticBrainz + Deezer + Last.fm). No audio download, so it's
quick (rate-limited ~1 track/sec by MusicBrainz). Gives immediate multi-view coverage:
AcousticBrainz Essentia high-level moods/genres, Deezer BPM, tags. Optional Last.fm tags need
`ECHO_LASTFM_API_KEY` in `.env`.

## Step 6 — Backfill (the long part — local audio analysis)

```bash
uv run echo backfill        # = models + sync + ingest, resumable
# or just the analysis if already synced/enriched:
uv run echo ingest
```
~14s/track (yt-dlp, 120s window) or ~7s/track (preview, 30s window). A ~3k library is roughly
6–15 hours depending on source/window. **Leave it running overnight.** Resumable: re-run to
continue. Monitor from another terminal:
```bash
uv run echo status
# Library: 2847 | Features: analyzed=812, pending=2035 | Enrichment: enriched=2601 | Pairs: none
```

## Choosing an audio source

Set `ECHO_AUDIO_SOURCE` in `.env`:

| Value | Source | Speed | Quality / notes |
|---|---|---|---|
| `ytdlp` (default) | YouTube search+download | ~14s/track | Full track; gray-area; wrong-track risk. |
| `preview` | Spotify preview_url, else Deezer 30s | ~7s/track | Keyless via Deezer for any ISRC; legit; exact. |
| `loopback` | Play + system-audio capture (jarvis) | real-time | Exact master; ~1.5 days/3k at 30s window. See docs/JARVIS.md. |

## Tuning (`.env`)

- `ECHO_ANALYZE_SECONDS=120` — central window analyzed (0 = whole track, ~4x slower).
- `ECHO_KEEP_AUDIO=1` — keep downloaded audio (re-analyze without re-download; uses disk).
- `ECHO_AUDIO_SOURCE=…` — see table.
- `ECHO_LASTFM_API_KEY=…` — enable Last.fm tag enrichment.

## Data locations

`~/Library/Application Support/ECHO/`: `echo.db`, `audio_cache/`, `essentia_models/`, `logs/`,
`vault/` (Obsidian export, later). Back up = copy `echo.db`.

## Troubleshooting

- `✗ Spotify credentials missing` — `.env` not filled, or not run from `~/ECHO`.
- Auth browser didn't open — copy the printed URL manually.
- A track fails ingest — normal (yt-dlp miss / no preview); logged, retried next run.
- Enrichment misses — AcousticBrainz coverage is partial by design; not an error.
