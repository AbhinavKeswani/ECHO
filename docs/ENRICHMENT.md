# ECHO enrichment — external feature databases in confluence

Research into what's still available in 2026 to get audio qualities *besides* our own
Librosa/Essentia extraction, and how ECHO fuses them into a more robust vibe graph.
Everything below is **verified live** against the actual APIs (2026-07).

## The core idea

Spotify's audio-features endpoint is dead, but the open-music ecosystem still exposes a
lot — and it's all joinable by the **ISRC** we already capture from Spotify. So each song
gets a *multi-view* feature set instead of one, and the graph draws on whichever views
exist for a given track:

```
Spotify ISRC ──► MusicBrainz ──► recording MBID(s)
                                     │
                                     ├──► AcousticBrainz ──► Essentia high-level
                                     │      (mood / genre / danceability — SAME method as ours)
ISRC ────────────────────────────────┼──► Deezer ──► BPM + gain + 30s preview MP3
artist+title ────────────────────────┴──► Last.fm ──► crowd mood/genre tags (semantic)
```

Local extraction (EffNet/MusiCNN embeddings + Librosa DSP) stays the backbone. Enrichment
**fills coverage** where audio fetch fails and **cross-validates** where both exist.

## The sources (verified)

| Source | Gives us | Join key | Access | Status |
|---|---|---|---|---|
| **AcousticBrainz** | Essentia **high-level**: mood_{happy,sad,aggressive,relaxed,acoustic,electronic,party}, danceability, 4 genre taxonomies, timbre, voice/instrumental, tonal/atonal | MBID | keyless, read-only (frozen 2022 dump, ~7.5M recordings) | ✅ 18 classifiers/track live |
| **MusicBrainz** | ISRC→MBID resolution (the join backbone) + canonical metadata | ISRC / artist+title | keyless, 50 req/min, UA required | ✅ live |
| **Deezer** | BPM, track gain (loudness proxy), **30s preview MP3** | ISRC / artist+title | keyless | ✅ live, previews playable |
| **Last.fm** | crowd **tags** (mood/genre/era) — a semantic view orthogonal to acoustics | artist+title / MBID | free API key | ⚠️ needs key (optional) |
| ReccoBeats | Spotify-compatible danceability/energy/valence + upload-based extraction | upload / id | keyless | ⚠️ reliability reported spotty; optional fallback |

### Why AcousticBrainz is the standout

It stores the **exact same Essentia high-level descriptors ECHO computes locally** — so
merging them is true confluence, not apples-to-oranges. Coverage is *per-MBID* (a recording
has several MBIDs; we try the candidates and take the first with data), so it's opportunistic,
not total — but where present it's a free, methodology-matched second opinion. Verified: Nirvana
"Smells Like Teen Spirit" → aggressive/party/roc/not-danceable; Queen "Bohemian Rhapsody" →
acoustic/relaxed/sad/rhy. Both sensible.

### Why Deezer matters twice

1. **Metadata**: BPM + gain, a keyless cross-check on our computed tempo/loudness.
2. **Audio source**: its 30s preview MP3 is a clean, keyless, legitimate alternative to
   yt-dlp. Already wired into ECHO's `preview` backend as a fallback when Spotify has no
   preview — verified end-to-end (fetch → decode → 1280-d embedding, ~7s/track).

## How this feeds the "connection diagram"

The graph becomes **multi-modal** — nodes carry several feature views, and edges can be
drawn from more than one:

- **Acoustic-similarity edges** — learned metric over our EffNet embeddings (primary).
- **High-level agreement** — AcousticBrainz mood/genre concordance reinforces or tempers
  an acoustic edge (two songs the metric links *and* AB agrees are both "aggressive/party"
  = a stronger edge).
- **Semantic/tag edges** — Last.fm tag overlap adds a "how people describe it" layer that
  pure audio can't capture (e.g. "summer", "nostA lgic", "workout").
- **Coverage fill** — a track whose audio we couldn't fetch can still land in the graph on
  AB high-level + Deezer BPM + Last.fm tags alone.

Cross-source **agreement raises edge confidence; disagreement flags a track for review** —
exactly the robustness the confluence buys us.

## Operational notes

- Enrichment is **API-only (no audio download)**, so `echo enrich` blankets the whole
  library in ~minutes-to-an-hour (rate-limited by MusicBrainz's 50/min), *independent of*
  and much faster than the heavy local ingest. Run it first for immediate coverage.
- Rate limits enforced in `enrich.py`: MusicBrainz ~1.1s/call, AcousticBrainz ~1.1s/call.
- Coverage is partial by nature (especially AB). That's fine — it's additive; local
  extraction is the guaranteed floor.
- Implementation: `engine/echo/enrich.py`, `enrichment` table, `echo enrich` command.
