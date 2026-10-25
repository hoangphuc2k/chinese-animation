#!/bin/sh
# Builds a rights-free test clip in infra/local/inbox/: Chinese speech from the
# macOS Ting-Ting voice over an FFmpeg test pattern. Requires macOS `say` and
# the built agent image (for FFmpeg).
set -eu
cd "$(dirname "$0")"
name="${1:-sample-zh.mp4}"
mkdir -p inbox
work="inbox/.work"
mkdir -p "$work"
say -v Ting-Ting -o "$work/speech.aiff" \
  "大家好，欢迎收看今天的节目。这位少年名叫林动，他来自一个小家族。为了保护家人，他决定踏上修炼之路。三年之后，他终于突破了元丹境。"
docker compose run --rm --no-deps -v "$PWD/inbox:/out" --entrypoint ffmpeg agent \
  -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=1280x720:rate=25" -i "/out/.work/speech.aiff" \
  -map 0:v -map 1:a -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "/out/$name"
rm -rf "$work"
echo "Created infra/local/inbox/$name"
