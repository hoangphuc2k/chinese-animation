import { Hono, type MiddlewareHandler } from "hono";
import { z } from "zod";

import { CreateImportRequest } from "../../../../packages/contracts/src/ingest";
import { JobDetailResponse } from "../../../../packages/contracts/src/jobs";
import { ProcessingState } from "../../../../packages/contracts/src/states";
import type { AuthContext } from "../middleware/access-auth";
import { Action, authorize } from "../middleware/authorize";
import type { CsrfTokenService } from "../middleware/csrf";
import { createRepositories } from "../repositories";
import type { JobEventRow } from "../repositories/job-events";
import type { ProjectRow } from "../repositories/projects";
import type { SqlDatabase } from "../repositories/sql";
import { IdempotencyConflictError, IdempotencyService, createIdempotencyStore } from "../services/idempotency-service";
import {
  ActiveImportExistsError,
  JobService,
  RetryNotAllowedError,
  SourceRightsRevokedError,
  StaleWorkflowError,
  type WorkflowDatabase,
} from "../services/job-service";
import { createScriptRoutes } from "./script-routes";
import { createRenderRoutes } from "./render-routes";
import { createPublicationRoutes } from "./publication-routes";

const CreateProjectRequest = z.object({ name: z.string().trim().min(1).max(200) }).strict();
const ProjectListQuery = z
  .object({
    cursor: z.string().min(1).optional(),
    limit: z.coerce.number().int().min(1).max(100).default(25),
  })
  .strict();
const EventListQuery = ProjectListQuery;
const JobTransitionRequest = z
  .object({ expectedState: ProcessingState, expectedVersion: z.number().int().positive() })
  .strict();

type BrowserWorkflowVariables = { Variables: AuthContext };

export interface BrowserWorkflowRoutesOptions {
  authenticate: MiddlewareHandler<BrowserWorkflowVariables>;
  csrf: MiddlewareHandler<BrowserWorkflowVariables>;
  csrfTokens?: CsrfTokenService;
  database: SqlDatabase & WorkflowDatabase;
  newId?: () => string;
  nowUtc?: () => string;
  requestId?: () => string;
}

interface ProjectCursor {
  id: string;
  updatedAtUtc: string;
}

interface EventCursor {
  createdAtUtc: string;
  id: string;
}

function problem(code: string, message: string, requestId: string, status: number): Response {
  return new Response(JSON.stringify({ error: { code, message, requestId, retryable: false } }), {
    headers: { "content-type": "application/problem+json" },
    status,
  });
}

function encodeCursor(cursor: ProjectCursor): string {
  return btoa(JSON.stringify(cursor)).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function decodeCursor(value: string | undefined): ProjectCursor | undefined {
  if (!value) return undefined;
  if (!/^[A-Za-z0-9_-]+$/.test(value)) return undefined;
  try {
    const padded = value.replaceAll("-", "+").replaceAll("_", "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
    const parsed: unknown = JSON.parse(atob(padded));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return undefined;
    const { id, updatedAtUtc } = parsed as Record<string, unknown>;
    return typeof id === "string" && typeof updatedAtUtc === "string" ? { id, updatedAtUtc } : undefined;
  } catch {
    return undefined;
  }
}

function decodeEventCursor(value: string | undefined): EventCursor | undefined {
  if (!value) return undefined;
  if (!/^[A-Za-z0-9_-]+$/.test(value)) return undefined;
  try {
    const padded = value.replaceAll("-", "+").replaceAll("_", "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
    const parsed: unknown = JSON.parse(atob(padded));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return undefined;
    const { createdAtUtc, id } = parsed as Record<string, unknown>;
    return typeof createdAtUtc === "string" && typeof id === "string" ? { createdAtUtc, id } : undefined;
  } catch {
    return undefined;
  }
}

function encodeEventCursor(cursor: EventCursor): string {
  return btoa(JSON.stringify(cursor)).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function projectResponse(row: ProjectRow) {
  return {
    createdAtUtc: row.created_at,
    id: row.id,
    name: row.name,
    status: row.status,
    updatedAtUtc: row.updated_at,
  };
}

function eventResponse(row: JobEventRow) {
  return {
    createdAtUtc: row.created_at,
    details: JSON.parse(row.details_json) as unknown,
    eventId: row.id,
    eventType: row.event_type,
    nextState: row.next_state,
    previousState: row.previous_state,
  };
}

function externalIdForImport(locator: z.infer<typeof CreateImportRequest>["locator"]): string {
  return locator.kind === "CREATOR_HTTPS_URL" ? locator.url : locator.inboxFileId;
}

async function requestJson(request: Request): Promise<unknown> {
  try {
    return await request.json();
  } catch {
    return undefined;
  }
}

export function createBrowserWorkflowRoutes(options: BrowserWorkflowRoutesOptions): Hono<BrowserWorkflowVariables> {
  const routes = new Hono<BrowserWorkflowVariables>();
  const repositories = createRepositories(options.database);
  const ids = options.newId ?? (() => crypto.randomUUID());
  const nowUtc = options.nowUtc ?? (() => new Date().toISOString());
  const requestId = options.requestId ?? (() => crypto.randomUUID());
  const idempotency = new IdempotencyService(createIdempotencyStore(repositories.idempotency), undefined, nowUtc);
  const jobs = new JobService(options.database, ids);

  routes.use("*", options.authenticate);
  routes.use("*", options.csrf);

  routes.get("/csrf-token", async (context) => {
    const identity = context.get("auth");
    if (!identity || identity.kind !== "browser" || !options.csrfTokens) {
      return problem("FORBIDDEN", "Browser identity is required.", requestId(), 403);
    }
    return context.json({ csrfToken: await options.csrfTokens.issue(identity.actorId) });
  });

  routes.post("/projects", authorize(Action.CreateImport), async (context) => {
    const body = CreateProjectRequest.safeParse(await requestJson(context.req.raw));
    if (!body.success) return problem("VALIDATION_FAILED", "Invalid project request.", requestId(), 400);
    const identity = context.get("auth");
    if (!identity || identity.kind !== "browser") return problem("FORBIDDEN", "Browser identity is required.", requestId(), 403);
    const idempotencyKey = context.req.header("idempotency-key");
    if (!idempotencyKey) return problem("VALIDATION_FAILED", "Idempotency-Key is required.", requestId(), 400);

    try {
      const result = await idempotency.execute(
        {
          actorId: identity.actorId,
          body: body.data,
          idempotencyKey,
          operation: "CREATE_PROJECT",
          route: "/projects",
        },
        async () => {
          const now = nowUtc();
          const project = {
            createdAtUtc: now,
            id: ids(),
            name: body.data.name,
            status: "ACTIVE",
            updatedAtUtc: now,
          };
          await repositories.projects.create(project);
          return { body: project, status: 201 };
        },
      );
      return context.json(result.body, result.status as 201);
    } catch (error) {
      if (error instanceof IdempotencyConflictError) return problem("CONFLICT", error.message, requestId(), 409);
      throw error;
    }
  });

  routes.get("/projects", async (context) => {
    const query = ProjectListQuery.safeParse(context.req.query());
    const cursor = query.success ? decodeCursor(query.data.cursor) : undefined;
    if (!query.success || (query.data.cursor && !cursor)) {
      return problem("VALIDATION_FAILED", "Invalid project list query.", requestId(), 400);
    }
    const rows = await repositories.projects.list({ cursor, limit: query.data.limit + 1 });
    const page = rows.slice(0, query.data.limit);
    const last = page.at(-1);
    return context.json({
      items: page.map(projectResponse),
      nextCursor: rows.length > query.data.limit && last ? encodeCursor({ id: last.id, updatedAtUtc: last.updated_at }) : null,
    });
  });

  routes.get("/projects/:projectId", async (context) => {
    const project = await repositories.projects.get(context.req.param("projectId"));
    if (!project) return problem("NOT_FOUND", "Project was not found.", requestId(), 404);
    return context.json(projectResponse(project));
  });

  routes.post("/projects/:projectId/imports", authorize(Action.CreateImport), async (context) => {
    const body = CreateImportRequest.safeParse(await requestJson(context.req.raw));
    if (!body.success) return problem("VALIDATION_FAILED", "Invalid import request.", requestId(), 400);
    const identity = context.get("auth");
    if (!identity || identity.kind !== "browser") return problem("FORBIDDEN", "Browser identity is required.", requestId(), 403);
    const idempotencyKey = context.req.header("idempotency-key");
    if (!idempotencyKey) return problem("VALIDATION_FAILED", "Idempotency-Key is required.", requestId(), 400);
    const projectId = context.req.param("projectId");
    if (!(await repositories.projects.get(projectId))) return problem("NOT_FOUND", "Project was not found.", requestId(), 404);

    try {
      const result = await idempotency.execute(
        {
          actorId: identity.actorId,
          body: body.data,
          idempotencyKey,
          operation: "CREATE_IMPORT",
          route: context.req.path,
        },
        async () => {
          const sourceAssetId = ids();
          const jobId = ids();
          const created = await jobs.createImport({
            actorId: identity.actorId,
            actorType: identity.role,
            allowedProcessors: body.data.allowedProcessors,
            jobId,
            nowUtc: nowUtc(),
            projectId,
            requestId: requestId(),
            rightsEvidence: body.data.rightsEvidence,
            sourceAssetId,
            sourceExternalId: externalIdForImport(body.data.locator),
            sourceMetadataJson: JSON.stringify({ locator: body.data.locator }),
            sourceProvider: body.data.locator.kind,
          });
          return { body: { jobId: created.jobId, sourceVideoId: created.sourceAssetId, state: "QUEUED" }, status: 202 };
        },
      );
      return context.json(result.body, result.status as 202);
    } catch (error) {
      if (error instanceof IdempotencyConflictError) return problem("CONFLICT", error.message, requestId(), 409);
      if (error instanceof ActiveImportExistsError) return problem("CONFLICT", error.message, requestId(), 409);
      if (error instanceof SourceRightsRevokedError) return problem("RIGHTS_BLOCKED", error.message, requestId(), 409);
      throw error;
    }
  });

  routes.post("/jobs/:jobId/cancel", authorize(Action.RetryCancel), async (context) => {
    const body = JobTransitionRequest.safeParse(await requestJson(context.req.raw));
    if (!body.success) return problem("VALIDATION_FAILED", "Invalid cancellation request.", requestId(), 400);
    const identity = context.get("auth");
    if (!identity || identity.kind !== "browser") return problem("FORBIDDEN", "Browser identity is required.", requestId(), 403);
    const idempotencyKey = context.req.header("idempotency-key");
    if (!idempotencyKey) return problem("VALIDATION_FAILED", "Idempotency-Key is required.", requestId(), 400);
    const jobId = context.req.param("jobId");
    try {
      const result = await idempotency.execute(
        {
          actorId: identity.actorId,
          body: body.data,
          idempotencyKey,
          operation: "CANCEL_JOB",
          route: context.req.path,
        },
        async () => {
          await jobs.transition({
            actorId: identity.actorId,
            actorType: identity.role,
            eventId: ids(),
            expectedState: body.data.expectedState,
            expectedVersion: body.data.expectedVersion,
            jobId,
            nextState: "CANCELLED",
            nowUtc: nowUtc(),
            operation: "CANCELLED",
            requestId: requestId(),
          });
          return { body: { jobId, state: "CANCELLED", version: body.data.expectedVersion + 1 }, status: 202 };
        },
      );
      return context.json(result.body, result.status as 202);
    } catch (error) {
      if (error instanceof IdempotencyConflictError || error instanceof StaleWorkflowError) {
        return problem("CONFLICT", error.message, requestId(), 409);
      }
      throw error;
    }
  });

  routes.post("/jobs/:jobId/retry", authorize(Action.RetryCancel), async (context) => {
    const body = JobTransitionRequest.safeParse(await requestJson(context.req.raw));
    if (!body.success) return problem("VALIDATION_FAILED", "Invalid retry request.", requestId(), 400);
    const identity = context.get("auth");
    if (!identity || identity.kind !== "browser") return problem("FORBIDDEN", "Browser identity is required.", requestId(), 403);
    const idempotencyKey = context.req.header("idempotency-key");
    if (!idempotencyKey) return problem("VALIDATION_FAILED", "Idempotency-Key is required.", requestId(), 400);
    const jobId = context.req.param("jobId");
    try {
      const result = await idempotency.execute(
        {
          actorId: identity.actorId,
          body: body.data,
          idempotencyKey,
          operation: "RETRY_JOB",
          route: context.req.path,
        },
        async () => {
          await jobs.retry({
            actorId: identity.actorId,
            actorType: identity.role,
            eventId: ids(),
            expectedState: body.data.expectedState,
            expectedVersion: body.data.expectedVersion,
            jobId,
            nowUtc: nowUtc(),
            operation: "RETRIED",
            requestId: requestId(),
          });
          return { body: { jobId, state: "QUEUED", version: body.data.expectedVersion + 1 }, status: 202 };
        },
      );
      return context.json(result.body, result.status as 202);
    } catch (error) {
      if (error instanceof IdempotencyConflictError || error instanceof RetryNotAllowedError || error instanceof StaleWorkflowError) {
        return problem("CONFLICT", error.message, requestId(), 409);
      }
      throw error;
    }
  });

  routes.get("/jobs/:jobId", async (context) => {
    const job = await repositories.jobs.getDetail(context.req.param("jobId"));
    if (!job) return problem("NOT_FOUND", "Job was not found.", requestId(), 404);
    return context.json(
      JobDetailResponse.parse({
        jobId: job.id,
        jobType: job.job_type,
        lastEventType: job.last_event_type,
        latestScript:
          job.latest_script_version !== null && job.latest_script_hash !== null
            ? { contentHash: job.latest_script_hash, version: job.latest_script_version }
            : null,
        retentionDeadlineUtc: job.retention_deadline ? new Date(job.retention_deadline).toISOString() : null,
        state: job.status,
        step: job.step,
        version: job.version,
      }),
    );
  });

  routes.get("/jobs/:jobId/events", async (context) => {
    const query = EventListQuery.safeParse(context.req.query());
    const cursor = query.success ? decodeEventCursor(query.data.cursor) : undefined;
    if (!query.success || (query.data.cursor && !cursor)) {
      return problem("VALIDATION_FAILED", "Invalid event timeline query.", requestId(), 400);
    }
    const rows = await repositories.jobEvents.list({ cursor, jobId: context.req.param("jobId"), limit: query.data.limit + 1 });
    const page = rows.slice(0, query.data.limit);
    const last = page.at(-1);
    return context.json({
      items: page.map(eventResponse),
      nextCursor:
        rows.length > query.data.limit && last
          ? encodeEventCursor({ createdAtUtc: last.created_at, id: last.id })
          : null,
    });
  });

  routes.route("/", createScriptRoutes(options));
  routes.route("/", createRenderRoutes(options));
  routes.route("/", createPublicationRoutes(options));

  return routes;
}
