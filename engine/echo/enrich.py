"""Enrichment layer — fuse ECHO's local analysis with open feature databases.

Spotify's features are gone, but the open-music ecosystem still exposes plenty, joinable
by the ISRC we already capture. This module resolves each track through that graph:

    Spotify ISRC ──► MusicBrainz (recording MBID) ──► AcousticBrainz (Essentia high-level)
                 └─► Deezer (BPM + 30s preview MP3)
                     Last.fm (crowd mood/genre tags — optional, needs API key)

Why it matters: AcousticBrainz stores the SAME Essentia high-level descriptors ECHO
computes, so it's true confluence — it fills coverage where local audio fetch fails and
cross-validates where both exist. Deezer's preview URL doubles as a clean, keyless audio
source. All of this is API-only (no audio download), so `echo enrich` can blanket the
whole library quickly, independent of the heavy local ingest.

All calls are best-effort and rate-limited; any single source failing never aborts a track.
"""

from __future__ import annotations

import logging
import time
import urllib.parse
import urllib.request
import json as _json

from . import config

log = logging.getLogger("echo.enrich")

# Politeness delays (per the services' documented limits).
_MB_DELAY = 1.1   # MusicBrainz: 50 req/min
_AB_DELAY = 1.1   # AcousticBrainz: ~10 req/10s


class EnrichError(RuntimeError):
    pass


def _get_json(url: str, headers: dict | None = None, timeout: float = 10.0):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _json.loads(r.read().decode("utf-8", "replace"))


# --- MusicBrainz: ISRC / name -> recording MBID(s) ---------------------------


def mb_candidate_mbids(isrc: str | None, artist: str, title: str, limit: int = 5) -> list[str]:
    """Resolve a track to candidate MusicBrainz recording MBIDs (ISRC first, then search)."""
    headers = {"User-Agent": config.USER_AGENT}
    mbids: list[str] = []
    try:
        if isrc:
            url = f"{config.MUSICBRAINZ_API}/recording?query=isrc:{urllib.parse.quote(isrc)}&fmt=json&limit={limit}"
            data = _get_json(url, headers)
            mbids = [r["id"] for r in data.get("recordings", [])]
        if not mbids:
            q = f'recording:"{title}" AND artist:"{artist}"'
            url = f"{config.MUSICBRAINZ_API}/recording?query={urllib.parse.quote(q)}&fmt=json&limit={limit}"
            data = _get_json(url, headers)
            mbids = [r["id"] for r in data.get("recordings", [])]
    except Exception as e:  # noqa: BLE001
        log.warning("musicbrainz lookup failed (%s): %s", isrc or title, e)
    finally:
        time.sleep(_MB_DELAY)
    return mbids


# --- AcousticBrainz: MBID -> Essentia high-level features --------------------


def ab_highlevel(mbids: list[str]) -> tuple[str | None, dict | None]:
    """Return (mbid, high-level features) for the first candidate MBID AB has data for.

    Flattens each classifier to {name: {value, probability}} — the Essentia high-level
    descriptors (mood_*, danceability, genre_*, timbre, voice_instrumental, ...).
    """
    for mbid in mbids:
        try:
            data = _get_json(f"{config.ACOUSTICBRAINZ_API}/{mbid}/high-level")
        except Exception as e:  # noqa: BLE001 — 404 = not in AB, just try next
            log.debug("AB miss %s: %s", mbid, e)
            time.sleep(_AB_DELAY)
            continue
        finally:
            time.sleep(_AB_DELAY)
        hl = data.get("highlevel")
        if hl:
            flat = {k: {"value": v.get("value"), "probability": v.get("probability")}
                    for k, v in hl.items()}
            return mbid, flat
    return (mbids[0] if mbids else None), None


# --- Deezer: ISRC -> BPM + preview MP3 (keyless) -----------------------------


def deezer_track(isrc: str | None, artist: str, title: str) -> dict | None:
    """Deezer metadata + preview for a track (ISRC lookup first, then search)."""
    try:
        if isrc:
            data = _get_json(f"{config.DEEZER_API}/track/isrc:{urllib.parse.quote(isrc)}")
            if data and data.get("id") and not data.get("error"):
                return _deezer_fields(data)
        q = f'artist:"{artist}" track:"{title}"'
        data = _get_json(f"{config.DEEZER_API}/search?q={urllib.parse.quote(q)}&limit=1")
        hits = data.get("data", [])
        if hits:
            # /search results are lean; refetch the full track for bpm/gain.
            full = _get_json(f"{config.DEEZER_API}/track/{hits[0]['id']}")
            return _deezer_fields(full)
    except Exception as e:  # noqa: BLE001
        log.warning("deezer lookup failed (%s): %s", isrc or title, e)
    return None


def _deezer_fields(d: dict) -> dict:
    return {
        "deezer_id": d.get("id"),
        "bpm": d.get("bpm") or None,       # Deezer returns 0 when unknown
        "gain": d.get("gain"),             # track gain (loudness proxy)
        "preview_url": d.get("preview") or None,
    }


def deezer_preview_url(isrc: str | None, artist: str, title: str) -> str | None:
    """Just the 30s preview MP3 URL — used by the `preview` audio backend as a fallback."""
    info = deezer_track(isrc, artist, title)
    return info.get("preview_url") if info else None


# --- Last.fm: crowd tags (optional; needs API key) ---------------------------


def lastfm_tags(artist: str, title: str, top: int = 10) -> list[dict] | None:
    if not config.LASTFM_API_KEY:
        return None
    try:
        params = urllib.parse.urlencode({
            "method": "track.gettoptags", "artist": artist, "track": title,
            "api_key": config.LASTFM_API_KEY, "format": "json", "autocorrect": 1,
        })
        data = _get_json(f"https://ws.audioscrobbler.com/2.0/?{params}")
        tags = (data.get("toptags") or {}).get("tag", [])
        return [{"tag": t["name"], "count": int(t.get("count", 0))} for t in tags[:top]]
    except Exception as e:  # noqa: BLE001
        log.warning("lastfm tags failed (%s - %s): %s", artist, title, e)
        return None


# --- Top-level ---------------------------------------------------------------


def enrich_track(track: dict) -> dict:
    """Resolve one track across every source. Returns the dict Store.save_enrichment wants."""
    isrc, artist, title = track.get("isrc"), track["artist"], track["title"]
    mbids = mb_candidate_mbids(isrc, artist, title)
    mbid, ab = ab_highlevel(mbids) if mbids else (None, None)
    return {
        "mbid": mbid,
        "ab_highlevel": ab,
        "deezer": deezer_track(isrc, artist, title),
        "lastfm_tags": lastfm_tags(artist, title),
    }
