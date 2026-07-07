# ECHO — Runbook for tomorrow

Everything is installed, verified, and ready. The **only** thing left is Spotify
credentials, then kicking off the backfill. Three steps.

## 1. Add Spotify credentials (~3 min)

```bash
cd ~/ECHO
cp .env.example .env
```

Then open `.env` and fill in `ECHO_SPOTIFY_CLIENT_ID` and `ECHO_SPOTIFY_CLIENT_SECRET`:

- Create an app at https://developer.spotify.com/dashboard
- In its settings, add the Redirect URI **exactly**: `http://127.0.0.1:8899/callback`
- Paste the Client ID / Secret into `.env`

## 2. Authenticate (one-time browser consent)

```bash
uv run echo init
```

A browser opens; approve access. The token is cached — you won't be asked again.
Expected output: `✓ Connected to Spotify as <you>`.

## 2b. Check what your app can access (10 sec)

If you're using an **older Spotify app** that's in Extended Quota Mode, it may still
have the legacy `audio-features` endpoint and/or `preview_url` snippets that newer apps
lost. This decides whether we even need yt-dlp:

```bash
uv run echo probe
```

- If **preview_url works** → we can analyze Spotify's official 30s previews instead of
  pulling from YouTube (cleaner, faster, no wrong-track matches). Tell me and I'll add
  the preview-based ingest path before you run the backfill.
- If **audio-features works** → Spotify's energy/valence/etc can be folded in as an
  extra signal on top of the local analysis.
- If neither → the default yt-dlp path (already built) is used. Just proceed.

## 3. Run the backfill (the long part — leave it running)

```bash
uv run echo backfill
```

This does, in order: download Essentia models (already done — skipped), sync your
Liked Songs into `echo.db`, then fetch + analyze every track. At ~15 s/track a
~3k-song library is roughly **12–15 hours**, so start it and let it run overnight.

It is **resumable**: if it stops, just run `uv run echo backfill` again and it
continues from where it left off (only pending/failed tracks are retried).

Check progress any time from another terminal:

```bash
uv run echo status
```

You'll see e.g. `Features: analyzed=812, pending=2103, failed=14`.

---

## What's already done (verified tonight)

- Python 3.12 env + all deps installed (`uv sync --extra audio`), including the
  tricky `essentia-tensorflow` arm64 build (pinned to the one cp312 wheel that exists).
- `ffmpeg` installed (Homebrew).
- All 10 Essentia models downloaded to `~/Library/Application Support/ECHO/essentia_models`.
- Full pipeline proven end-to-end on a real track: yt-dlp → decode → 47 Librosa
  features + 1280-d EffNet embedding + 200-d MusiCNN embedding + BPM/key/loudness +
  8 mood scores, in ~14 s/track.

## What comes after the backfill (next dev sessions)

- **M2** — ECHO pitches candidate vibe pairs; you confirm/reject; it learns your
  personal feature weights (`echo pairs`, `echo train`).
- **M3** — cluster into named vibe groups, build the graph.
- **M4/M5** — MCP tools ("find songs that feel like…", auto-playlists) + the
  interactive graph view.
- **M6** — auto-ingest new Liked Songs + wire insights into Atlas.

## Troubleshooting

- **`✗ Spotify credentials missing`** — `.env` isn't filled in or you're not in `~/ECHO`.
- **Auth browser didn't open** — copy the URL it prints into a browser manually.
- **A track fails** — normal (yt-dlp can't find some tracks); it's logged and skipped.
  Failed tracks are retried on the next `backfill`/`ingest` run.
