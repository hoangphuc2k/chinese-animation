import { Hono } from "hono";
import { z } from "zod";

import { ApproveScriptRequest } from "../../../../packages/contracts/src/review";
import { Sha256 } from "../../../../packages/contracts/src/ids";
import type { AuthContext } from "../middleware/access-auth";
import { Action, authorize } from "../middleware/authorize";
import { createRepositories } from "../repositories";
import { IdempotencyConflictError, IdempotencyService, createIdempotencyStore } from "../services/idempotency-service";
import { ApprovalMismatchError, ApprovalService } from "../services/approval-service";
import type { BrowserWorkflowRoutesOptions } from "./project-routes";

const ScriptEditRequest = z
  .object({
    contentHash: Sha256,
    metadataHash: Sha256.nullable().optional(),
    transcriptHash: Sha256.nullable().optional(),
    translationHash: Sha256.nullable().optional(),
    voiceSettingsHash: Sha256.nullable().optional(),
  })
  .strict();
const ListQuery = z.object({ cursor: z.coerce.number().int().positive().optional(), limit: z.coerce.number().int().min(1).max(100).default(25) }).strict();

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

export function createScriptRoutes(options: BrowserWorkflowRoutesOptions): Hono<BrowserWorkflowVariables> {
  const routes = new Hono<BrowserWorkflowVariables>();
  const repositories = createRepositories(options.database);
  const ids = options.newId ?? (() => crypto.randomUUID());
  const nowUtc = options.nowUtc ?? (() => new Date().toISOString());
  const requestId = options.requestId ?? (() => crypto.randomUUID());
  const idempotency = new IdempotencyService(createIdempotencyStore(repositories.idempotency), ids, nowUtc);
  const approvals = new ApprovalService(options.database, ids);

  routes.post("/jobs/:jobId/scripts", authorize(Action.EditScript), async (context) => {
    const body = ScriptEditRequest.safeParse(await requestJson(context.req.raw));
    if (!body.success) return problem("VALIDATION_FAILED", "Invalid script edit request.", requestId(), 400);
    const identity = context.get("auth");
    if (!identity || identity.kind !== "browser") return problem("FORBIDDEN", "Browser identity is required.", requestId(), 403);
    const idempotencyKey = context.req.header("idempotency-key");
    if (!idempotencyKey) return problem("VALIDATION_FAILED", "Idempotency-Key is required.", requestId(), 400);
    const jobId = context.req.param("jobId");
    if (!(await repositories.jobs.get(jobId))) return problem("NOT_FOUND", "Job was not found.", requestId(), 404);
    try {
      const result = await idempotency.execute(
        { actorId: identity.actorId, body: body.data, idempotencyKey, operation: "EDIT_SCRIPT", route: context.req.path },
        async () => {
          const scriptId = ids();
          const version = await approvals.saveScriptEdit({
            actorId: identity.actorId,
            contentHash: body.data.contentHash,
            jobId,
            metadataHash: body.data.metadataHash ?? null,
            nowUtc: nowUtc(),
            requestId: requestId(),
            scriptId,
            transcriptHash: body.data.transcriptHash ?? null,
            translationHash: body.data.translationHash ?? null,
            voiceSettingsHash: body.data.voiceSettingsHash ?? null,
          });
          return { body: { contentHash: body.data.contentHash, id: scriptId, jobId, version }, status: 201 };
        },
      );
      return context.json(result.body, result.status as 201);
    } catch (error) {
      if (error instanceof IdempotencyConflictError) return problem("CONFLICT", error.message, requestId(), 409);
      throw error;
    }
  });

  routes.post("/jobs/:jobId/scripts/approve", authorize(Action.ApproveScript), async (context) => {
    const body = ApproveScriptRequest.safeParse(await requestJson(context.req.raw));
    if (!body.success || body.data.jobId !== context.req.param("jobId")) {
      return problem("VALIDATION_FAILED", "Invalid script approval request.", requestId(), 400);
    }
    const identity = context.get("auth");
    if (!identity || identity.kind !== "browser") return problem("FORBIDDEN", "Browser identity is required.", requestId(), 403);
    const idempotencyKey = context.req.header("idempotency-key");
    if (!idempotencyKey) return problem("VALIDATION_FAILED", "Idempotency-Key is required.", requestId(), 400);
    const jobId = context.req.param("jobId");
    try {
      const result = await idempotency.execute(
        { actorId: identity.actorId, body: body.data, idempotencyKey, operation: "APPROVE_SCRIPT", route: context.req.path },
        async () => {
          const approvalId = await approvals.approveScript({
            actorId: identity.actorId,
            contentHash: body.data.contentHash,
            jobId,
            nowUtc: nowUtc(),
            requestId: requestId(),
            scriptVersion: body.data.scriptVersion,
          });
          return {
            body: { approvalId, contentHash: body.data.contentHash, jobId, scriptVersion: body.data.scriptVersion },
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

  routes.get("/jobs/:jobId/scripts", async (context) => {
    const query = ListQuery.safeParse(context.req.query());
    if (!query.success) return problem("VALIDATION_FAILED", "Invalid script list query.", requestId(), 400);
    const rows = await repositories.scriptVersions.list({ cursor: query.data.cursor, jobId: context.req.param("jobId"), limit: query.data.limit + 1 });
    const page = rows.slice(0, query.data.limit);
    const last = page.at(-1);
    return context.json({
      items: page.map((row) => ({
        contentHash: row.content_hash,
        createdAtUtc: row.created_at,
        id: row.id,
        metadataHash: row.metadata_hash,
        transcriptHash: row.transcript_hash,
        translationHash: row.translation_hash,
        version: row.version,
        voiceSettingsHash: row.voice_settings_hash,
      })),
      nextCursor: rows.length > query.data.limit && last ? String(last.version) : null,
    });
  });

  return routes;
}
