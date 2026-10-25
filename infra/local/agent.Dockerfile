# Processing agent with FFmpeg, whisper.cpp and a whisper model for local runs.
FROM python:3.12-slim-bookworm AS whisper
RUN apt-get update \
  && apt-get install --no-install-recommends -y build-essential cmake git ca-certificates curl \
  && rm -rf /var/lib/apt/lists/*
ARG WHISPER_CPP_VERSION=v1.7.5
RUN git clone --depth 1 --branch "${WHISPER_CPP_VERSION}" https://github.com/ggml-org/whisper.cpp /src/whisper.cpp \
  && cmake -S /src/whisper.cpp -B /src/build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DWHISPER_BUILD_TESTS=OFF \
  && cmake --build /src/build -j --target whisper-cli
ARG WHISPER_MODEL=ggml-small-q5_1.bin
RUN mkdir -p /models \
  && curl -fsSL -o "/models/${WHISPER_MODEL}" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/${WHISPER_MODEL}"

FROM python:3.12-slim-bookworm
RUN apt-get update \
  && apt-get install --no-install-recommends -y ffmpeg fonts-dejavu-core libgomp1 \
  && rm -rf /var/lib/apt/lists/*
COPY --from=whisper /src/build/bin/whisper-cli /opt/localizer/bin/whisper-cli
COPY --from=whisper /models /opt/localizer/models

WORKDIR /opt/localizer-agent
COPY apps/processing-agent/pyproject.toml ./
COPY apps/processing-agent/src ./src
RUN pip install --no-cache-dir .

# Placeholder watermark: a translucent blue badge.
RUN mkdir -p /opt/localizer/assets \
  && ffmpeg -hide_banner -loglevel error -f lavfi -i "color=c=0x1e88e5:s=200x60,format=rgba,colorchannelmixer=aa=0.85" \
    -frames:v 1 /opt/localizer/assets/watermark.png
COPY infra/local/agent-entrypoint.sh /usr/local/bin/agent-entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["/bin/sh", "/usr/local/bin/agent-entrypoint.sh"]
