# torsync

Cross-seed tooling for a qBittorrent library: take torrents that are already
seeding on one private tracker and seed the same data on another, without
re-downloading a single byte.

Built around a specific setup - qBittorrent on a Windows host, Prowlarr on a
Linux Docker box - but the scripts are generic.

## How cross-seeding actually works

Two torrents, one copy of the data:

1. The existing torrent keeps seeding to tracker A.
2. The `.torrent` from tracker B is added as a **separate** torrent.
3. Its save path points at the **same folder** as the existing data.
4. A force-recheck confirms 100%, and it starts seeding.

Both trackers count you as a seeder. One copy on disk.

> **Never merge announce URLs.** Adding tracker B's announce URL to tracker A's
> torrent leaks your passkey into tracker B's swarm. Both sites ban for it.
> Two separate torrent entries is the only safe method.
>
> Keep Auto Torrent Management **off** for cross-seeded torrents - it can
> relocate files and break the other torrent.

A match only works if the remote release is byte-identical: same file layout,
same sizes. A name match means nothing. That is why `match.py` filters on exact
size rather than title similarity.

## Layout

```
scripts/scan-qbt.py          inventory local torrents (bencode, no WebUI needed)
scripts/match.py             search an indexer per torrent, keep exact-size hits
scripts/add-indexers.sh      register TorrentLeech / TorrentBD in Prowlarr
scripts/flaresolverr-tag.sh  attach a Cloudflare solver to one indexer
stack/docker-compose.yml     Prowlarr + Byparr + Solvearr
stack/byparr/                Byparr image patched to accept request cookies
```

## Setup

```bash
cp .env.example .env      # fill in credentials; .env is gitignored
cd stack && docker compose up -d
bash scripts/add-indexers.sh
```

Inventory what you have, then look for candidates:

```bash
python3 scripts/scan-qbt.py --tracker torrentbd
python3 scripts/match.py --indexer 1 --tracker torrentbd --json tl-matches.json
```

`match.py` caches to `.match-cache.json`. Trackers rate limit hard -
TorrentLeech declares `requestDelay: 4.1`, so a ~90 torrent sweep is several
minutes of deliberate waiting. Do not lower `--delay` below the indexer's
declared value.

## Cloudflare solvers

Private trackers sit behind Cloudflare, and the three common solvers each fail
differently. Measured, not assumed:

| Solver | Clears Cloudflare | Forwards cookies |
|---|---|---|
| FlareSolverr | no - times out on Managed Challenge | yes |
| Solvearr | no - 403 | yes |
| Byparr (upstream) | yes | **no - silently dropped** |
| Byparr (patched here) | yes | yes |

Upstream Byparr declares `cookies` on its *response* model only, so cookies sent
by a client are dropped by pydantic and every request goes out anonymous.
`stack/byparr/add_cookie_support.py` adds the field to `LinkRequest` and injects
into the browser context before navigation. The patch is applied at image build
and fails loudly if upstream's layout changes.

Solvers are expensive and flaky. None is attached to any indexer by default -
opt in per indexer only when one actually returns a Cloudflare error:

```bash
scripts/flaresolverr-tag.sh on TorrentBD
```

TorrentLeech does not need one; it serves plain requests fine.

## Known limits

- **Cookie-auth trackers may bind sessions to a browser fingerprint.** TorrentBD
  sets a `ufp` cookie and rejects a valid `x_auth` presented by a different
  browser, so a solver-based proxy cannot authenticate even with fresh cookies.
- **Music rarely cross-seeds.** General trackers have one flat audio category
  and little overlap in piece layout with dedicated music trackers.
- **`cf_clearance` is bound to IP and User-Agent.** Harvest cookies on a machine
  behind the same egress IP as the solver, or they will not work.
