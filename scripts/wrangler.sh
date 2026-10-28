#!/usr/bin/env sh
# Runs Wrangler inside the Linux control-plane image, for hosts where the Workers runtime cannot run
# (macOS < 13.5). Usage: scripts/wrangler.sh <wrangler args>, e.g. scripts/wrangler.sh whoami
#
# Authenticate with an API token rather than `wrangler login` (its browser callback cannot reach a
# container): export CLOUDFLARE_API_TOKEN=... (and CLOUDFLARE_ACCOUNT_ID=... if the token spans accounts).
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
image=localizer-local-control-plane:latest

if ! docker image inspect "$image" >/dev/null 2>&1; then
  docker compose -f "$repo_root/infra/local/compose.yaml" build control-plane
fi

tty_flags=""
[ -t 0 ] && [ -t 1 ] && tty_flags="-it"

# The repository is mounted so wrangler.jsonc edits persist. Anonymous volumes keep the image's
# Linux node_modules instead of the host's macOS ones.
exec docker run --rm $tty_flags \
  --entrypoint npx \
  -e CLOUDFLARE_API_TOKEN -e CLOUDFLARE_ACCOUNT_ID -e CI=true -e WRANGLER_SEND_METRICS=false \
  -v "$repo_root:/repo" \
  -v /repo/node_modules \
  -v /repo/apps/control-plane/node_modules \
  -v /repo/packages/contracts/node_modules \
  -w /repo/apps/control-plane \
  "$image" wrangler "$@"
