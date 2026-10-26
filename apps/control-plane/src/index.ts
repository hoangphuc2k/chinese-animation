import { Hono, type Context } from "hono";

import { createAgentRoutes } from "./api/agent-routes";
import { createBrowserWorkflowRoutes } from "./api/project-routes";
import type { WorkerEnv } from "./env";
import { createAccessAuthenticator, createAgentAuthenticator } from "./middleware/access-auth";
import { createCsrfProtector, createCsrfTokenService, type CsrfTokenService } from "./middleware/csrf";
import { createStructuredLogger } from "./observability/logger";
import { RetentionService } from "./scheduled/retention";
import { LeaseService, type LeaseDatabase } from "./services/lease-service";

interface ProblemDetails {
  detail: string;
  instance: string;
  status: number;
  title: string;
  type: string;
}

const problemType = "https://www.rfc-editor.org/rfc/rfc9457#section-3.1";

function problemResponse(problem: ProblemDetails): Response {
  return new Response(JSON.stringify(problem), {
    headers: { "content-type": "application/problem+json" },
    status: problem.status,
  });
}

export const app = new Hono<{ Bindings: WorkerEnv }>();
const agentAuthenticators = new Map<string, ReturnType<typeof createAgentAuthenticator>>();
const browserAuthenticators = new Map<string, ReturnType<typeof createAccessAuthenticator>>();
const csrfProtectors = new Map<string, WorkerCsrf>();

function agentAuthenticator(env: WorkerEnv): ReturnType<typeof createAgentAuthenticator> {
  const key = `${env.ACCESS_ISSUER}:${env.AGENT_ACCESS_AUDIENCE}:${env.AGENT_SERVICE_CLIENT_ID}`;
  let authenticator = agentAuthenticators.get(key);
  if (!authenticator) {
    authenticator = createAgentAuthenticator({
      audience: env.AGENT_ACCESS_AUDIENCE,
      issuer: env.ACCESS_ISSUER,
      serviceClientId: env.AGENT_SERVICE_CLIENT_ID,
    });
    agentAuthenticators.set(key, authenticator);
  }
  return authenticator;
}

function browserAuthenticator(env: WorkerEnv): ReturnType<typeof createAccessAuthenticator> {
  const key = `${env.ACCESS_ISSUER}:${env.ACCESS_AUDIENCE}:${env.ADMIN_EMAIL}`;
  let authenticator = browserAuthenticators.get(key);
  if (!authenticator) {
    authenticator = createAccessAuthenticator({
      allowedEmail: env.ADMIN_EMAIL,
      audience: env.ACCESS_AUDIENCE,
      issuer: env.ACCESS_ISSUER,
    });
    browserAuthenticators.set(key, authenticator);
  }
  return authenticator;
}

interface WorkerCsrf {
  protector: ReturnType<typeof createCsrfProtector>;
  tokens: CsrfTokenService;
}

export function workerCsrf(env: WorkerEnv): WorkerCsrf {
  const key = `${env.ADMIN_ORIGIN}:${env.CSRF_SIGNING_SECRET}`;
  let csrf = csrfProtectors.get(key);
  if (!csrf) {
    const tokens = createCsrfTokenService({ secret: env.CSRF_SIGNING_SECRET });
    csrf = { protector: createCsrfProtector({ origin: env.ADMIN_ORIGIN, tokens }), tokens };
    csrfProtectors.set(key, csrf);
  }
  return csrf;
}

app.get("/api/v1/health", (context) => {
  const requestId = context.req.header("x-request-id") ?? crypto.randomUUID();

  return context.json({
    deploymentVersion: context.env.DEPLOYMENT_VERSION,
    requestId,
    status: "ok",
  });
});

app.all("/api/v1/agent/*", async (context) => {
  const routes = createAgentRoutes({
    authenticate: agentAuthenticator(context.env),
    leaseDurationSeconds: 300,
    leaseService: new LeaseService(context.env.CONTROL_PLANE_DB as unknown as LeaseDatabase),
  });
  const url = new URL(context.req.url);
  url.pathname = url.pathname.slice("/api/v1/agent".length) || "/";
  return routes.fetch(new Request(url, context.req.raw));
});

async function browserWorkflowResponse(context: Context<{ Bindings: WorkerEnv }>): Promise<Response> {
  const csrf = workerCsrf(context.env);
  const routes = createBrowserWorkflowRoutes({
    authenticate: browserAuthenticator(context.env),
    csrf: csrf.protector,
    csrfTokens: csrf.tokens,
    database: context.env.CONTROL_PLANE_DB,
  });
  const url = new URL(context.req.url);
  url.pathname = url.pathname.slice("/api/v1".length) || "/";
  return routes.fetch(new Request(url, context.req.raw));
}

for (const path of [
  "/api/v1/csrf-token",
  "/api/v1/projects",
  "/api/v1/projects/:projectId",
  "/api/v1/projects/:projectId/imports",
  "/api/v1/jobs/:jobId/cancel",
  "/api/v1/jobs/:jobId/retry",
  "/api/v1/jobs/:jobId/events",
  "/api/v1/jobs/:jobId/scripts",
  "/api/v1/jobs/:jobId/scripts/approve",
  "/api/v1/jobs/:jobId/renders",
  "/api/v1/jobs/:jobId/publish-approval",
]) {
  app.all(path, browserWorkflowResponse);
}

app.notFound((context) =>
  problemResponse({
    detail: "No API route matches this request.",
    instance: context.req.path,
    status: 404,
    title: "Not Found",
    type: problemType,
  }),
);

export default {
  fetch(request, env, executionContext) {
    return app.fetch(request, env, executionContext);
  },
  async scheduled(controller, env) {
    const requestId = crypto.randomUUID();
    const nowUtc = new Date(controller.scheduledTime).toISOString();
    const startedAt = Date.now();
    const logger = createStructuredLogger();

    try {
      await new RetentionService(env.CONTROL_PLANE_DB, undefined, logger).run({ nowUtc, requestId });
      logger.info({ durationMs: Date.now() - startedAt, requestId, stage: "RETENTION_SCHEDULE" });
    } catch {
      logger.error({
        durationMs: Date.now() - startedAt,
        errorCode: "RETENTION_SCHEDULE_FAILED",
        requestId,
        stage: "RETENTION_SCHEDULE",
      });
      throw new Error("Retention schedule failed");
    }
  },
} satisfies ExportedHandler<WorkerEnv>;
