# Runs the control-plane Worker with `wrangler dev` and a local D1 database.
# Linux is required: Wrangler's workerd runtime does not run on macOS 12.
FROM node:24-bookworm-slim

ENV CI=true WRANGLER_SEND_METRICS=false
WORKDIR /repo

COPY package.json package-lock.json tsconfig.base.json ./
COPY apps/control-plane/package.json apps/control-plane/
COPY packages/contracts/package.json packages/contracts/
RUN npm ci --no-audit --no-fund

COPY packages/contracts packages/contracts
COPY apps/control-plane apps/control-plane
COPY infra/local/control-plane-entrypoint.sh /usr/local/bin/control-plane-entrypoint.sh

WORKDIR /repo/apps/control-plane
HEALTHCHECK --interval=5s --timeout=3s --start-period=60s --retries=30 \
  CMD node -e "fetch('http://127.0.0.1:8787/api/v1/health').then((r) => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"
ENTRYPOINT ["/bin/sh", "/usr/local/bin/control-plane-entrypoint.sh"]
