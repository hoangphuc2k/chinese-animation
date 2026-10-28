# syntax=docker/dockerfile:1.7
# Processing-agent image for the OCI ARM64 host (also builds for linux/amd64).
# whisper.cpp is compiled from a pinned tag so transcription output is reproducible.
ARG WHISPER_CPP_VERSION=v1.7.4

FROM debian:bookworm-slim AS whisper-build
ARG WHISPER_CPP_VERSION
RUN apt-get update \
  && apt-get install --no-install-recommends -y build-essential cmake git ca-certificates \
  && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${WHISPER_CPP_VERSION}" https://github.com/ggml-org/whisper.cpp.git /src/whisper.cpp
WORKDIR /src/whisper.cpp
# GGML_NATIVE=OFF: never tune for the build machine; the image runs on a different CPU.
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF \
    -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF \
  && cmake --build build --config Release -j"$(nproc)" --target whisper-cli \
  && install -D -m 0755 build/bin/whisper-cli /out/whisper-cli

FROM python:3.12-slim-bookworm AS runtime
ARG WHISPER_CPP_VERSION
RUN apt-get update \
  && apt-get install --no-install-recommends -y ffmpeg libgomp1 tini \
  && rm -rf /var/lib/apt/lists/*

COPY --from=whisper-build /out/whisper-cli /opt/localizer/bin/whisper-cli

WORKDIR /opt/localizer-agent
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# Paths follow the spec's dedicated media volume (/mnt/localizer); models and the watermark are
# mounted read-only at runtime and verified against pinned SHA-256 values by the agent.
RUN useradd --create-home --uid 10001 agent \
  && mkdir -p /mnt/localizer/workspaces /mnt/localizer/inbox /opt/localizer/models /opt/localizer/assets \
  && chown -R agent:agent /mnt/localizer

USER agent
ENV PYTHONUNBUFFERED=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  WORKSPACE_ROOT=/mnt/localizer/workspaces \
  INBOX_ROOT=/mnt/localizer/inbox \
  LOCALIZER_WHISPER_CPP_VERSION=${WHISPER_CPP_VERSION}
ENTRYPOINT ["tini", "--", "python", "-m", "localizer_agent"]
