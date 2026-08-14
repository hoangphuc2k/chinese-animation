import type { MiddlewareHandler } from "hono";
import { describe, expect, it } from "vitest";

import { createAgentRoutes } from "../src/api/agent-routes";
import type { AuthContext } from "../src/middleware/access-auth";
import type { CompleteLeaseInput } from "../src/services/lease-service";

const token = "t".repeat(43);
const hash = "a".repeat(64);

const authenticate: MiddlewareHandler<{ Variables: AuthContext }> = async (context, next) => {
  context.set("auth", { actorId: "agent-a", kind: "agent", role: "agent" });
  await next();
};

describe("agent pipeline completion route", () => {
  it("forwards validated pipeline artifacts to the lease service", async () => {
    let completed: CompleteLeaseInput | undefined;
    const routes = createAgentRoutes({
      authenticate,
      leaseDurationSeconds: 60,
      leaseService: {
        claim: async () => null,
        complete: async (input: CompleteLeaseInput) => {
          completed = input;
        },
        getScriptApproval: async () => null,
        heartbeat: async () => undefined,
      },
      nowUtc: () => "2026-08-07T00:00:00.000Z",
      requestId: () => "00000000-0000-4000-8000-000000000099",
    });

    const response = await routes.request("https://worker.example/jobs/00000000-0000-4000-8000-000000000001/complete", {
      body: JSON.stringify({
        expectedState: "TRANSCRIBING",
        jobVersion: 2,
        leaseToken: token,
        pipelineArtifacts: [
          { artifactRef: "artifact-01", byteCount: 512, kind: "TRANSCRIPT", sha256: hash, toolVersion: "whisper.cpp-1.7.4" },
        ],
        state: "TRANSLATING",
        step: "transcribed",
      }),
      headers: { "content-type": "application/json" },
      method: "POST",
    });

    expect(response.status).toBe(204);
    expect(completed).toMatchObject({
      pipelineArtifacts: [{ artifactRef: "artifact-01", kind: "TRANSCRIPT", sha256: hash }],
    });
  });
});
