#!/bin/sh
# Local runs pin the model and watermark to whatever the image contains.
# Production supplies these hashes from deployment configuration instead.
set -eu
WHISPER_MODEL_SHA256="$(sha256sum "$WHISPER_MODEL_PATH" | cut -d ' ' -f 1)"
WATERMARK_SHA256="$(sha256sum "$WATERMARK_PATH" | cut -d ' ' -f 1)"
WHISPER_BINARY_VERSION="whisper.cpp-$("$WHISPER_BINARY_PATH" --version 2>/dev/null | head -n 1 | tr -cd 'A-Za-z0-9._-' || true)"
export WHISPER_MODEL_SHA256 WATERMARK_SHA256 WHISPER_BINARY_VERSION
exec python -m localizer_agent
