# ECHO on jarvis — the loopback capture backend

ECHO's audio sourcing is pluggable (`ECHO_AUDIO_SOURCE=ytdlp|preview|loopback`). The
**loopback** backend is meant to run on the idle "jarvis" device: it plays each track
through Spotify and captures the system audio via a CoreAudio tap — yielding the exact
Spotify master audio, no wrong-track risk, no YouTube. This is the highest-quality
source; its only cost is that it runs in real time.

## Why it works even with speakers off

A CoreAudio tap captures the **digital** output stream before the DAC, so output volume
can be muted (or routed to a null device) and capture is unaffected. Target the
**Spotify desktop app** (reliably tappable) rather than the browser Web Playback SDK,
whose EME path is less predictable for OS-level capture.

## Time budget

We only capture ECHO's analysis window (`ECHO_ANALYZE_SECONDS`, default 120), not the
whole song. Per-track ≈ window + seek/settle overhead (~10–20 s):

| Window | Per track | 3k library |
|---|---|---|
| 30 s  | ~45 s  | ~1.5 days |
| 120 s | ~135 s | ~4.7 days |

Resumable like every ECHO backend (work is driven off `features.status`), so it can run
across multiple sessions on the idle device.

## The one function to implement

Replace the stub `_acquire_loopback(track)` in `engine/echo/audio.py`. Contract:

```
_acquire_loopback(track: dict) -> tuple[Path, str]
    # track has: spotify_id, title, artist, duration_ms, spotify_url, ...
    # 1. Ensure a Spotify playback session (Web Playback SDK device or the desktop app).
    # 2. Start playback of `spotify:track:{track['spotify_id']}`, seek to a central
    #    offset (duration_ms/2 - window/2), let it settle (~1–2 s).
    # 3. Capture ECHO_ANALYZE_SECONDS of system audio to AUDIO_CACHE/{spotify_id}.wav
    #    (44.1 kHz; features.load_mono handles resampling). Mute output first.
    # 4. Stop playback. Return (wav_path, f"loopback:{track['spotify_id']}").
    # Raise audio.AudioError on any failure — ingest marks the track failed and moves on.
```

Everything downstream (`load_mono`, `features.analyze`, persistence, resumability) is
already backend-agnostic — implementing this function is the whole port.

## Reuse from Vein

Vein already captures system audio on macOS. Lift its CoreAudio tap / aggregate-device
setup:

- `~/Vein-MacOS/engine/vein/` — the audio-capture module (CoreAudio tap → PCM frames).
  Wrap it to (a) start on demand, (b) buffer N seconds, (c) flush to a WAV.

Controlling Spotify playback: either the Web Playback SDK (create a device, then use the
Web API's `PUT /me/player/play` + `/seek` to drive it — needs Premium + `user-modify-
playback-state` scope, which must be added to `SPOTIFY_SCOPE` in config.py), or
AppleScript against the Spotify desktop app (`tell application "Spotify" to play track …`).

## Running it on jarvis

```bash
git clone <this-repo> ~/ECHO && cd ~/ECHO
uv sync --extra audio
cp .env.example .env         # same Spotify creds; add user-modify-playback-state scope
echo "ECHO_AUDIO_SOURCE=loopback" >> .env
echo "ECHO_ANALYZE_SECONDS=30" >> .env   # 30s window → ~1.5 days for 3k
uv run echo init
uv run echo backfill
```

The resulting `echo.db` can be copied back, or jarvis can just host ECHO's server.
