#!/usr/bin/env bash
# Attach (or detach) FlareSolverr to a single indexer.
#
# FlareSolverr is unreliable, so it stays off by default. Only run this when an
# indexer genuinely fails with a Cloudflare challenge, and only for that one.
#
#   scripts/flaresolverr-tag.sh on  TorrentBD
#   scripts/flaresolverr-tag.sh off TorrentBD
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

action=${1:?usage: $0 on|off <IndexerName>}
target=${2:?usage: $0 on|off <IndexerName>}

api() {
  local method=$1 path=$2; shift 2
  curl -fsS -X "$method" "${PROWLARR_URL}${path}" \
    -H "X-Api-Key: ${PROWLARR_API_KEY}" \
    -H 'Content-Type: application/json' "$@"
}

# Reuse an existing "flaresolverr" tag or create one.
tag_id=$(api GET /api/v1/tag | python3 -c '
import sys, json
print(next((t["id"] for t in json.load(sys.stdin) if t["label"] == "flaresolverr"), ""))
')
if [ -z "$tag_id" ]; then
  tag_id=$(api POST /api/v1/tag -d '{"label":"flaresolverr"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
fi

# The proxy applies to whichever indexers carry the tag.
proxy=$(api GET /api/v1/indexerproxy | python3 -c '
import sys, json
p = next(x for x in json.load(sys.stdin) if x["name"] == "FlareSolverr")
p["tags"] = [int(sys.argv[1])]
print(json.dumps(p))
' "$tag_id")
api PUT "/api/v1/indexerproxy/$(python3 -c 'import sys,json;print(json.loads(sys.stdin.read())["id"])' <<<"$proxy")" -d "$proxy" >/dev/null

body=$(api GET /api/v1/indexer | python3 -c '
import sys, json
name, tag, action = sys.argv[1], int(sys.argv[2]), sys.argv[3]
ix = next((x for x in json.load(sys.stdin) if x["name"] == name), None)
if ix is None:
    sys.exit(f"no indexer named {name}")
tags = set(ix["tags"])
tags.add(tag) if action == "on" else tags.discard(tag)
ix["tags"] = sorted(tags)
print(json.dumps(ix))
' "$target" "$tag_id" "$action")

api PUT "/api/v1/indexer/$(python3 -c 'import sys,json;print(json.loads(sys.stdin.read())["id"])' <<<"$body")" -d "$body" >/dev/null
echo "FlareSolverr $action for $target"
