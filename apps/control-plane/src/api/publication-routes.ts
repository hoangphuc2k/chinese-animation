import { Hono } from "hono";

import { ApprovePublishRequest } from "../../../../packages/contracts/src/publishing";
import type { AuthContext } from "../middleware/access-auth";
import { Action, authorize } from "../middleware/authorize";
import { createRepositories } from "../repositories";
import { IdempotencyConflictError, IdempotencyService, createIdempotencyStore } from "../services/idempotency-service";
import { ApprovalMismatchError, ApprovalService } from "../services/approval-service";
import type { BrowserWorkflowRoutesOptions } from "./project-routes";

type BrowserWorkflowVariables = { Variables: AuthContext };

function problem(code: string, message: string, requestId: string, status: number): Response {
  return new Response(JSON.stringify({ error: { code, message, requestId, retryable: false } }), {
    headers: { "content-type": "application/problem+json" },
    status,
  });
}

async function requestJson(request: Request): Promise<unknown> {
  try {
    return await request.json();
  } catch {
    return undefined;
  }
}

export function createPublicationRoutes(options: BrowserWorkflowRoutesOptions): Hono<BrowserWorkflowVariables> {
  const routes = new Hono<BrowserWorkflowVariables>();
  const repositories = createRepositories(options.database);
  const ids = options.newId ?? (() => crypto.randomUUID());
  const nowUtc = options.nowUtc ?? (() => new Date().toISOString());
  const requestId = options.requestId ?? (() => crypto.randomUUID());
  const idempotency = new IdempotencyService(createIdempotencyStore(repositories.idempotency), ids, nowUtc);
  const approvals = new ApprovalService(options.database, ids);

  routes.post("/jobs/:jobId/publish-approval", authorize(Action.ApprovePublish), async (context) => {
    const body = ApprovePublishRequest.safeParse(await requestJson(context.req.raw));
    if (!body.success || body.data.jobId !== context.req.param("jobId")) {
      return problem("VALIDATION_FAILED", "Invalid publish approval request.", requestId(), 400);
    }
    const identity = context.get("auth");
    if (!identity || identity.kind !== "browser") return problem("FORBIDDEN", "Browser identity is required.", requestId(), 403);
    const idempotencyKey = context.req.header("idempotency-key");
    if (!idempotencyKey) return problem("VALIDATION_FAILED", "Idempotency-Key is required.", requestId(), 400);
    const jobId = context.req.param("jobId");
    if (!(await repositories.jobs.get(jobId))) return problem("NOT_FOUND", "Job was not found.", requestId(), 404);
    try {
      const result = await idempotency.execute(
        { actorId: identity.actorId, body: body.data, idempotencyKey, operation: "APPROVE_PUBLISH", route: context.req.path },
        async () => {
          const approved = await approvals.approvePublish({
            actorId: identity.actorId,
            destinations: body.data.destinations,
            jobId,
            metadataHash: body.data.metadataHash,
            nowUtc: nowUtc(),
            renderId: body.data.renderId,
            renderVersion: body.data.renderVersion,
            requestId: requestId(),
            scheduleAtUtc: body.data.scheduleAtUtc,
          });
          return {
            body: {
              approvalId: approved.approvalId,
              destinations: body.data.destinations,
              jobId,
              metadataHash: body.data.metadataHash,
              renderHash: approved.renderHash,
              renderId: body.data.renderId,
              renderVersion: body.data.renderVersion,
            },
            status: 202,
          };
        },
      );
      return context.json(result.body, result.status as 202);
    } catch (error) {
      if (error instanceof IdempotencyConflictError || error instanceof ApprovalMismatchError) {
        return problem("CONFLICT", error.message, requestId(), 409);
      }
      throw error;
    }
  });

  return routes;
}
