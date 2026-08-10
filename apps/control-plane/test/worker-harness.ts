import type { WorkerEnv } from "../src/env";

export function createTestEnv(overrides: Partial<WorkerEnv> = {}): WorkerEnv {
  return {
    ACCESS_AUDIENCE: "test-audience",
    ACCESS_ISSUER: "https://access.example.test",
    ADMIN_EMAIL: "admin@example.test",
    AGENT_ACCESS_AUDIENCE: "test-agent-audience",
    AGENT_SERVICE_SUBJECT: "test-agent-subject",
    CONTROL_PLANE_DB: {} as D1Database,
    CSRF_SIGNING_SECRET: "test-csrf-signing-secret",
    DEPLOYMENT_VERSION: "test-build",
    ENVIRONMENT: "development",
    PREVIEW_ORIGIN: "https://preview.example.test",
    ...overrides,
  };
}
