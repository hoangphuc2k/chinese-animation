FROM python:3.12-slim-bookworm AS runtime

RUN apt-get update \
  && apt-get install --no-install-recommends -y ffmpeg \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/localizer-agent
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 agent \
  && mkdir -p /var/lib/localizer-agent/workspace /var/lib/localizer-agent/inbox \
  && chown -R agent:agent /var/lib/localizer-agent

USER agent
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "localizer_agent"]
