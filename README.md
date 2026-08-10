# Chinese Animation Localizer

This repository is specification-first. The approved product design is
[`2026-07-24-chinese-animation-localization-mvp-design.md`](./2026-07-24-chinese-animation-localization-mvp-design.md).
The operations-console UI is defined in [`docs/superpowers/specs/2026-07-24-operations-console-ui-design.md`](./docs/superpowers/specs/2026-07-24-operations-console-ui-design.md), with visual references in [`docs/ui-mockups/`](./docs/ui-mockups/).
The ordered execution plan is [`docs/superpowers/plans/2026-07-24-mvp-master-implementation-sequence.md`](./docs/superpowers/plans/2026-07-24-mvp-master-implementation-sequence.md).

Implementation is split between a Cloudflare control plane and an OCI processing
agent. No production import or publishing behavior is enabled yet.

## Local prerequisites

Use Node.js 24+, npm workspaces, Python 3.12+, and uv. Run `npm install`
before the root quality commands. Python dependencies are managed from
`apps/processing-agent/pyproject.toml` with `uv sync`.
