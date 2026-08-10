# Repository Guidelines

## Project Structure & Module Organization

This repository is specification-first. The root document,
`2026-07-24-chinese-animation-localization-mvp-design.md`, is the authoritative
source for scope, architecture, workflows, and external-service constraints.

As implementation begins, keep the two execution planes distinct:

- `apps/control-plane/`: Vite, Preact, and TypeScript UI/API for Cloudflare Workers.
- `apps/processing-agent/`: Python services running media workflows on OCI ARM64.
- `infra/`: Wrangler, D1 migrations, Tunnel, Nginx, and deployment configuration.
- `tests/`: cross-service integration and media fixture tests.
- `assets/`: non-secret test media, subtitle fixtures, and watermark samples.

Update the specification when an architectural decision changes.

## Build, Test, and Development Commands

No build scripts or package manifests exist yet. Add commands with the first
implementation and expose a predictable root-level interface.
Recommended commands are:

- `npm run dev`: run the control plane locally.
- `npm run lint`: check TypeScript formatting and static analysis.
- `npm test`: run control-plane unit tests.
- `pytest`: run processing-agent tests.
- `wrangler dev`: exercise Worker bindings and D1 locally.

Document new prerequisites and environment setup in `README.md`; do not rely on
machine-specific global tools.

## Coding Style & Naming Conventions

Use two-space indentation for TypeScript, JSON, and YAML; use four spaces for
Python. Prefer explicit TypeScript types at service boundaries and type hints in
Python. Name TypeScript components and types in `PascalCase`, functions and
variables in `camelCase`, Python modules and functions in `snake_case`, and
tests after behavior (for example, `sourceAdapter.test.ts` or
`test_source_adapter.py`). Keep source adapters behind the documented
`SourceAdapter` contract.

## Testing Guidelines

Test rights checks, state transitions, retries, deduplication, and schema
validation as core behavior. Mock paid/external APIs in unit tests. Gate
Bilibili, Douyin, ARM64, FFmpeg, upload, and unpublished-post behavior behind
explicit integration or PoC tests. Never publish test uploads publicly. Add a
regression test with every bug fix.

## Commit & Pull Request Guidelines

There is no Git history to infer an existing convention. Use short,
imperative commit subjects, optionally with Conventional Commit prefixes, such
as `feat: add discovery job lease`. Keep commits focused. Pull requests should
describe the user-visible effect, tests run, configuration or migration changes,
and relevant design sections. Link issues and include screenshots for UI work
or sample output for media-pipeline changes.

## Security & Configuration

Never commit tokens, cookies, permission evidence, creator media, or production
credentials. Use local environment files and platform secret stores, provide
sanitized `.env.example` entries, and preserve the specification’s requirement
for explicit rights verification and separate publication approval.
