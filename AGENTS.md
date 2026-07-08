# AGENTS.md — operating ECHO

Guide for AI agents (and humans) running ECHO. For the build roadmap and deep
conventions see [HANDOFF.md](HANDOFF.md); for requirements see [REQUIREMENTS.md](REQUIREMENTS.md).

## ECHO runs standalone — Atlas is optional

ECHO's core is fully self-contained: **library sync → audio analysis → enrichment →
vibe model → graph → playlists** all run with no Atlas and no other local apps present.
Nothing in `engine/echo/` imports Atlas. The Atlas integration is an **optional M6 add-on**
(off by default, `ECHO_ENABLE_ATLAS=0`) whose *only* job is surfacing ECHO's suggestions
inside the Atlas dashboard. If a machine doesn't have Atlas (e.g. a storage-limited box),
do nothing special — just run ECHO.

**Minimal standalone install** (no Atlas, no extra ecosystem):
```bash
git clone https://github.com/AbhinavKeswani/ECHO ~/ECHO && cd ~/ECHO
brew install ffmpeg
uv sync --extra audio     # ECHO + Librosa/Essentia only; pulls in nothing Atlas-related
uv run echo models        # ~1 GB of Essentia models
```
Then `echo init → probe → sync → enrich → ingest` (see [docs/STARTUP.md](docs/STARTUP.md)).

> Tight on storage? The **enrichment** path (`echo enrich`) needs no models and no audio
> extra — it pulls features from open databases (AcousticBrainz/Deezer/MusicBrainz) over
> the network. Installing without `--extra audio` gives a tiny footprint that can still
> populate a lot of the graph; add the audio stack later when you want local embeddings.

## Using a second device as a compute node (sync over git)

The heavy work is local audio analysis (Essentia/Librosa). You can offload it to a spare
"compute" device and sync the results back through git — no shared filesystem needed.

The unit of exchange is a **snapshot**: `echo export` writes the whole library (tracks +
features + embeddings + enrichment + your pair labels) to one gzipped-JSON file; `echo
import` merges it into another device's `echo.db`. Merge is keyed by Spotify id and is
**non-destructive** — analyzed features and labeled pairs win, so the compute node's
analysis and your labels both survive a round trip in either direction.

### Workflow

```
┌─ Main device (has Spotify creds) ─┐        ┌─ Compute node (spare box) ─┐
│ echo sync            # pull likes  │        │                            │
│ echo export --out … ───────────────┼─ git ─►│ echo import …              │
│                                    │  push  │ echo enrich   # APIs       │
│                                    │  /pull │ echo ingest   # heavy!     │
│ echo import …        ◄─────────────┼─ git ──┤ echo export --out …        │
│ echo graph / serve   # use it      │        │                            │
└────────────────────────────────────┘        └────────────────────────────┘
```

1. **Main**: `echo sync` then `echo export --out <snapshot>`; commit + push (see privacy below).
2. **Compute node**: pull; `echo import <snapshot>`; run the heavy `echo enrich` + `echo ingest`
   (resumable — safe to stop/restart); `echo export --out <snapshot>`; commit + push.
3. **Main**: pull; `echo import <snapshot>` to absorb the analysis; use `echo graph` / `serve`.

Only step 2's `ingest` needs the audio stack + models; the main device can stay light and
just consume the imported features.

### PRIVACY — do not sync your library through the public code repo

A snapshot contains your **actual liked-songs library and its analysis** — personal data.
The public ECHO code repo must not hold it. ECHO's `.gitignore` already excludes
`*.snapshot.json.gz` and `data/` so it can't leak by accident. To sync it, use one of:

- **A separate PRIVATE repo** (recommended): `gh repo create ECHO-library --private`, keep a
  checkout on each device, `echo export --out ECHO-library/lib.snapshot.json.gz`, commit/push there.
- **A private branch / private fork** of ECHO dedicated to data (force-add the file there only).

Never `git add -f` a snapshot into the public repo.

## Command reference

`init` · `probe` · `sync` · `enrich` · `models` · `ingest` · `backfill` · `export` · `import` · `status`.
Run `uv run echo <cmd> --help`. Full descriptions in [README.md](README.md) / [docs/STARTUP.md](docs/STARTUP.md).

## Agent etiquette

- Long jobs (`ingest`, `backfill`) are resumable — run in the background and poll `echo status`;
  a stall is never fatal, just re-run.
- Use a scratch DB (`ECHO_DB=/tmp/echo_test.db`) for experiments; never dirty the real one.
- Don't bump the `essentia-tensorflow` pin or parallelize the rate-limited enrichment APIs
  (see [HANDOFF.md](HANDOFF.md) → Gotchas).
- Respect the privacy rule above whenever you touch snapshots or git.
