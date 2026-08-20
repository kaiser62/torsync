#!/usr/bin/env python3
"""Prove a cross-seed candidate by hashing real bytes against its piece hashes.

Comparing file lengths only shows the *shape* matches. This reads the actual
data off disk, assembles the byte stream in the torrent's file order, and checks
SHA-1 piece hashes - the same thing a client's recheck does.

Sampling first/middle/last pieces is enough to catch misalignment or different
content; --full hashes everything.

    python3 scripts/verify-data.py tl-matches2.json
    python3 scripts/verify-data.py tl-matches2.json --full
"""
import argparse
import hashlib
import importlib.util
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("scanqbt", os.path.join(HERE, "scan-qbt.py"))
scanqbt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanqbt)


def files_of(meta):
    info = meta[b"info"]
    if b"files" not in info:
        return [(scanqbt.text(info[b"name"]), info[b"length"])]
    return [("/".join(scanqbt.text(p) for p in f[b"path"]), f[b"length"])
            for f in info[b"files"]]


def resolve(save_path, root, rel):
    """Locate a torrent file on disk, allowing for a renamed top folder."""
    base = save_path.replace("/", os.sep)
    for candidate in (os.path.join(base, root, *rel.split("/")),
                      os.path.join(base, *rel.split("/"))):
        if os.path.exists(candidate):
            return candidate
    return None


class Stream:
    """Read the torrent's files as one contiguous byte stream."""

    def __init__(self, entries):
        self.entries = entries          # [(abs path or None, length)]
        self.total = sum(e[1] for e in entries)

    def read(self, offset, length):
        out = bytearray()
        pos = 0
        for path, size in self.entries:
            if pos + size <= offset:
                pos += size
                continue
            if len(out) >= length:
                break
            start = max(0, offset - pos)
            want = min(size - start, length - len(out))
            if path is None:
                return None                     # missing file, cannot verify
            with open(path, "rb") as fh:
                fh.seek(start)
                chunk = fh.read(want)
            if len(chunk) != want:
                return None
            out += chunk
            pos += size
        return bytes(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results")
    ap.add_argument("--full", action="store_true", help="hash every piece")
    ap.add_argument("--samples", type=int, default=12)
    args = ap.parse_args()

    data = json.load(open(args.results, encoding="utf-8"))

    local_by_hash = {}
    for entry in sorted(os.listdir(scanqbt.DEFAULT_BT_BACKUP)):
        if entry.endswith(".torrent"):
            path = os.path.join(scanqbt.DEFAULT_BT_BACKUP, entry)
            try:
                local_by_hash[scanqbt.parse(path)["infohash"]] = path
            except Exception:
                pass
    for r in data:
        for cand in r["matches"] + r["near"]:
            print(f'\n=== {r["name"][:62]}')
            print(f'    vs {cand["title"][:62]}')
            try:
                raw = urllib.request.urlopen(urllib.request.Request(
                    cand["downloadUrl"], headers={"User-Agent": "torsync"}), timeout=120).read()
                meta, _ = scanqbt.decode(raw)
            except Exception as exc:
                print(f"    fetch failed: {exc}")
                continue

            info = meta[b"info"]
            root = scanqbt.text(info[b"name"])
            piece_len = info[b"piece length"]
            pieces = info[b"pieces"]
            n_pieces = len(pieces) // 20

            remote_files = files_of(meta)

            # Trackers rename folders and files freely. When the ordered length
            # sequences agree the streams are positionally identical, so map
            # remote file i onto local file i and read through our own paths.
            local_path = local_by_hash.get(r["infohash"])
            local_files = []
            if local_path:
                with open(local_path, "rb") as fh:
                    local_meta, _ = scanqbt.decode(fh.read())
                local_root = scanqbt.text(local_meta[b"info"][b"name"])
                local_files = files_of(local_meta)

            entries, missing = [], 0
            aligned = (len(local_files) == len(remote_files)
                       and [n for _, n in local_files] == [n for _, n in remote_files])
            source = local_files if aligned else remote_files
            src_root = local_root if aligned else root
            for rel, size in source:
                path = resolve(r["save_path"], src_root, rel)
                if path is None or os.path.getsize(path) != size:
                    path = None
                    missing += 1
                entries.append((path, size))

            if aligned:
                print("    mapping    : by position (remote renames files)")
            if missing:
                print(f"    {missing}/{len(entries)} files not found on disk at "
                      f'{r["save_path"]}')

            stream = Stream(entries)
            if args.full:
                indices = range(n_pieces)
            else:
                step = max(1, n_pieces // args.samples)
                indices = sorted({0, n_pieces - 1, *range(0, n_pieces, step)})

            checked = ok = unreadable = 0
            for i in indices:
                offset = i * piece_len
                want = min(piece_len, stream.total - offset)
                blob = stream.read(offset, want)
                if blob is None:
                    unreadable += 1
                    continue
                checked += 1
                if hashlib.sha1(blob).digest() == pieces[i * 20:(i + 1) * 20]:
                    ok += 1

            if unreadable:
                print(f"    unreadable : {unreadable} sampled pieces sit in missing files")
            if checked == 0:
                print(f"    VERDICT: cannot verify - data not readable at this path")
            elif ok == checked:
                scope = "all" if args.full else f"{checked} sampled"
                print(f"    pieces     : {ok}/{checked} verified ({scope} of {n_pieces})")
                print(f"    VERDICT: DATA CONFIRMED - real bytes hash correctly")
            else:
                print(f"    pieces     : {ok}/{checked} verified of {n_pieces}")
                print(f"    VERDICT: MISMATCH - data differs")


if __name__ == "__main__":
    main()
