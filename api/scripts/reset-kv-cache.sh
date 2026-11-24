#!/usr/bin/env bash
# Delete every entry in the answer cache (AEROQUERY_ANSWER_CACHE).
#
# Usage (from anywhere):
#   api/scripts/reset-kv-cache.sh            # remote (production) namespace
#   api/scripts/reset-kv-cache.sh --local    # local `pywrangler dev` storage
#   api/scripts/reset-kv-cache.sh --yes      # skip the confirmation prompt
set -euo pipefail

BINDING="AEROQUERY_ANSWER_CACHE"
TARGET="--remote"
FORCE=false

for arg in "$@"; do
  case "$arg" in
    --local) TARGET="--local" ;;
    --remote) TARGET="--remote" ;;
    -y|--yes) FORCE=true ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

command -v jq >/dev/null || { echo "jq is required (brew install jq)" >&2; exit 1; }

cd "$(dirname "$0")/.."   # api/, where wrangler.toml lives

keys_file="$(mktemp -t aeroquery-kv-keys.XXXXXX)"
trap 'rm -f "$keys_file"' EXIT

npx wrangler kv key list --binding "$BINDING" "$TARGET" | jq '[.[].name]' > "$keys_file"
count="$(jq length "$keys_file")"

if [[ "$count" -eq 0 ]]; then
  echo "Cache is already empty (${TARGET#--})."
  exit 0
fi

if [[ "$FORCE" != true ]]; then
  read -r -p "Delete $count cached answers from $BINDING (${TARGET#--})? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }
fi

npx wrangler kv bulk delete "$keys_file" --binding "$BINDING" "$TARGET" --force
echo "Deleted $count keys from $BINDING (${TARGET#--})."
