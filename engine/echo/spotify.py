"""Spotify Web API client — library sync, metadata, and playlist writing.

IMPORTANT (verified 2026): Spotify's audio-features / audio-analysis / recommendations
endpoints are deprecated and return 403 for apps created after Nov 2024. ECHO therefore
uses Spotify ONLY for what still works: reading Liked Songs + track metadata, and
creating/appending playlists. All acoustic analysis happens locally (Librosa/Essentia).

Auth is the standard spotipy Authorization-Code flow; the token is cached under the
app-support dir so the browser consent in `echo init` runs exactly once.
"""

from __future__ import annotations

import logging

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from . import config

log = logging.getLogger("echo.spotify")


class SpotifyError(RuntimeError):
    pass


def _oauth() -> SpotifyOAuth:
    if not (config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET):
        raise SpotifyError(
            "Spotify credentials missing. Set ECHO_SPOTIFY_CLIENT_ID and "
            "ECHO_SPOTIFY_CLIENT_SECRET (register an app at "
            "https://developer.spotify.com/dashboard and add the redirect URI "
            f"{config.SPOTIFY_REDIRECT_URI})."
        )
    config.ensure_dirs()
    return SpotifyOAuth(
        client_id=config.SPOTIFY_CLIENT_ID,
        client_secret=config.SPOTIFY_CLIENT_SECRET,
        redirect_uri=config.SPOTIFY_REDIRECT_URI,
        scope=config.SPOTIFY_SCOPE,
        cache_path=str(config.SPOTIFY_TOKEN_CACHE),
        open_browser=True,
    )


def client() -> spotipy.Spotify:
    """An authenticated Spotify client. Triggers the browser consent flow if needed."""
    return spotipy.Spotify(auth_manager=_oauth(), requests_timeout=15, retries=3)


def has_token() -> bool:
    """True if a cached token exists (so `echo init` can report auth status)."""
    return config.SPOTIFY_TOKEN_CACHE.exists()


def current_user(sp: spotipy.Spotify | None = None) -> dict:
    sp = sp or client()
    return sp.current_user()


def _flatten_track(item: dict) -> dict | None:
    """Map a Spotify saved-track item to ECHO's track dict. None if unplayable/local."""
    tr = item.get("track") or {}
    if not tr.get("id"):  # local files / unavailable tracks have no id
        return None
    artists = [a["name"] for a in tr.get("artists", []) if a.get("name")]
    added_at = item.get("added_at")
    # added_at is ISO 8601 (e.g. '2024-05-01T12:00:00Z'); convert to epoch.
    added_epoch = None
    if added_at:
        import datetime as _dt

        added_epoch = _dt.datetime.fromisoformat(added_at.replace("Z", "+00:00")).timestamp()
    return {
        "spotify_id": tr["id"],
        "title": tr.get("name", ""),
        "artist": artists[0] if artists else "",
        "artists": artists,
        "album": (tr.get("album") or {}).get("name"),
        "duration_ms": tr.get("duration_ms"),
        "isrc": (tr.get("external_ids") or {}).get("isrc"),
        "popularity": tr.get("popularity"),
        "spotify_url": (tr.get("external_urls") or {}).get("spotify"),
        "preview_url": tr.get("preview_url"),
        "added_at": added_epoch,
    }


def iter_liked_tracks(sp: spotipy.Spotify | None = None, page: int = 50):
    """Yield every Liked Song as an ECHO track dict, newest first (Spotify's order)."""
    sp = sp or client()
    offset = 0
    while True:
        resp = sp.current_user_saved_tracks(limit=page, offset=offset)
        items = resp.get("items", [])
        if not items:
            break
        for item in items:
            t = _flatten_track(item)
            if t:
                yield t
        if resp.get("next") is None:
            break
        offset += page


def probe_access(sp: spotipy.Spotify | None = None, sample: int = 50) -> dict:
    """Check whether THIS app retains the legacy audio data ECHO could use.

    Two independent checks, since an Extended-Quota-Mode app may keep one or both:
      * audio_features — the deprecated GET /audio-features endpoint (energy, valence…).
      * preview_url    — 30s snippets on track objects (would let us analyze the
                         official preview instead of fetching audio via yt-dlp).

    Returns a report dict; never raises (each check is caught and reported).
    """
    sp = sp or client()
    report: dict = {"audio_features": None, "preview_url": None}

    # --- audio-features (probe with a well-known public track id) ---
    test_id = "11dFghVXANLlKbjvxF9RS7"  # Daft Punk — Get Lucky
    try:
        af = sp.audio_features([test_id])
        ok = bool(af and af[0])
        report["audio_features"] = {
            "available": ok,
            "detail": "endpoint returned features" if ok else "endpoint returned empty",
        }
    except Exception as e:  # spotipy raises SpotifyException(403/404) when deprecated
        report["audio_features"] = {"available": False, "detail": f"{type(e).__name__}: {e}"}

    # --- preview_url (sample the user's own liked tracks) ---
    try:
        resp = sp.current_user_saved_tracks(limit=min(sample, 50))
        items = resp.get("items", [])
        with_preview = sum(1 for it in items if (it.get("track") or {}).get("preview_url"))
        n = len(items)
        report["preview_url"] = {
            "available": with_preview > 0,
            "with_preview": with_preview,
            "sampled": n,
            "detail": f"{with_preview}/{n} sampled liked tracks have a preview_url",
        }
    except Exception as e:
        report["preview_url"] = {"available": False, "detail": f"{type(e).__name__}: {e}"}

    return report


def create_playlist(name: str, track_uris: list[str], public: bool = False,
                    description: str = "", sp: spotipy.Spotify | None = None) -> dict:
    """Create a playlist in the user's account and add the given track URIs."""
    sp = sp or client()
    uid = sp.current_user()["id"]
    pl = sp.user_playlist_create(uid, name, public=public, description=description)
    # Spotify caps adds at 100 URIs per call.
    for i in range(0, len(track_uris), 100):
        sp.playlist_add_items(pl["id"], track_uris[i : i + 100])
    return pl
