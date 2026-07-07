"""Runtime configuration and well-known paths for the ECHO engine.

Pattern mirrors Atlas/Vein's config.py: a per-OS app-data dir holds the SQLite DB,
logs, the downloaded-audio cache, and the exported Obsidian vault. Everything binds
to localhost — nothing leaves the box except the two intentional network calls
(Spotify Web API for library sync/playlists, and yt-dlp for audio during ingest).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_dotenv() -> None:
    """Populate os.environ from a project-root `.env` (without clobbering real env vars).

    Keeps credentials out of the shell profile: drop them in ECHO/.env once and every
    `echo` command picks them up. Minimal parser — `KEY=value`, `#` comments, no quoting
    tricks — so there's no python-dotenv dependency.
    """
    root = Path(__file__).resolve().parent.parent.parent  # ECHO/
    for env_path in (root / ".env", _app_support() / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


# --- Paths (per-OS app-data dir) ---------------------------------------------


def _app_support() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "ECHO"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ECHO"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "ECHO"


_load_dotenv()  # must run before the os.environ reads below

APP_SUPPORT = _app_support()
DB_PATH = Path(os.environ.get("ECHO_DB", str(APP_SUPPORT / "echo.db")))
LOG_DIR = APP_SUPPORT / "logs"
# Downloaded audio (yt-dlp) lives here during analysis. Kept or purged per KEEP_AUDIO.
AUDIO_CACHE = Path(os.environ.get("ECHO_AUDIO_CACHE", str(APP_SUPPORT / "audio_cache")))
# Obsidian vault export target (one note per song + per-cluster notes).
VAULT_DIR = Path(os.environ.get("ECHO_VAULT_DIR", str(APP_SUPPORT / "vault")))

# Retain the source audio after analysis? Off by default — features are all we need,
# and a full library of WAVs is large. Set ECHO_KEEP_AUDIO=1 to keep for re-analysis.
KEEP_AUDIO = os.environ.get("ECHO_KEEP_AUDIO", "0") == "1"

# Where ingest sources audio from, per track. Pluggable so ECHO can move between
# machines without touching the pipeline:
#   ytdlp    — search + download from YouTube (default; works for any app).
#   preview  — Spotify's 30s preview_url (needs a grandfathered/extended-quota app;
#              confirm with `echo probe`). Fast, legitimate, exact.
#   loopback — play the track on this device and capture system audio via a CoreAudio
#              tap (the "jarvis" backend; reuses Vein's capture). Exact master audio,
#              but real-time. See docs/JARVIS.md.
AUDIO_SOURCE = os.environ.get("ECHO_AUDIO_SOURCE", "ytdlp").strip().lower()

# Analyze only a central window of each track (seconds). A track's "vibe" is roughly
# stationary, so a central segment gives the same fingerprint as the whole song while
# cutting per-track analysis time ~4x (Librosa's CQT/tonnetz dominate and scale with
# length). Set ECHO_ANALYZE_SECONDS=0 to analyze the full track.
ANALYZE_SECONDS = int(os.environ.get("ECHO_ANALYZE_SECONDS", "120"))


def ensure_dirs() -> None:
    for d in (APP_SUPPORT, LOG_DIR, AUDIO_CACHE, VAULT_DIR):
        d.mkdir(parents=True, exist_ok=True)


# --- Server ------------------------------------------------------------------

# Vein owns 8765, Atlas owns 8770; ECHO takes 8771.
HOST = os.environ.get("ECHO_HOST", "127.0.0.1")
PORT = int(os.environ.get("ECHO_PORT", "8771"))

# --- Spotify Web API ---------------------------------------------------------
#
# Register a Spotify app at https://developer.spotify.com/dashboard, add the redirect
# URI below to it, then export the id/secret. NOTE (verified 2026): the audio-features,
# audio-analysis, and recommendations endpoints are deprecated/403 for new apps — ECHO
# uses Spotify ONLY for library sync + track metadata + playlist writing.

SPOTIFY_CLIENT_ID = os.environ.get("ECHO_SPOTIFY_CLIENT_ID", "").strip()
SPOTIFY_CLIENT_SECRET = os.environ.get("ECHO_SPOTIFY_CLIENT_SECRET", "").strip()
SPOTIFY_REDIRECT_URI = os.environ.get("ECHO_SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8899/callback")
# user-library-read: Liked Songs. playlist-modify-*: write vibe playlists back.
SPOTIFY_SCOPE = "user-library-read playlist-modify-private playlist-modify-public"
# spotipy caches the OAuth token here so `echo init` runs the browser flow just once.
SPOTIFY_TOKEN_CACHE = APP_SUPPORT / "spotify_token.json"

# --- Claude bridge (LLM cluster naming, M3) ----------------------------------

# The local `claude` CLI binary, same headless-print pattern Atlas uses. Overridable.
CLAUDE_BIN = os.environ.get("ECHO_CLAUDE_BIN", "claude")

# --- Atlas integration (M6, read/write bridge) -------------------------------
#
# ECHO owns echo.db; Atlas reads it read-only (mirroring how Atlas reads Vein).
# ECHO can also file source='echo' todos into Atlas via its local API.


def _atlas_db() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "Atlas" / "atlas.db"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Atlas" / "atlas.db"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "Atlas" / "atlas.db"


ATLAS_DB = Path(os.environ.get("ECHO_ATLAS_DB", str(_atlas_db())))
ATLAS_API = os.environ.get("ECHO_ATLAS_API", "http://127.0.0.1:8770")
