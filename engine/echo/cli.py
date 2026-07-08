"""ECHO command-line entrypoint: `echo <command>`.

M0 commands (this milestone):
    echo init      — run the Spotify OAuth consent flow once, verify the connection
    echo sync      — pull Liked Songs metadata into echo.db (incremental)
    echo status    — show library / pipeline counts

Later milestones register their own subcommands here (ingest, pairs, train,
graph, serve, watch) so `echo` stays the single front door to every process.
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import config
from .store import Store


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


# --- Commands ----------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    """Authenticate with Spotify (browser consent, cached thereafter)."""
    from . import spotify

    try:
        sp = spotify.client()
        me = sp.current_user()
    except spotify.SpotifyError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2
    except Exception as e:  # spotipy surfaces auth failures as generic exceptions
        print(f"✗ Spotify auth failed: {e}", file=sys.stderr)
        return 1
    print(f"✓ Connected to Spotify as {me.get('display_name') or me.get('id')}")
    print(f"  Token cached at {config.SPOTIFY_TOKEN_CACHE}")
    print(f"  DB at {config.DB_PATH}")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Report whether this Spotify app still has audio-features / preview_url access."""
    from . import spotify

    try:
        report = spotify.probe_access(sample=args.sample)
    except spotify.SpotifyError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2

    af = report["audio_features"] or {}
    pv = report["preview_url"] or {}
    mark = lambda ok: "✓" if ok else "✗"  # noqa: E731
    print(f"{mark(af.get('available'))} audio-features: {af.get('detail')}")
    print(f"{mark(pv.get('available'))} preview_url:    {pv.get('detail')}")
    print()
    if pv.get("available"):
        print("→ preview_url works: ECHO can analyze official 30s previews instead of yt-dlp.")
    else:
        print("→ preview_url unavailable: ECHO will source audio via yt-dlp (default path).")
    if af.get("available"):
        print("→ audio-features works: Spotify's energy/valence/etc can be folded in as extra signal.")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """Pull Liked Songs metadata into echo.db (skips already-known unless --full)."""
    from . import spotify

    store = Store()
    try:
        sp = spotify.client()
    except spotify.SpotifyError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2

    known = set() if args.full else store.known_spotify_ids()
    added = 0
    seen = 0
    for t in spotify.iter_liked_tracks(sp):
        seen += 1
        # Newest-first order: once we hit a known track on an incremental sync,
        # everything after it is already stored — stop early.
        if not args.full and t["spotify_id"] in known:
            break
        store.upsert_track(t)
        added += 1
        if added % 100 == 0:
            print(f"  … {added} new tracks")

    store.set_setting("last_sync", {"seen": seen, "added": added})
    total = store.count_tracks()
    print(f"✓ Sync complete: {added} new, {total} total liked songs in echo.db")
    fc = store.feature_counts()
    pending = fc.get("pending", 0) + fc.get("failed", 0)
    if pending:
        print(f"  {pending} awaiting audio analysis — run `echo ingest` (M1).")
    store.close()
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    """Download the Essentia pretrained models (idempotent)."""
    from . import models

    results = models.download_all(force=args.force)
    ok = sum(1 for v in results.values() if v == "ok")
    skipped = sum(1 for v in results.values() if v == "skipped")
    errors = {k: v for k, v in results.items() if v.startswith("error")}
    print(f"✓ Models: {ok} downloaded, {skipped} already present, {len(errors)} failed")
    for k, v in errors.items():
        print(f"  ✗ {k}: {v}", file=sys.stderr)
    print(f"  Stored in {models.MODELS_DIR}")
    return 1 if errors else 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Fetch audio + extract features for pending/failed tracks (resumable)."""
    from . import ingest

    try:
        summary = ingest.run(limit=args.limit)
    except RuntimeError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2
    print(
        f"✓ Ingest done: {summary['ok']} analyzed, {summary['failed']} failed "
        f"of {summary['total']} in {summary['seconds']}s"
    )
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    """One-shot overnight run: ensure models, sync library, then ingest everything."""
    from . import models

    if models.missing():
        print("→ Downloading Essentia models…")
        rc = cmd_models(argparse.Namespace(force=False))
        if rc:
            return rc
    print("→ Syncing Liked Songs…")
    rc = cmd_sync(argparse.Namespace(full=args.full))
    if rc:
        return rc
    print("→ Ingesting audio + extracting features (this is the long part)…")
    return cmd_ingest(argparse.Namespace(limit=args.limit))


def cmd_enrich(args: argparse.Namespace) -> int:
    """Fuse external feature DBs (MusicBrainz/AcousticBrainz/Deezer/Last.fm) by ISRC."""
    from . import enrich

    store = Store()
    queue = store.tracks_needing_enrichment(limit=args.limit)
    total = len(queue)
    print(f"Enriching {total} track(s) via open feature databases…")
    hits = {"ab": 0, "deezer": 0, "lastfm": 0}
    for i, track in enumerate(queue, 1):
        try:
            data = enrich.enrich_track(track)
            store.save_enrichment(track["id"], data)
            if data.get("ab_highlevel"):
                hits["ab"] += 1
            if data.get("deezer"):
                hits["deezer"] += 1
            if data.get("lastfm_tags"):
                hits["lastfm"] += 1
        except Exception as e:  # noqa: BLE001
            store.mark_enrichment_failed(track["id"], str(e))
        if i % 25 == 0 or i == total:
            print(f"  {i}/{total} — AcousticBrainz {hits['ab']}, Deezer {hits['deezer']}, Last.fm {hits['lastfm']}")
    ec = store.enrichment_counts()
    print(f"✓ Enrichment: " + (", ".join(f"{k}={v}" for k, v in sorted(ec.items())) or "none"))
    store.close()
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Export the library (tracks + features + enrichment + labels) to a syncable snapshot."""
    from pathlib import Path
    from . import snapshot

    store = Store()
    out = Path(args.out)
    summary = snapshot.write_snapshot(store, out)
    print(f"✓ Exported {summary['tracks']} tracks ({summary['analyzed']} analyzed, "
          f"{summary['pairs']} pairs) → {out}")
    print("  Sync via a PRIVATE repo/branch — this is your personal library (see docs/AGENTS.md).")
    store.close()
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """Import a snapshot from another device (merges; analyzed/labeled data wins)."""
    from pathlib import Path
    from . import snapshot

    store = Store()
    try:
        counts = snapshot.read_snapshot(store, Path(args.path))
    except (FileNotFoundError, ValueError) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2
    print(f"✓ Imported: {counts['tracks']} tracks, {counts['features']} features, "
          f"{counts['enrichment']} enrichment, {counts['pairs']} pairs merged")
    store.close()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Show library and pipeline counts."""
    store = Store()
    total = store.count_tracks()
    fc = store.feature_counts()
    ec = store.enrichment_counts()
    pc = store.pair_counts()
    print(f"Library:    {total} liked songs")
    print(f"Features:   " + (", ".join(f"{k}={v}" for k, v in sorted(fc.items())) or "none"))
    print(f"Enrichment: " + (", ".join(f"{k}={v}" for k, v in sorted(ec.items())) or "none"))
    print(f"Pairs:      " + (", ".join(f"{k}={v}" for k, v in sorted(pc.items())) or "none"))
    print(f"DB:         {config.DB_PATH}")
    store.close()
    return 0


# --- Dispatch ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="echo", description="ECHO — musical-taste vibe graph")
    sub = p.add_subparsers(dest="command", required=True)

    sp_init = sub.add_parser("init", help="authenticate with Spotify (one-time browser consent)")
    sp_init.set_defaults(func=cmd_init)

    sp_probe = sub.add_parser("probe", help="check if this app retains audio-features / preview_url access")
    sp_probe.add_argument("--sample", type=int, default=50, help="how many liked tracks to sample for preview_url")
    sp_probe.set_defaults(func=cmd_probe)

    sp_sync = sub.add_parser("sync", help="pull Liked Songs metadata into echo.db")
    sp_sync.add_argument("--full", action="store_true", help="re-scan the entire library, not just new tracks")
    sp_sync.set_defaults(func=cmd_sync)

    sp_models = sub.add_parser("models", help="download Essentia pretrained models")
    sp_models.add_argument("--force", action="store_true", help="re-download even if present")
    sp_models.set_defaults(func=cmd_models)

    sp_ingest = sub.add_parser("ingest", help="fetch audio + extract features (resumable)")
    sp_ingest.add_argument("--limit", type=int, default=None, help="process at most N tracks")
    sp_ingest.set_defaults(func=cmd_ingest)

    sp_backfill = sub.add_parser("backfill", help="one-shot: models + sync + ingest (the overnight run)")
    sp_backfill.add_argument("--full", action="store_true", help="re-scan entire library")
    sp_backfill.add_argument("--limit", type=int, default=None, help="ingest at most N tracks")
    sp_backfill.set_defaults(func=cmd_backfill)

    sp_enrich = sub.add_parser("enrich", help="fuse external feature DBs by ISRC (fast, API-only)")
    sp_enrich.add_argument("--limit", type=int, default=None, help="enrich at most N tracks")
    sp_enrich.set_defaults(func=cmd_enrich)

    sp_export = sub.add_parser("export", help="export the library to a git-syncable snapshot")
    sp_export.add_argument("--out", default="echo-library.snapshot.json.gz", help="output file path")
    sp_export.set_defaults(func=cmd_export)

    sp_import = sub.add_parser("import", help="merge a snapshot exported on another device")
    sp_import.add_argument("path", help="snapshot file to import")
    sp_import.set_defaults(func=cmd_import)

    sp_status = sub.add_parser("status", help="show library / pipeline counts")
    sp_status.set_defaults(func=cmd_status)

    return p


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
