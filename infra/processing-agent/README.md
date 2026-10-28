# Deploying the processing plane with Portainer

The system has two halves that deploy separately:

| Part | Runs on | Deployed with |
|---|---|---|
| Control plane (API, D1, cron) | Cloudflare Workers | `wrangler` (section 4) |
| Processing agent | Docker host, e.g. the OCI ARM64 VM | Portainer stack from `docker-compose.yml` |

The agent opens no ports. It only makes outbound HTTPS calls to the control plane, so the stack
has no published ports and needs no tunnel today.

## 1. Build and push the image

Choose one of these:

- **GitHub Actions (recommended):**
  1. In the repository settings, set the variable `REGISTRY` (for example
     `registry.example.com/localizer`) and the secrets `REGISTRY_USERNAME` and `REGISTRY_PASSWORD`.
  2. Run the `agent-image` workflow manually, or push a tag such as `agent-v0.1.0`.
  3. The workflow runs the agent tests, then pushes `localizer-agent:sha-<commit>` for
     `linux/arm64` and `linux/amd64`.
- **Local:** with Docker Buildx running, run
  `REGISTRY=registry.example.com/localizer scripts/build-agent-image.sh`.
  It builds `linux/arm64` by default; set `PLATFORMS` to build others.

Always deploy an immutable `sha-…` tag, not `latest`, so a redeploy cannot silently change code.

In Portainer, add the registry under **Registries** so the host can pull from it.

## 2. Prepare the host

Run these on the Docker host. The spec expects a dedicated media volume mounted at `/mnt/localizer`.

```sh
sudo mkdir -p /mnt/localizer/{workspaces,inbox,models,assets}

# Whisper model (the agent refuses to start if its SHA-256 does not match)
sudo curl -L -o /mnt/localizer/models/ggml-small-q5_1.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin
sha256sum /mnt/localizer/models/ggml-small-q5_1.bin      # -> WHISPER_MODEL_SHA256

# Watermark: a transparent PNG with the logo and brand name
sudo cp watermark.png /mnt/localizer/assets/watermark.png
sha256sum /mnt/localizer/assets/watermark.png            # -> WATERMARK_SHA256

# The container runs as UID/GID 10001
sudo chown -R 10001:10001 /mnt/localizer
```

Creator files for manual import go into `/mnt/localizer/inbox` (over Bastion/SCP). The agent
mounts that directory read-only.

## 3. Create the stack in Portainer

1. Go to **Stacks → Add stack**.
2. Choose **Repository**, then set:
   - the repository URL;
   - the branch;
   - the compose path `infra/processing-agent/docker-compose.yml`.

   Alternatively, paste the file into the **Web editor**.
3. Under **Environment variables**, add every value from `stack.env.example`, or load the file and
   edit it. The required values are:
   - `REGISTRY` and `AGENT_IMAGE_TAG`;
   - `CONTROL_PLANE_URL`, `AGENT_ACCESS_CLIENT_ID` and `AGENT_ACCESS_CLIENT_SECRET` (section 4);
   - `WHISPER_MODEL_SHA256` and `WATERMARK_SHA256`.
4. Turn on translation by setting all three `GEMINI_*` values, and voice by setting all three
   `AZURE_SPEECH_*` values. Set all three of a provider or none: a partial set stops the agent at
   startup with a configuration error.
5. Deploy. To upgrade later, change `AGENT_IMAGE_TAG` and use **Update the stack** with
   **Re-pull image** enabled.

If a required variable is missing, the deploy fails immediately with a message that names it.

## 4. Cloudflare side the agent depends on

1. **Service token:** create one under Zero Trust → Access → Service credentials. Its Client ID
   and Client Secret go into `AGENT_ACCESS_CLIENT_ID` and `AGENT_ACCESS_CLIENT_SECRET`.
2. **Agent API hostname:** route a hostname (for example `agent-api.example.com`) to the Worker.
   Protect it with an Access application whose only policy is **Service Auth** for that token.
   Set `CONTROL_PLANE_URL` to that origin. The admin UI hostname keeps its own Access application,
   limited to the administrator's email.
3. **Worker configuration** in `apps/control-plane/wrangler.jsonc` or the dashboard:
   - `AGENT_SERVICE_CLIENT_ID`: the same Client ID. The Worker matches it against the service
     JWT's `common_name`.
   - `AGENT_ACCESS_AUDIENCE`: the agent Access application's AUD tag.
   - `ADMIN_ORIGIN`, `ADMIN_EMAIL`, `ACCESS_AUDIENCE` and `ACCESS_ISSUER`.
   - The `CSRF_SIGNING_SECRET` secret, set with `wrangler secret put CSRF_SIGNING_SECRET`.
4. **D1:** create the database, put its `database_id` in `wrangler.jsonc`, then run
   `wrangler d1 migrations apply <database> --remote`. That applies migrations 0001–0007.
   Then deploy with `wrangler deploy`.

## 5. Check that it works

- In Portainer, the `agent` container stays **running** and does not restart in a loop. A
  configuration error shows up in the container logs.
- Create an import through `POST /api/v1/projects/:id/imports` with
  `allowedProcessors: ["GOOGLE_GEMINI", "AZURE_SPEECH"]`. Then poll `GET /api/v1/jobs/:id` and
  watch it advance through `INGESTING`, `PROBING` and later states.

## Known limitations of this deployment

- **Preview playback is not served yet.** The Nginx authorization path (audit item P0-5) is a
  separate feature, so this stack runs neither Nginx nor `cloudflared`.
- **Publishing is not wired into the agent yet** (P0-7). Jobs stop at `WAITING_PUBLISH_APPROVAL`.
- **Provider secrets live in the Portainer stack environment.** The spec's OCI Vault /
  instance-principal integration is not implemented. Restrict who can view the stack in Portainer.
- **Every risk-gate PoC is still `BLOCKED`.** Per the spec, production imports stay disabled until
  the OCI media benchmark sets a passing maximum duration. Treat this deployment as staging until
  then.
