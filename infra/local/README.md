# Local end-to-end stack

Runs the control plane and processing agent together on one machine, so a
real import can go from inbox file to rendered preview before anything is
deployed. Everything runs in Linux containers, which also works around
Wrangler not running on macOS 12.

| Service | What it is |
| --- | --- |
| `control-plane` | The real Worker under `wrangler dev`, with a local D1 database |
| `access-edge` | Stand-in for Cloudflare Access: issues Access-shaped JWTs and serves HTTPS |
| `fake-providers` | Fake Gemini and Azure Speech endpoints (placeholder text, sine-tone audio) |
| `agent` | The real processing agent with FFmpeg and whisper.cpp (`ggml-small-q5_1`) |
| `smoke` | Script that plays the administrator through the API |

What is real: Worker code and auth checks, D1 migrations and leasing, agent
ingest, FFmpeg, whisper.cpp transcription, mixing, rendering and preview
registration. What is fake: Cloudflare Access login, Gemini and Azure output.
Nothing is uploaded anywhere.

Speed on this stack says nothing about the OCI ARM host; the OCI benchmark
(spec section 16) is still required.

## Prerequisites

Docker with Colima (or Docker Desktop). The first build downloads Node and
Python images, builds whisper.cpp and downloads a ~190 MB model.

```sh
colima start --cpu 4 --memory 8
```

## Run

```sh
cd infra/local
docker compose build
./make-fixture.sh                       # creates inbox/sample-zh.mp4 (macOS only)
docker compose up -d
docker compose run --rm smoke           # prints each state transition
docker compose logs -f agent            # agent log, in another terminal
```

To test your own authorized clip, copy it into `infra/local/inbox/` and run
`INBOX_FILE_ID=<file name> docker compose run --rm smoke`. Media in `inbox/`
is git-ignored; never commit creator media.

Reset all local data:

```sh
docker compose down -v
```

## Notes

- The administrator is always logged in locally. Agent routes still require
  the service-token headers, as they do behind real Access.
- Fake translations are sized to fit each source window, so the default run
  does not stop at `TIMING_REVIEW_REQUIRED`.
- The agent pins the model and watermark hashes to whatever the image contains;
  production must supply them from deployment configuration.
