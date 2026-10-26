import { Hono } from "hono";
import { z } from "zod";

import { Sha256 } from "../../../../packages/contracts/src/ids";
import type { AuthContext } from "../middleware/access-auth";
import { Action, authorize } from "../middleware/authorize";
import { createRepositories } from "../repositories";
import { IdempotencyConflictError, IdempotencyService, createIdempotencyStore } from "../services/idempotency-service";
import { ApprovalService } from "../services/approval-service";
import type { BrowserWorkflowRoutesOptions } from "./project-routes";

const CreateRenderRequest = z
  .object({
    byteCount: z.number().int().nonnegative().safe(),
    previewDescriptor: z.string().min(1).max(255).regex(/^[A-Za-z0-9_-]+$/),
    renderHash: Sha256,
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

export function createRenderRoutes(options: BrowserWorkflowRoutesOptions): Hono<BrowserWorkflowVariables> {
  const routes = new Hono<BrowserWorkflowVariables>();
  const repositories = createRepositories(options.database);
  const ids = options.newId ?? (() => crypto.randomUUID());
  const nowUtc = options.nowUtc ?? (() => new Date().toISOString());
  const requestId = options.requestId ?? (() => crypto.randomUUID());
  const idempotency = new IdempotencyService(createIdempotencyStore(repositories.idempotency), undefined, nowUtc);
  const approvals = new ApprovalService(options.database, ids);

  routes.post("/jobs/:jobId/renders", authorize(Action.Render), async (context) => {
    const body = CreateRenderRequest.safeParse(await requestJson(context.req.raw));
    if (!body.success) return problem("VALIDATION_FAILED", "Invalid render request.", requestId(), 400);
    const identity = context.get("auth");
    if (!identity || identity.kind !== "browser") return problem("FORBIDDEN", "Browser identity is required.", requestId(), 403);
    const idempotencyKey = context.req.header("idempotency-key");
    if (!idempotencyKey) return problem("VALIDATION_FAILED", "Idempotency-Key is required.", requestId(), 400);
    const jobId = context.req.param("jobId");
    if (!(await repositories.jobs.get(jobId))) return problem("NOT_FOUND", "Job was not found.", requestId(), 404);
    try {
      const result = await idempotency.execute(
        { actorId: identity.actorId, body: body.data, idempotencyKey, operation: "CREATE_RENDER", route: context.req.path },
        async () => {
          const renderId = ids();
          const version = await approvals.saveRender({
            actorId: identity.actorId,
            artifactRef: body.data.previewDescriptor,
            byteCount: body.data.byteCount,
            jobId,
            nowUtc: nowUtc(),
            renderHash: body.data.renderHash,
            renderId,
            requestId: requestId(),
          });
          return {
            body: {
              byteCount: body.data.byteCount,
              id: renderId,
              jobId,
              preview: { descriptor: body.data.previewDescriptor },
              renderHash: body.data.renderHash,
              version,
            },
            status: 201,
          };
        },
      );
      return context.json(result.body, result.status as 201);
    } catch (error) {
      if (error instanceof IdempotencyConflictError) return problem("CONFLICT", error.message, requestId(), 409);
      throw error;
    }
  });

  routes.get("/jobs/:jobId/renders", async (context) => {
    const query = ListQuery.safeParse(context.req.query());
    if (!query.success) return problem("VALIDATION_FAILED", "Invalid render list query.", requestId(), 400);
    const rows = await repositories.renderVersions.list({ cursor: query.data.cursor, jobId: context.req.param("jobId"), limit: query.data.limit + 1 });
    const page = rows.slice(0, query.data.limit);
    const last = page.at(-1);
    return context.json({
      items: page.map((row) => ({
        byteCount: row.byte_count,
        createdAtUtc: row.created_at,
        id: row.id,
        preview: { descriptor: row.artifact_ref },
        renderHash: row.render_hash,
        version: row.version,
      })),
      nextCursor: rows.length > query.data.limit && last ? String(last.version) : null,
    });
  });

  return routes;
}
