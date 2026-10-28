import { Hono } from "hono";
import { describe, expect, it, vi } from "vitest";

import { ImportInput } from "../../../packages/contracts/src/jobs";
import type { AuthContext } from "../src/middleware/access-auth";
import { createAgentRoutes, type AgentLeaseService } from "../src/api/agent-routes";
import { LeaseInvalidError } from "../src/services/lease-service";
import { app as controlPlaneApp } from "../src/index";
import { createTestEnv } from "./worker-harness";

function agentAuth() {
  return async (context: { set: (key: "auth", value: AuthContext["auth"]) => void }, next: () => Promise<void>) => {
    context.set("auth", { actorId: "agent-1", kind: "agent", role: "agent" });
    await next();
  };
}

function browserAuth() {
  return async (context: { set: (key: "auth", value: AuthContext["auth"]) => void }, next: () => Promise<void>) => {
    context.set("auth", { actorId: "admin-1", email: "admin@example.test", kind: "browser", role: "admin" });
    await next();
  };
}

function service(overrides: Partial<AgentLeaseService> = {}): AgentLeaseService {
  return {
    claim: async () => ({
      jobId: "00000000-0000-4000-8000-000000000001",
      jobType: "LOCALIZE",
      jobVersion: 2,
      leaseExpiresAtUtc: "2026-08-04T00:05:00Z",
      leaseToken: "t".repeat(43),
      state: "QUEUED",
    }),
    complete: async () => undefined,
    getScriptApproval: async () => null,
    heartbeat: async () => undefined,
    ...overrides,
  };
}

function app(authenticate: ReturnType<typeof agentAuth>, leaseService = service()) {
  const app = new Hono<{ Variables: AuthContext }>();
  app.route("/api/v1/agent", createAgentRoutes({ authenticate, leaseDurationSeconds: 300, leaseService, nowUtc: () => "2026-08-04T00:00:00Z", requestId: () => "00000000-0000-4000-8000-000000000099" }));
  return app;
}

describe("agent routes", () => {
  it("mounts the agent API behind service authentication in the Worker", async () => {
    const response = await controlPlaneApp.request(
      "https://control-plane.test/api/v1/agent/jobs/claim",
      { method: "POST" },
      createTestEnv(),
    );

    expect(response.status).toBe(401);
  });

  it("returns a raw lease token once to an authenticated agent claim", async () => {
    const response = await app(agentAuth()).request("https://control-plane.test/api/v1/agent/jobs/claim", {
      body: JSON.stringify({ supportedJobTypes: ["LOCALIZE"], supportedStates: ["QUEUED"] }),
      headers: { "content-type": "application/json" },
      method: "POST",
    });

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      jobId: "00000000-0000-4000-8000-000000000001",
      jobType: "LOCALIZE",
      jobVersion: 2,
      leaseExpiresAtUtc: "2026-08-04T00:05:00Z",
      leaseToken: "t".repeat(43),
      requestId: "00000000-0000-4000-8000-000000000099",
      state: "QUEUED",
    });
  });

  it("generates request IDs without detaching crypto.randomUUID, which the Workers runtime rejects", async () => {
    const original = crypto.randomUUID;
    // workerd throws "Illegal invocation" when randomUUID is called without `this === crypto`.
    vi.spyOn(crypto, "randomUUID").mockImplementation(function (this: unknown) {
      if (this !== crypto) throw new TypeError("Illegal invocation");
      return original.call(crypto);
    });
    try {
      const routes = new Hono<{ Variables: AuthContext }>();
      routes.route("/api/v1/agent", createAgentRoutes({ authenticate: agentAuth(), leaseDurationSeconds: 300, leaseService: service() }));

      const response = await routes.request("https://control-plane.test/api/v1/agent/jobs/claim", {
        body: JSON.stringify({ supportedJobTypes: ["LOCALIZE"], supportedStates: ["QUEUED"] }),
        headers: { "content-type": "application/json" },
        method: "POST",
      });

      expect(response.status).toBe(200);
    } finally {
      vi.restoreAllMocks();
    }
  });

  it("binds a compatible IMPORT claim to sanitized ingest input", async () => {
    let claimInput: unknown;
    const response = await app(
      agentAuth(),
      service({
        claim: async (input) => {
          claimInput = input;
          return {
            importInput: ImportInput.parse({
              locator: { inboxFileId: "creator-submission-42", kind: "OCI_INBOX_FILE" },
              rightsEvidenceHash: "a".repeat(64),
              sourceAssetId: "00000000-0000-4000-8000-000000000002",
            }),
            jobId: "00000000-0000-4000-8000-000000000001",
            jobType: "IMPORT",
            jobVersion: 2,
            leaseExpiresAtUtc: "2026-08-04T00:05:00Z",
            leaseToken: "t".repeat(43),
            state: "QUEUED",
          };
        },
      }),
    ).request("https://control-plane.test/api/v1/agent/jobs/claim", {
      body: JSON.stringify({ supportedJobTypes: ["IMPORT"], supportedStates: ["QUEUED", "INGESTING"] }),
      headers: { "content-type": "application/json" },
      method: "POST",
    });

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      importInput: {
        locator: { inboxFileId: "creator-submission-42", kind: "OCI_INBOX_FILE" },
        rightsEvidenceHash: "a".repeat(64),
        sourceAssetId: "00000000-0000-4000-8000-000000000002",
      },
      jobType: "IMPORT",
    });
    expect(claimInput).toMatchObject({
      agentId: "agent-1",
      supportedJobTypes: ["IMPORT"],
      supportedStates: ["QUEUED", "INGESTING"],
    });
  });

  it("rejects a claim that does not declare supported job types", async () => {
    const response = await app(agentAuth()).request("https://control-plane.test/api/v1/agent/jobs/claim", {
      body: JSON.stringify({ supportedJobTypes: [], supportedStates: ["QUEUED"] }),
      headers: { "content-type": "application/json" },
      method: "POST",
    });

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toMatchObject({ error: { code: "VALIDATION_FAILED" } });
  });

  it("requires job version with the heartbeat lease token", async () => {
    const response = await app(agentAuth()).request(
      "https://control-plane.test/api/v1/agent/jobs/00000000-0000-4000-8000-000000000001/heartbeat",
      { body: JSON.stringify({ leaseToken: "t".repeat(43) }), headers: { "content-type": "application/json" }, method: "POST" },
    );

    expect(response.status).toBe(400);
  });

  it("returns only an exact active script approval to an authenticated agent", async () => {
    const leaseService = service() as AgentLeaseService & {
      getScriptApproval(input: { contentHash: string; jobId: string; scriptVersion: number }): Promise<unknown>;
    };
    leaseService.getScriptApproval = async (input) => ({ approved: true, ...input });
    const response = await app(agentAuth(), leaseService).request(
      `https://control-plane.test/api/v1/agent/jobs/00000000-0000-4000-8000-000000000001/script-approval?scriptVersion=2&contentHash=${"a".repeat(64)}`,
    );

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      approved: true,
      contentHash: "a".repeat(64),
      jobId: "00000000-0000-4000-8000-000000000001",
      scriptVersion: 2,
    });
  });

  it("maps an invalid completion lease to LEASE_INVALID", async () => {
    const response = await app(
      agentAuth(),
      service({ complete: async () => Promise.reject(new LeaseInvalidError()) }),
    ).request("https://control-plane.test/api/v1/agent/jobs/00000000-0000-4000-8000-000000000001/complete", {
      body: JSON.stringify({
        expectedState: "QUEUED",
        jobVersion: 2,
        leaseToken: "t".repeat(43),
        state: "INGESTING",
        step: "ingest",
      }),
      headers: { "content-type": "application/json" },
      method: "POST",
    });

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toMatchObject({ error: { code: "LEASE_INVALID" } });
  });

  it("rejects a browser identity even when it reaches the agent route group", async () => {
    const response = await app(browserAuth() as ReturnType<typeof agentAuth>).request(
      "https://control-plane.test/api/v1/agent/jobs/claim",
      { method: "POST" },
    );

    expect(response.status).toBe(403);
  });
});
