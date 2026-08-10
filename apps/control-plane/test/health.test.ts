import { describe, expect, it } from "vitest";

import { app } from "../src/index";
import { createTestEnv } from "./worker-harness";

describe("GET /api/v1/health", () => {
  it("returns the caller request ID and deployment version", async () => {
    const response = await app.request(
      "https://control-plane.test/api/v1/health",
      {
        headers: { "x-request-id": "req-health-123" },
      },
      createTestEnv({ DEPLOYMENT_VERSION: "test-build-42" }),
    );

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      deploymentVersion: "test-build-42",
      requestId: "req-health-123",
      status: "ok",
    });
  });
});

describe("unknown API routes", () => {
  it("returns an RFC 9457 problem response", async () => {
    const response = await app.request(
      "https://control-plane.test/api/v1/unknown",
      undefined,
      createTestEnv({ DEPLOYMENT_VERSION: "test-build-42" }),
    );

    expect(response.status).toBe(404);
    await expect(response.json()).resolves.toEqual({
      detail: "No API route matches this request.",
      instance: "/api/v1/unknown",
      status: 404,
      title: "Not Found",
      type: "https://www.rfc-editor.org/rfc/rfc9457#section-3.1",
    });
  });
});
