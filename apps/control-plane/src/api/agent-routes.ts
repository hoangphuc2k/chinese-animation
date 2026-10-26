import { Hono, type MiddlewareHandler } from "hono";
import { z } from "zod";

import { ClaimJobRequest, ClaimJobResponse, CompleteStepRequest, HeartbeatRequest } from "../../../../packages/contracts/src/jobs";
import { Sha256 } from "../../../../packages/contracts/src/ids";
import type { AuthContext } from "../middleware/access-auth";
import { LeaseInvalidError, type ClaimedLease, type ClaimLeaseInput, type CompleteLeaseInput, type HeartbeatLeaseInput, type ScriptApprovalLookupInput, type ScriptApprovalSnapshot } from "../services/lease-service";

export interface AgentLeaseService {
  claim(input: ClaimLeaseInput): Promise<ClaimedLease | null>;
  complete(input: CompleteLeaseInput): Promise<void>;
  getScriptApproval(input: ScriptApprovalLookupInput): Promise<ScriptApprovalSnapshot | null>;
  heartbeat(input: HeartbeatLeaseInput): Promise<void>;
}

export interface AgentRoutesOptions {
  authenticate: MiddlewareHandler<{ Variables: AuthContext }>;
  leaseDurationSeconds: number;
  leaseService: AgentLeaseService;
  nowUtc?: () => string;
  requestId?: () => string;
}

function problem(code: string, message: string, requestId: string, status: number): Response {
  return new Response(JSON.stringify({ error: { code, message, requestId, retryable: false } }), {
    headers: { "content-type": "application/problem+json" },
    status,
  });
}

function leaseExpiresAt(nowUtc: string, seconds: number): string {
  return new Date(new Date(nowUtc).getTime() + seconds * 1000).toISOString();
}

async function requestJson(request: Request): Promise<unknown> {
  try {
    return await request.json();
  } catch {
    return undefined;
  }
}

export function createAgentRoutes(options: AgentRoutesOptions): Hono<{ Variables: AuthContext }> {
  const routes = new Hono<{ Variables: AuthContext }>();
  const nowUtc = options.nowUtc ?? (() => new Date().toISOString());
  const requestId = options.requestId ?? crypto.randomUUID;

  routes.use("*", options.authenticate);
  routes.use("*", async (context, next) => {
    if (context.get("auth")?.kind !== "agent") return problem("FORBIDDEN", "Agent service identity is required.", requestId(), 403);
    await next();
  });

  routes.post("/jobs/claim", async (context) => {
    const input = ClaimJobRequest.safeParse(await requestJson(context.req.raw));
    if (!input.success) return problem("VALIDATION_FAILED", "Invalid claim request.", requestId(), 400);
    const identity = context.get("auth");
    if (!identity || identity.kind !== "agent") return problem("FORBIDDEN", "Agent service identity is required.", requestId(), 403);
    const now = nowUtc();
    const claimed = await options.leaseService.claim({
      agentId: identity.actorId,
      leaseExpiresAtUtc: leaseExpiresAt(now, options.leaseDurationSeconds),
      nowUtc: now,
      supportedJobTypes: input.data.supportedJobTypes,
      supportedStates: input.data.supportedStates,
    });
    if (!claimed) return new Response(null, { status: 204 });
    const response = ClaimJobResponse.parse({ ...claimed, requestId: requestId() });
    return context.json(response);
  });

  routes.post("/jobs/:jobId/heartbeat", async (context) => {
    const input = HeartbeatRequest.safeParse(await requestJson(context.req.raw));
    if (!input.success) return problem("VALIDATION_FAILED", "Invalid heartbeat request.", requestId(), 400);
    const identity = context.get("auth");
    if (!identity || identity.kind !== "agent") return problem("FORBIDDEN", "Agent service identity is required.", requestId(), 403);
    const now = nowUtc();
    try {
      await options.leaseService.heartbeat({
        agentId: identity.actorId,
        jobId: context.req.param("jobId"),
        jobVersion: input.data.jobVersion,
        leaseExpiresAtUtc: leaseExpiresAt(now, options.leaseDurationSeconds),
        leaseToken: input.data.leaseToken,
        nowUtc: now,
      });
      return new Response(null, { status: 204 });
    } catch (error) {
      if (error instanceof LeaseInvalidError) return problem("LEASE_INVALID", error.message, requestId(), 409);
      throw error;
    }
  });

  routes.get("/jobs/:jobId/script-approval", async (context) => {
    const query = z
      .object({ contentHash: Sha256, scriptVersion: z.coerce.number().int().positive() })
      .strict()
      .safeParse(context.req.query());
    if (!query.success) return problem("VALIDATION_FAILED", "Invalid script approval lookup.", requestId(), 400);
    const approval = await options.leaseService.getScriptApproval({
      contentHash: query.data.contentHash,
      jobId: context.req.param("jobId"),
      scriptVersion: query.data.scriptVersion,
    });
    if (!approval) return problem("NOT_FOUND", "No matching active script approval exists.", requestId(), 404);
    return context.json(approval);
  });

  routes.post("/jobs/:jobId/complete", async (context) => {
    const input = CompleteStepRequest.safeParse(await requestJson(context.req.raw));
    if (!input.success) return problem("VALIDATION_FAILED", "Invalid completion request.", requestId(), 400);
    const identity = context.get("auth");
    if (!identity || identity.kind !== "agent") return problem("FORBIDDEN", "Agent service identity is required.", requestId(), 403);
    try {
      const completion: CompleteLeaseInput = {
        agentId: identity.actorId,
        expectedState: input.data.expectedState,
        jobId: context.req.param("jobId"),
        jobVersion: input.data.jobVersion,
        leaseToken: input.data.leaseToken,
        nextState: input.data.state,
        nowUtc: nowUtc(),
        step: input.data.step,
      };
      if (input.data.failureCode) completion.failureCode = input.data.failureCode;
      if (input.data.ingestResult) completion.ingestResult = input.data.ingestResult;
      if (input.data.pipelineArtifacts) completion.pipelineArtifacts = input.data.pipelineArtifacts;
      if (input.data.preview) completion.preview = input.data.preview;
      await options.leaseService.complete(completion);
      return new Response(null, { status: 204 });
    } catch (error) {
      if (error instanceof LeaseInvalidError) return problem("LEASE_INVALID", error.message, requestId(), 409);
      throw error;
    }
  });

  return routes;
}
