#!/usr/bin/env sh
# Runs Wrangler inside the Linux control-plane image, for hosts where the Workers runtime cannot run
# (macOS < 13.5). Usage: scripts/wrangler.sh <wrangler args>, e.g. scripts/wrangler.sh whoami
#
# Authenticate either with `scripts/wrangler.sh login` (open the printed URL in your browser; the OAuth
# callback on localhost:8976 is forwarded into the container and the login is kept in the Docker volume
# localizer-wrangler-config), or with an API token: export CLOUDFLARE_API_TOKEN=... (and
# CLOUDFLARE_ACCOUNT_ID=... if the token spans accounts).
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
image=localizer-local-control-plane:latest

if ! docker image inspect "$image" >/dev/null 2>&1; then
  docker compose -f "$repo_root/infra/local/compose.yaml" build control-plane
fi

tty_flags=""
[ -t 0 ] && [ -t 1 ] && tty_flags="-it"

# `wrangler login` waits for the browser's OAuth redirect to localhost:8976; publish that port and
# listen on all container interfaces. The container cannot open a browser, so the URL is printed.
login_flags=""
if [ "${1:-}" = "login" ]; then
  login_flags="-p 127.0.0.1:8976:8976"
  shift
  set -- login --callback-host 0.0.0.0 --callback-port 8976 --browser false "$@"
fi

# The repository is mounted so wrangler.jsonc edits persist. Anonymous volumes keep the image's
# Linux node_modules instead of the host's macOS ones.
exec docker run --rm $tty_flags $login_flags \
  --entrypoint npx \
  -e CLOUDFLARE_API_TOKEN -e CLOUDFLARE_ACCOUNT_ID -e CI=true -e WRANGLER_SEND_METRICS=false \
  -v localizer-wrangler-config:/root/.config/.wrangler \
  -v "$repo_root:/repo" \
  -v /repo/node_modules \
  -v /repo/apps/control-plane/node_modules \
  -v /repo/packages/contracts/node_modules \
  -w /repo/apps/control-plane \
  "$image" wrangler "$@"
