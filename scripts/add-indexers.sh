#!/usr/bin/env bash
# Register TorrentLeech + TorrentBD in Prowlarr from .env credentials.
# Idempotent: updates the indexer if it already exists.
#
# FlareSolverr is deliberately NOT tagged onto these indexers. It is flaky,
# so it stays unused until a tracker actually fails a Cloudflare challenge.
# To enable it for one indexer only, see scripts/flaresolverr-tag.sh.
set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "missing .env (copy .env.example)" >&2; exit 1; }
set -a; . ./.env; set +a

: "${PROWLARR_URL:?}" "${PROWLARR_API_KEY:?}"

api() {
  local method=$1 path=$2
  shift 2
  curl -fsS -X "$method" "${PROWLARR_URL}${path}" \
    -H "X-Api-Key: ${PROWLARR_API_KEY}" \
    -H 'Content-Type: application/json' "$@"
}

# find_id <indexer name> -> prints existing id, or nothing
find_id() {
  api GET /api/v1/indexer | python3 -c '
import sys, json
want = sys.argv[1]
for x in json.load(sys.stdin):
    if x["name"] == want:
        print(x["id"])
        break
' "$1"
}

# upsert <name> <definitionFile> <field=value>...
upsert() {
  local name=$1 deffile=$2; shift 2
  local payload
  payload=$(python3 -c '
import sys, json
name, deffile = sys.argv[1], sys.argv[2]
fields = [{"name": "definitionFile", "value": deffile}]
for pair in sys.argv[3:]:
    k, _, v = pair.partition("=")
    fields.append({"name": k, "value": v})
print(json.dumps({
    "name": name,
    "implementation": "Cardigann",
    "configContract": "CardigannSettings",
    "enable": True,
    "priority": 25,
    "appProfileId": 1,
    "tags": [],
    "fields": fields,
}))
' "$name" "$deffile" "$@")

  local id
  id=$(find_id "$name")
  if [ -n "$id" ]; then
    echo "updating $name (id $id)"
    payload=$(python3 -c '
import sys, json
d = json.loads(sys.stdin.read()); d["id"] = int(sys.argv[1]); print(json.dumps(d))
' "$id" <<<"$payload")
    api PUT "/api/v1/indexer/${id}" -d "$payload" >/dev/null
  else
    echo "creating $name"
    api POST /api/v1/indexer -d "$payload" >/dev/null
  fi
}

if [ -n "${TL_USERNAME:-}" ] && [ -n "${TL_PASSWORD:-}" ]; then
  upsert TorrentLeech torrentleech \
    baseUrl=https://www.torrentleech.org/ \
    username="$TL_USERNAME" \
    password="$TL_PASSWORD" \
    alt2fatoken="${TL_2FA_TOKEN:-}"
else
  echo "skip TorrentLeech: TL_USERNAME/TL_PASSWORD unset"
fi

if [ -n "${TBD_COOKIE:-}" ]; then
  upsert TorrentBD torrentbd \
    baseUrl=https://www.torrentbd.net/ \
    cookie="$TBD_COOKIE" \
    useragent="${TBD_USERAGENT:-}"
else
  echo "skip TorrentBD: TBD_COOKIE unset"
fi

echo
echo "--- indexers ---"
api GET /api/v1/indexer | python3 -c '
import sys, json
for x in json.load(sys.stdin):
    print(f'"'"'{x["id"]:>3}  {x["name"]:<16} enabled={x["enable"]}'"'"')
    print(f'"'"'     torznab: {sys.argv[1]}/{x["id"]}/api?apikey={sys.argv[2]}'"'"')
' "$PROWLARR_URL" "$PROWLARR_API_KEY"
