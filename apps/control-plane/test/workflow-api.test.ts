import { afterEach, describe, expect, it } from "vitest";

import { createBrowserWorkflowRoutes } from "../src/api/project-routes";
import { app } from "../src/index";
import type { AuthContext } from "../src/middleware/access-auth";
import { createCsrfTokenService } from "../src/middleware/csrf";
import { createMigratedD1, type SqliteD1 } from "./sqlite-d1";
import { createTestEnv } from "./worker-harness";

const projectId = "00000000-0000-4000-8000-000000000001";
const projectIdTwo = "00000000-0000-4000-8000-000000000002";
const databases: SqliteD1[] = [];

function createDatabase(): SqliteD1 {
  const database = createMigratedD1();
  databases.push(database);
  return database;
}

function authenticatedBrowser(role: "admin" | "operator" | "reviewer" = "operator") {
  return async (context: { set(key: "auth", value: AuthContext["auth"]): void }, next: () => Promise<void>) => {
    context.set("auth", {
      actorId: "operator-1",
      email: "admin@example.test",
      kind: "browser",
      role,
    });
    await next();
  };
}

function noCsrf() {
  return async (_context: unknown, next: () => Promise<void>) => {
    await next();
  };
}

function createRoutes(
  ids = [projectId, projectIdTwo],
  role: "admin" | "operator" | "reviewer" = "operator",
  database = createDatabase(),
) {
  return createBrowserWorkflowRoutes({
    authenticate: authenticatedBrowser(role),
    csrf: noCsrf(),
    database,
    newId: () => ids.shift() ?? crypto.randomUUID(),
    nowUtc: () => "2026-08-04T00:00:00.000Z",
    requestId: () => "00000000-0000-4000-8000-000000000099",
  });
}

afterEach(() => {
  for (const database of databases.splice(0)) database.close();
});

describe("browser workflow API", () => {
  it("mounts browser workflow routes behind the Access authenticator", async () => {
    const response = await app.fetch(new Request("https://control.example.test/api/v1/projects"), createTestEnv());

    expect(response.status).toBe(401);
  });

  it("issues a CSRF token bound to the verified browser identity", async () => {
    const tokens = createCsrfTokenService({ secret: "test-csrf-secret" });
    const routes = createBrowserWorkflowRoutes({
      authenticate: authenticatedBrowser("admin"),
      csrf: noCsrf(),
      csrfTokens: tokens,
      database: createDatabase(),
    });

    const response = await routes.request("https://control.example.test/csrf-token");

    expect(response.status).toBe(200);
    const body = (await response.json()) as { csrfToken: string };
    await expect(tokens.verify(body.csrfToken, "operator-1")).resolves.toBe(true);
  });

  it("creates projects and exposes stable, bounded project pagination and detail", async () => {
    const routes = createRoutes();

    const created = await routes.request("https://control.example.test/projects", {
      body: JSON.stringify({ name: "First project" }),
      headers: { "content-type": "application/json", "idempotency-key": "create-project-1" },
      method: "POST",
    });

    expect(created.status).toBe(201);
    await expect(created.json()).resolves.toEqual({
      createdAtUtc: "2026-08-04T00:00:00.000Z",
      id: projectId,
      name: "First project",
      status: "ACTIVE",
      updatedAtUtc: "2026-08-04T00:00:00.000Z",
    });

    const listing = await routes.request("https://control.example.test/projects?limit=1");
    expect(listing.status).toBe(200);
    await expect(listing.json()).resolves.toEqual({
      items: [
        {
          createdAtUtc: "2026-08-04T00:00:00.000Z",
          id: projectId,
          name: "First project",
          status: "ACTIVE",
          updatedAtUtc: "2026-08-04T00:00:00.000Z",
        },
      ],
      nextCursor: null,
    });

    const detail = await routes.request(`https://control.example.test/projects/${projectId}`);
    expect(detail.status).toBe(200);
    await expect(detail.json()).resolves.toEqual({
      createdAtUtc: "2026-08-04T00:00:00.000Z",
      id: projectId,
      name: "First project",
      status: "ACTIVE",
      updatedAtUtc: "2026-08-04T00:00:00.000Z",
    });

    const unbounded = await routes.request("https://control.example.test/projects?limit=101");
    expect(unbounded.status).toBe(400);
  });

  it("imports an audited job and supports cancellation, retry, and a stable event timeline", async () => {
    const routes = createRoutes([
      projectId,
      "00000000-0000-4000-8000-000000000010",
      "00000000-0000-4000-8000-000000000011",
      "00000000-0000-4000-8000-000000000012",
      "00000000-0000-4000-8000-000000000013",
      "00000000-0000-4000-8000-000000000014",
      "00000000-0000-4000-8000-000000000015",
      "00000000-0000-4000-8000-000000000016",
      "00000000-0000-4000-8000-000000000017",
      "00000000-0000-4000-8000-000000000018",
    ]);
    await routes.request("https://control.example.test/projects", {
      body: JSON.stringify({ name: "Import project" }),
      headers: { "content-type": "application/json", "idempotency-key": "create-import-project" },
      method: "POST",
    });

    const imported = await routes.request(`https://control.example.test/projects/${projectId}/imports`, {
      body: JSON.stringify({
        locator: { kind: "CREATOR_HTTPS_URL", url: "https://creator.example.test/episode-1" },
        rightsEvidence: "creator permission record 2026-08-04",
      }),
      headers: { "content-type": "application/json", "idempotency-key": "import-job-1" },
      method: "POST",
    });

    expect(imported.status).toBe(202);
    const importBody = (await imported.json()) as { jobId: string; sourceVideoId: string; state: string };
    expect(importBody).toEqual({
      jobId: "00000000-0000-4000-8000-000000000012",
      sourceVideoId: "00000000-0000-4000-8000-000000000011",
      state: "QUEUED",
    });

    const cancelled = await routes.request(`https://control.example.test/jobs/${importBody.jobId}/cancel`, {
      body: JSON.stringify({ expectedState: "QUEUED", expectedVersion: 1 }),
      headers: { "content-type": "application/json", "idempotency-key": "cancel-job-1" },
      method: "POST",
    });
    expect(cancelled.status).toBe(202);
    await expect(cancelled.json()).resolves.toEqual({ jobId: importBody.jobId, state: "CANCELLED", version: 2 });

    const retried = await routes.request(`https://control.example.test/jobs/${importBody.jobId}/retry`, {
      body: JSON.stringify({ expectedState: "CANCELLED", expectedVersion: 2 }),
      headers: { "content-type": "application/json", "idempotency-key": "retry-job-1" },
      method: "POST",
    });
    expect(retried.status).toBe(202);
    await expect(retried.json()).resolves.toEqual({ jobId: importBody.jobId, state: "QUEUED", version: 3 });

    const timeline = await routes.request(`https://control.example.test/jobs/${importBody.jobId}/events?limit=100`);
    expect(timeline.status).toBe(200);
    await expect(timeline.json()).resolves.toMatchObject({
      items: [
        { eventType: "IMPORT_REQUESTED", nextState: "QUEUED", previousState: null },
        { eventType: "CANCELLED", nextState: "CANCELLED", previousState: "QUEUED" },
        { eventType: "RETRIED", nextState: "QUEUED", previousState: "CANCELLED" },
      ],
      nextCursor: null,
    });

    const firstTimelinePage = await routes.request(`https://control.example.test/jobs/${importBody.jobId}/events?limit=1`);
    const firstTimelineBody = (await firstTimelinePage.json()) as { items: Array<{ eventType: string }>; nextCursor: string };
    expect(firstTimelineBody.items).toEqual([expect.objectContaining({ eventType: "IMPORT_REQUESTED" })]);
    expect(firstTimelineBody.nextCursor).toEqual(expect.any(String));

    const secondTimelinePage = await routes.request(
      `https://control.example.test/jobs/${importBody.jobId}/events?limit=1&cursor=${encodeURIComponent(firstTimelineBody.nextCursor)}`,
    );
    expect(secondTimelinePage.status).toBe(200);
    await expect(secondTimelinePage.json()).resolves.toMatchObject({
      items: [expect.objectContaining({ eventType: "CANCELLED" })],
    });
  });

  it("binds script approval to an immutable version and invalidates it after a later edit", async () => {
    const database = createDatabase();
    const operator = createRoutes(
      [
        projectId,
        "00000000-0000-4000-8000-000000000020",
        "00000000-0000-4000-8000-000000000021",
        "00000000-0000-4000-8000-000000000022",
        "00000000-0000-4000-000000000023",
        "00000000-0000-4000-8000-000000000024",
        "00000000-0000-4000-8000-000000000025",
        "00000000-0000-4000-8000-000000000026",
      ],
      "operator",
      database,
    );
    const reviewer = createRoutes([], "reviewer", database);
    await operator.request("https://control.example.test/projects", {
      body: JSON.stringify({ name: "Script project" }),
      headers: { "content-type": "application/json", "idempotency-key": "create-script-project" },
      method: "POST",
    });
    const imported = await operator.request(`https://control.example.test/projects/${projectId}/imports`, {
      body: JSON.stringify({
        locator: { kind: "OCI_INBOX_FILE", inboxFileId: "episode-1.mp4" },
        rightsEvidence: "creator permission record 2026-08-04",
      }),
      headers: { "content-type": "application/json", "idempotency-key": "import-script-job" },
      method: "POST",
    });
    const { jobId } = (await imported.json()) as { jobId: string };
    const firstHash = "a".repeat(64);
    const secondHash = "b".repeat(64);

    const firstEdit = await operator.request(`https://control.example.test/jobs/${jobId}/scripts`, {
      body: JSON.stringify({ contentHash: firstHash, transcriptHash: "c".repeat(64) }),
      headers: { "content-type": "application/json", "idempotency-key": "script-edit-1" },
      method: "POST",
    });
    expect(firstEdit.status).toBe(201);
    await expect(firstEdit.json()).resolves.toMatchObject({ contentHash: firstHash, jobId, version: 1 });

    const premature = await reviewer.request(`https://control.example.test/jobs/${jobId}/scripts/approve`, {
      body: JSON.stringify({ contentHash: firstHash, jobId, scriptVersion: 1 }),
      headers: { "content-type": "application/json", "idempotency-key": "approve-script-early" },
      method: "POST",
    });
    expect(premature.status).toBe(409);

    database.sqlite.prepare("UPDATE jobs SET status = 'WAITING_SCRIPT_REVIEW' WHERE id = ?").run(jobId);
    const approved = await reviewer.request(`https://control.example.test/jobs/${jobId}/scripts/approve`, {
      body: JSON.stringify({ contentHash: firstHash, jobId, scriptVersion: 1 }),
      headers: { "content-type": "application/json", "idempotency-key": "approve-script-1" },
      method: "POST",
    });
    expect(approved.status).toBe(202);
    await expect(approved.json()).resolves.toMatchObject({ contentHash: firstHash, jobId, scriptVersion: 1 });
    expect(database.sqlite.prepare("SELECT status FROM jobs WHERE id = ?").get(jobId)).toEqual({ status: "GENERATING_VOICE" });

    const secondEdit = await operator.request(`https://control.example.test/jobs/${jobId}/scripts`, {
      body: JSON.stringify({ contentHash: secondHash, transcriptHash: "d".repeat(64) }),
      headers: { "content-type": "application/json", "idempotency-key": "script-edit-2" },
      method: "POST",
    });
    expect(secondEdit.status).toBe(201);
    await expect(secondEdit.json()).resolves.toMatchObject({ contentHash: secondHash, jobId, version: 2 });

    const scripts = await operator.request(`https://control.example.test/jobs/${jobId}/scripts?limit=100`);
    expect(scripts.status).toBe(200);
    await expect(scripts.json()).resolves.toMatchObject({
      items: [
        { contentHash: firstHash, version: 1 },
        { contentHash: secondHash, version: 2 },
      ],
      nextCursor: null,
    });
  });

  it("binds publish approval to an immutable render and exposes only an opaque preview descriptor", async () => {
    const database = createDatabase();
    const operator = createRoutes(
      [
        projectId,
        "00000000-0000-4000-8000-000000000030",
        "00000000-0000-4000-8000-000000000031",
        "00000000-0000-4000-8000-000000000032",
        "00000000-0000-4000-8000-000000000033",
        "00000000-0000-4000-8000-000000000034",
        "00000000-0000-4000-8000-000000000035",
        "00000000-0000-4000-8000-000000000036",
        "00000000-0000-4000-8000-000000000037",
        "00000000-0000-4000-8000-000000000038",
        "00000000-0000-4000-8000-000000000039",
      ],
      "operator",
      database,
    );
    const reviewer = createRoutes([], "reviewer", database);
    await operator.request("https://control.example.test/projects", {
      body: JSON.stringify({ name: "Render project" }),
      headers: { "content-type": "application/json", "idempotency-key": "create-render-project" },
      method: "POST",
    });
    const imported = await operator.request(`https://control.example.test/projects/${projectId}/imports`, {
      body: JSON.stringify({
        locator: { kind: "OCI_INBOX_FILE", inboxFileId: "episode-2.mp4" },
        rightsEvidence: "creator permission record 2026-08-04",
      }),
      headers: { "content-type": "application/json", "idempotency-key": "import-render-job" },
      method: "POST",
    });
    const { jobId } = (await imported.json()) as { jobId: string };
    const firstRenderHash = "e".repeat(64);
    const metadataHash = "f".repeat(64);

    const firstRender = await operator.request(`https://control.example.test/jobs/${jobId}/renders`, {
      body: JSON.stringify({ byteCount: 42, previewDescriptor: "preview-opaque-render-v1", renderHash: firstRenderHash }),
      headers: { "content-type": "application/json", "idempotency-key": "create-render-1" },
      method: "POST",
    });
    expect(firstRender.status).toBe(201);
    const firstRenderBody = (await firstRender.json()) as { id: string; preview: { descriptor: string }; version: number };
    expect(firstRenderBody).toMatchObject({ preview: { descriptor: "preview-opaque-render-v1" }, version: 1 });
    expect(JSON.stringify(firstRenderBody)).not.toContain("https://");

    const publishApproved = await reviewer.request(`https://control.example.test/jobs/${jobId}/publish-approval`, {
      body: JSON.stringify({
        destinations: ["YOUTUBE"],
        jobId,
        metadata: [{ description: "Vietnamese description", destination: "YOUTUBE", tags: ["anime"], title: "Vietnamese title" }],
        metadataHash,
        renderId: firstRenderBody.id,
        renderVersion: 1,
        scheduleAtUtc: null,
      }),
      headers: { "content-type": "application/json", "idempotency-key": "approve-publish-1" },
      method: "POST",
    });
    expect(publishApproved.status).toBe(202);
    await expect(publishApproved.json()).resolves.toMatchObject({
      jobId,
      metadataHash,
      renderHash: firstRenderHash,
      renderId: firstRenderBody.id,
      renderVersion: 1,
    });

    const secondRender = await operator.request(`https://control.example.test/jobs/${jobId}/renders`, {
      body: JSON.stringify({ byteCount: 43, previewDescriptor: "preview-opaque-render-v2", renderHash: "1".repeat(64) }),
      headers: { "content-type": "application/json", "idempotency-key": "create-render-2" },
      method: "POST",
    });
    expect(secondRender.status).toBe(201);
    await expect(secondRender.json()).resolves.toMatchObject({ version: 2 });

    const approval = database.sqlite
      .prepare("SELECT invalidated_at, content_hash, metadata_hash FROM approvals WHERE job_id = ? AND approval_type = 'PUBLISH_APPROVED'")
      .get(jobId) as { content_hash: string; invalidated_at: string | null; metadata_hash: string };
    expect(approval).toMatchObject({ content_hash: firstRenderHash, metadata_hash: metadataHash });
    expect(approval.invalidated_at).toBe("2026-08-04T00:00:00.000Z");
  });
});
