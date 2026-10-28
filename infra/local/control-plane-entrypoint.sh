#!/bin/sh
# Writes local-only Worker variables, applies D1 migrations, then serves the Worker.
set -eu
cat > .dev.vars <<EOF
ACCESS_AUDIENCE=${ADMIN_AUDIENCE}
ACCESS_ISSUER=${ACCESS_ISSUER}
ADMIN_EMAIL=${ADMIN_EMAIL}
ADMIN_ORIGIN=${ADMIN_ORIGIN}
AGENT_ACCESS_AUDIENCE=${AGENT_AUDIENCE}
AGENT_SERVICE_CLIENT_ID=${AGENT_CLIENT_ID}
CSRF_SIGNING_SECRET=${CSRF_SIGNING_SECRET}
DEPLOYMENT_VERSION=local
EOF
npx wrangler d1 migrations apply control-plane-local --local --persist-to /state
exec npx wrangler dev --ip 0.0.0.0 --port 8787 --persist-to /state
