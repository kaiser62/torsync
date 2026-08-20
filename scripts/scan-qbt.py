#!/usr/bin/env python3
"""Inventory the local qBittorrent torrents without needing the WebUI.

Reads .torrent files straight out of BT_backup and reports name, size, infohash
and current tracker host, so we know what there is to cross-seed before wiring
anything up to a tracker.

    python3 scripts/scan-qbt.py                 # table
    python3 scripts/scan-qbt.py --json          # machine-readable
    python3 scripts/scan-qbt.py --tracker torrentbd
"""
import argparse
import hashlib
import json
import os
import sys
from urllib.parse import urlparse

DEFAULT_BT_BACKUP = os.path.expandvars(r"%LOCALAPPDATA%\qBittorrent\BT_backup")


def decode(data, pos=0):
    """Minimal bencode decoder. Returns (value, next_pos)."""
    ch = data[pos:pos + 1]
    if ch == b"i":
        end = data.index(b"e", pos)
        return int(data[pos + 1:end]), end + 1
    if ch == b"l":
        pos += 1
        out = []
        while data[pos:pos + 1] != b"e":
            item, pos = decode(data, pos)
            out.append(item)
        return out, pos + 1
    if ch == b"d":
        pos += 1
        out = {}
        while data[pos:pos + 1] != b"e":
            key, pos = decode(data, pos)
            val, pos = decode(data, pos)
            out[key] = val
        return out, pos + 1
    if ch.isdigit():
        colon = data.index(b":", pos)
        length = int(data[pos:colon])
        start = colon + 1
        return data[start:start + length], start + length
    raise ValueError(f"bad bencode at byte {pos}")


def encode(obj):
    if isinstance(obj, int):
        return b"i%de" % obj
    if isinstance(obj, bytes):
        return b"%d:%s" % (len(obj), obj)
    if isinstance(obj, list):
        return b"l" + b"".join(encode(x) for x in obj) + b"e"
    if isinstance(obj, dict):
        return b"d" + b"".join(
            encode(k) + encode(v) for k, v in sorted(obj.items())
        ) + b"e"
    raise TypeError(type(obj))


def text(value):
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def hosts_of(urls):
    hosts = []
    for url in urls:
        host = urlparse(url).hostname
        # Announce URLs carry a passkey; keep only the host.
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def resume_info(path):
    """qBittorrent strips announce URLs from the .torrent and keeps the live
    tracker list, save path and category in the sibling .fastresume."""
    resume = path[: -len(".torrent")] + ".fastresume"
    if not os.path.exists(resume):
        return {}
    with open(resume, "rb") as fh:
        data, _ = decode(fh.read())
    urls = []
    for tier in data.get(b"trackers", []):
        urls.extend(text(u) for u in (tier if isinstance(tier, list) else [tier]))
    return {
        "trackers": hosts_of(urls),
        "save_path": text(data.get(b"qBt-savePath", b"")),
        "category": text(data.get(b"qBt-category", b"")),
    }


def parse(path):
    with open(path, "rb") as fh:
        meta, _ = decode(fh.read())
    info = meta[b"info"]
    files = info.get(b"files")
    if files:
        size = sum(f[b"length"] for f in files)
        count = len(files)
    else:
        size = info[b"length"]
        count = 1
    record = {
        "infohash": hashlib.sha1(encode(info)).hexdigest(),
        "name": text(info[b"name"]),
        "size": size,
        "files": count,
        "piece_length": info[b"piece length"],
        "private": bool(info.get(b"private", 0)),
        "trackers": [],
        "save_path": "",
        "category": "",
        "source": os.path.basename(path),
    }
    record.update(resume_info(path))
    if not record["trackers"]:
        urls = [text(meta[b"announce"])] if b"announce" in meta else []
        for tier in meta.get(b"announce-list", []):
            urls.extend(text(u) for u in tier)
        record["trackers"] = hosts_of(urls)
    return record


def human(size):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}PiB"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bt-backup", default=DEFAULT_BT_BACKUP)
    ap.add_argument("--tracker", help="only torrents whose tracker host contains this")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(args.bt_backup):
        sys.exit(f"no such directory: {args.bt_backup}")

    rows, failed = [], []
    for entry in sorted(os.listdir(args.bt_backup)):
        if not entry.endswith(".torrent"):
            continue
        path = os.path.join(args.bt_backup, entry)
        try:
            rows.append(parse(path))
        except Exception as exc:  # a corrupt torrent should not abort the scan
            failed.append((entry, str(exc)))

    if args.tracker:
        needle = args.tracker.lower()
        rows = [r for r in rows if any(needle in h.lower() for h in r["trackers"])]

    if args.json:
        json.dump(rows, sys.stdout, indent=2)
        print()
    else:
        rows.sort(key=lambda r: -r["size"])
        for r in rows:
            flag = "priv" if r["private"] else "pub "
            host = r["trackers"][0] if r["trackers"] else "-"
            print(f'{human(r["size"]):>9}  {r["files"]:>4}f  {flag}  {host:<28} {r["name"]}')
        print(f"\n{len(rows)} torrents, {human(sum(r['size'] for r in rows))} total")
        by_host = {}
        for r in rows:
            by_host[r["trackers"][0] if r["trackers"] else "-"] = (
                by_host.get(r["trackers"][0] if r["trackers"] else "-", 0) + 1
            )
        for host, n in sorted(by_host.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>3}  {host}")

    for entry, exc in failed:
        print(f"skipped {entry}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
