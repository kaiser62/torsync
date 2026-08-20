#!/usr/bin/env python3
"""Find cross-seed candidates for local torrents on a Prowlarr indexer.

A name hit means nothing: cross-seeding only works when the remote release is
byte-identical to what is already on disk. So we search by name, then keep only
results whose total size matches exactly (or within --tolerance bytes).

    python3 scripts/match.py --indexer 1                  # every local torrent
    python3 scripts/match.py --indexer 1 --tracker torrentbd
    python3 scripts/match.py --indexer 1 --json out.json

Results are cached in .match-cache.json so re-runs are cheap; trackers rate
limit hard (TorrentLeech declares requestDelay 4.1s) and a full sweep of ~90
torrents is several minutes of deliberate waiting.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CACHE = ".match-cache.json"
# Repack/scene decorations that never appear in a tracker's own title.
NOISE = re.compile(
    r"\[(fitgirl|dodi)[^\]]*\]|\((\d{4})\)|\b(repack|multi\d+|incl|dlc|update|"
    r"v?\d+\.\d[\d.]*)\b|[\[\]()_.]", re.I)


def load_env(path=".env"):
    env = {}
    if not os.path.exists(path):
        sys.exit("missing .env")
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def search(env, indexer, query, timeout=120):
    url = env["PROWLARR_URL"].rstrip("/") + "/api/v1/search?" + urllib.parse.urlencode(
        {"query": query, "indexerIds": indexer, "type": "search", "limit": 100})
    req = urllib.request.Request(url, headers={"X-Api-Key": env["PROWLARR_API_KEY"]})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def query_for(name):
    """Reduce a local torrent name to something a tracker search will match."""
    q = NOISE.sub(" ", name)
    q = re.sub(r"\s+", " ", q).strip()
    # Long tails hurt more than they help; trackers match on leading words.
    return " ".join(q.split()[:6])


def human(size):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}PiB"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--indexer", required=True, help="Prowlarr indexer id")
    ap.add_argument("--tracker", help="only local torrents on this tracker host")
    ap.add_argument("--delay", type=float, default=4.5,
                    help="seconds between searches; keep >= the indexer's requestDelay")
    ap.add_argument("--tolerance", type=int, default=0,
                    help="bytes of size difference still considered a match")
    ap.add_argument("--limit", type=int, help="stop after N local torrents")
    ap.add_argument("--json", help="write full results here")
    ap.add_argument("--refresh", action="store_true", help="ignore the cache")
    args = ap.parse_args()

    env = load_env()

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "scanqbt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "scan-qbt.py"))
    scanqbt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanqbt)

    local = []
    for entry in sorted(os.listdir(scanqbt.DEFAULT_BT_BACKUP)):
        if entry.endswith(".torrent"):
            try:
                local.append(scanqbt.parse(os.path.join(scanqbt.DEFAULT_BT_BACKUP, entry)))
            except Exception:
                pass
    if args.tracker:
        needle = args.tracker.lower()
        local = [t for t in local if any(needle in h.lower() for h in t["trackers"])]
    local.sort(key=lambda t: -t["size"])
    if args.limit:
        local = local[: args.limit]

    cache = {}
    if os.path.exists(CACHE) and not args.refresh:
        cache = json.load(open(CACHE, encoding="utf-8"))

    out, hits = [], 0
    for i, t in enumerate(local, 1):
        q = query_for(t["name"])
        key = f'{args.indexer}:{q}'
        if key in cache:
            results = cache[key]
        else:
            try:
                results = search(env, args.indexer, q)
            except Exception as exc:
                print(f"[{i}/{len(local)}] SEARCH FAILED {q!r}: {exc}", file=sys.stderr)
                results = []
            cache[key] = results
            json.dump(cache, open(CACHE, "w", encoding="utf-8"))
            time.sleep(args.delay)

        matches = [r for r in results
                   if abs(r.get("size", 0) - t["size"]) <= args.tolerance]
        record = {
            "name": t["name"],
            "size": t["size"],
            "infohash": t["infohash"],
            "save_path": t["save_path"],
            "query": q,
            "results": len(results),
            "matches": [{"title": m["title"], "size": m["size"],
                         "seeders": m.get("seeders", 0),
                         "guid": m.get("guid"),
                         "downloadUrl": m.get("downloadUrl")} for m in matches],
        }
        out.append(record)
        mark = "MATCH" if matches else "  -  "
        if matches:
            hits += 1
        print(f'[{i}/{len(local)}] {mark} {human(t["size"]):>9} '
              f'{len(results):>3} hits  {t["name"][:58]}')
        for m in matches:
            print(f'          -> S:{m.get("seeders",0):<4} {m["title"][:70]}')

    print(f"\n{hits}/{len(local)} local torrents have an exact-size candidate")
    if args.json:
        json.dump(out, open(args.json, "w", encoding="utf-8"), indent=2)
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
