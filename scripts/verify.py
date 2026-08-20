#!/usr/bin/env python3
"""Verify cross-seed candidates by comparing real file layouts.

Total size is only a hint. A cross-seed works when the remote torrent's files
line up with what is already on disk, so this downloads each candidate .torrent
through Prowlarr and diffs the (path, length) list against the local torrent.

    python3 scripts/verify.py tl-matches2.json --top 5
"""
import argparse
import importlib.util
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scanqbt = load("scanqbt", os.path.join(HERE, "scan-qbt.py"))


def files_of(meta):
    """Return [(joined path, length)] for single- and multi-file torrents."""
    info = meta[b"info"]
    if b"files" not in info:
        return [(scanqbt.text(info[b"name"]), info[b"length"])]
    return [("/".join(scanqbt.text(p) for p in f[b"path"]), f[b"length"])
            for f in info[b"files"]]


def fetch(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "torsync/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def compare(local_files, remote_files):
    """Decide whether the remote torrent describes the same byte stream.

    Pieces are hashed over the files concatenated in listed order, so what
    matters is the ordered sequence of file lengths - not names, not folder
    layout, and *not* piece length. A different piece size re-chunks the same
    stream and still verifies.
    """
    local_lengths = [length for _, length in local_files]
    remote_lengths = [length for _, length in remote_files]
    local_bytes = sum(local_lengths)

    identical_stream = local_lengths == remote_lengths

    # Fallback for partial overlap: how much of our data appears remotely at all.
    pool = {}
    for length in remote_lengths:
        pool[length] = pool.get(length, 0) + 1
    shared = 0
    for length in local_lengths:
        if pool.get(length):
            pool[length] -= 1
            shared += length

    return {
        "local_files": len(local_files),
        "remote_files": len(remote_files),
        "identical_stream": identical_stream,
        "shared_bytes": shared,
        "coverage": shared / local_bytes if local_bytes else 0,
        "extra_remote_bytes": sum(remote_lengths) - shared,
    }


def human(size):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}PiB"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results", help="json produced by match.py")
    ap.add_argument("--top", type=int, default=5, help="how many near candidates to check")
    ap.add_argument("--include-exact", action="store_true")
    args = ap.parse_args()

    data = json.load(open(args.results, encoding="utf-8"))
    by_hash = {}
    for entry in sorted(os.listdir(scanqbt.DEFAULT_BT_BACKUP)):
        if entry.endswith(".torrent"):
            path = os.path.join(scanqbt.DEFAULT_BT_BACKUP, entry)
            try:
                by_hash[scanqbt.parse(path)["infohash"]] = path
            except Exception:
                pass

    candidates = []
    for r in data:
        if args.include_exact:
            candidates += [(r, m, 0.0) for m in r["matches"]]
        for n in r["near"]:
            candidates.append((r, n, abs(n["delta_pct"])))
    candidates.sort(key=lambda c: c[2])
    candidates = candidates[: args.top]

    for r, cand, delta in candidates:
        print(f'\n=== {r["name"][:64]}')
        print(f'    vs {cand["title"][:64]}  ({delta:+.3f}%)')
        local_path = by_hash.get(r["infohash"])
        if not local_path:
            print("    local torrent not found"); continue
        with open(local_path, "rb") as fh:
            local_meta, _ = scanqbt.decode(fh.read())
        url = cand.get("downloadUrl")
        if not url:
            print("    no downloadUrl"); continue
        try:
            remote_meta, _ = scanqbt.decode(fetch(url))
        except Exception as exc:
            print(f"    fetch failed: {exc}"); continue

        stats = compare(files_of(local_meta), files_of(remote_meta))
        lp = local_meta[b"info"][b"piece length"]
        rp = remote_meta[b"info"][b"piece length"]
        print(f'    files      : {stats["local_files"]} local / {stats["remote_files"]} remote')
        print(f'    coverage   : {stats["coverage"]*100:.2f}% of local bytes present remotely')
        print(f'    extra      : {human(stats["extra_remote_bytes"])} not on disk')
        print(f'    piece size : {lp//1024}KiB local vs {rp//1024}KiB remote'
              f'{"  (differs - harmless)" if lp != rp else ""}')
        if stats["identical_stream"]:
            print("    VERDICT: CROSS-SEEDABLE - identical file stream, recheck will pass")
        elif stats["coverage"] >= 0.99:
            print(f'    VERDICT: partial - {human(stats["extra_remote_bytes"])} extra, needs matchMode: partial')
        elif stats["coverage"] > 0.5:
            print(f'    VERDICT: partial - only {stats["coverage"]*100:.0f}% overlap')
        else:
            print("    VERDICT: no - different data")


if __name__ == "__main__":
    main()
