#!/usr/bin/env sh
# Build and push the processing-agent image from a machine with Docker Buildx.
# Usage: REGISTRY=registry.example.com/localizer scripts/build-agent-image.sh
set -eu

: "${REGISTRY:?Set REGISTRY, for example registry.example.com/localizer}"
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
tag=${TAG:-sha-$(git -C "$repo_root" rev-parse --short HEAD)}
platforms=${PLATFORMS:-linux/arm64}

if [ -n "$(git -C "$repo_root" status --porcelain -- apps/processing-agent)" ]; then
  echo "apps/processing-agent has uncommitted changes; commit them so the tag matches the image." >&2
  exit 1
fi

docker buildx build \
  --platform "$platforms" \
  --tag "$REGISTRY/localizer-agent:$tag" \
  --push \
  "$repo_root/apps/processing-agent"

echo "Pushed $REGISTRY/localizer-agent:$tag"
