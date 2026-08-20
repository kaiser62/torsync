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
# Decorations that never appear in a tracker's own title.
RELEASE_TAGS = re.compile(
    r"\b(fitgirl|dodi|elamigos|xatab|kaos|qxr|psa|repack|portable|crackfix)\b"
    r"|\bby\s+\w+$"
    r"|^(codex|skidrow|plaza|reloaded|razor1911|empress)[-.]", re.I)
# Edition / version / packaging noise.
EDITION = re.compile(
    r"\b(gold|deluxe|ultimate|premium|definitive|complete|digital|anniversary"
    r"|remastered|enhanced|tour|legendary)\s+edition\b"
    r"|\bv?\d+[.\d]{2,}\b"
    r"|\bupdate\s*\d*\b|\b\d+\s*dlcs?\b|\bincl\b|\bmulti\d+\b|\blatest\b"
    r"|\b\d{2}bit\b|\b[\d.]+\s*khz\b"
    r"|\b(iso|exe|rar|mkv|mp4)\b", re.I)
BRACKETS = re.compile(r"[\[({][^\])}]*[\])}]")
YEAR = re.compile(r"\b(19|20)\d{2}\b")


def clean(text):
    """Normalise a release name into plain searchable words."""
    for a, b in (("’", "'"), ("‘", "'"), ("–", "-"), ("—", "-")):
        text = text.replace(a, b)
    text = BRACKETS.sub(" ", text)
    text = RELEASE_TAGS.sub(" ", text)
    text = EDITION.sub(" ", text)
    text = re.sub(r"[._]+", " ", text)
    text = YEAR.sub(" ", text)
    text = re.sub(r"[^\w\s'-]+", " ", text)
    text = re.sub(r"\s+-+\s+|\s+-+$|^-+\s+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -")


def queries_for(name):
    """Several search angles; trackers title releases inconsistently.

    One query misses far too much: "Far Cry 5-Gold Edition v1.011 + 5 DLCs
    [FitGirl Repack]" has to reduce to "Far Cry 5" to find anything at all.
    Progressively shorter prefixes trade precision for recall, and the caller
    unions the results.
    """
    base = clean(name)
    words = base.split()
    out = []
    for candidate in (base, " ".join(words[:5]), " ".join(words[:3]), " ".join(words[:2])):
        candidate = candidate.strip(" -")
        if len(candidate) >= 3 and candidate.lower() not in {o.lower() for o in out}:
            out.append(candidate)
    return out


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
    ap.add_argument("--near", type=float, default=0.02,
                    help="fractional size difference still worth reporting as a near miss")
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
        # Union several query angles - one phrasing misses far too much.
        results, seen_guid = [], set()
        for q in queries_for(t["name"]):
            key = f"{args.indexer}:{q}"
            if key in cache:
                batch = cache[key]
            else:
                try:
                    batch = search(env, args.indexer, q)
                except Exception as exc:
                    print(f"[{i}/{len(local)}] SEARCH FAILED {q!r}: {exc}", file=sys.stderr)
                    batch = []
                cache[key] = batch
                json.dump(cache, open(CACHE, "w", encoding="utf-8"))
                time.sleep(args.delay)
            for r in batch:
                guid = r.get("guid") or r.get("title")
                if guid not in seen_guid:
                    seen_guid.add(guid)
                    results.append(r)
            # Enough signal already; skip the broader, noisier fallbacks.
            if len(results) >= 25:
                break

        exact, near = [], []
        for r in results:
            size = r.get("size") or 0
            if not size:
                continue
            delta = abs(size - t["size"])
            if delta <= args.tolerance:
                exact.append(r)
            elif delta / t["size"] <= args.near:
                near.append(r)

        record = {
            "name": t["name"],
            "size": t["size"],
            "infohash": t["infohash"],
            "save_path": t["save_path"],
            "queries": queries_for(t["name"]),
            "results": len(results),
            "matches": [{"title": m["title"], "size": m["size"],
                         "seeders": m.get("seeders", 0),
                         "guid": m.get("guid"),
                         "downloadUrl": m.get("downloadUrl")} for m in exact],
            "near": [{"title": m["title"], "size": m["size"],
                      "delta_pct": round((m["size"] - t["size"]) / t["size"] * 100, 3),
                      "seeders": m.get("seeders", 0),
                      "downloadUrl": m.get("downloadUrl")} for m in
                     sorted(near, key=lambda x: abs(x["size"] - t["size"]))[:3]],
        }
        out.append(record)
        if exact:
            hits += 1
        mark = "MATCH" if exact else ("near " if near else "  -  ")
        print(f'[{i}/{len(local)}] {mark} {human(t["size"]):>9} '
              f'{len(results):>3} hits  {t["name"][:52]}')
        for m in exact:
            print(f'          -> S:{m.get("seeders",0):<4} {m["title"][:66]}')
        for m in record["near"]:
            print(f'          ~> {m["delta_pct"]:+.2f}% S:{m["seeders"]:<4} {m["title"][:60]}')

    print(f"\n{hits}/{len(local)} local torrents have an exact-size candidate")
    if args.json:
        json.dump(out, open(args.json, "w", encoding="utf-8"), indent=2)
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
