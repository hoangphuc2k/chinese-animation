import { afterEach, describe, expect, it } from "vitest";

import type { PipelineArtifact } from "../../../packages/contracts/src/jobs";
import type { JobType, ProcessingState } from "../../../packages/contracts/src/states";
import { createBrowserWorkflowRoutes } from "../src/api/project-routes";
import type { AuthContext } from "../src/middleware/access-auth";
import { RetentionService } from "../src/scheduled/retention";
import { ApprovalService, ApprovalMismatchError } from "../src/services/approval-service";
import { ActiveImportExistsError, JobService, SourceRightsRevokedError } from "../src/services/job-service";
import { LeaseService, type ClaimedLease, type LeaseDatabase } from "../src/services/lease-service";
import { createMigratedD1, type SqliteD1 } from "./sqlite-d1";

const projectId = "00000000-0000-4000-8000-000000000001";
const agentId = "agent-1";
const t0 = "2026-10-26T00:00:00.000Z";
const sha = (character: string) => character.repeat(64);
const url = "https://creator.example/episode-01.mp4";

const databases: SqliteD1[] = [];

afterEach(() => {
  for (const database of databases.splice(0)) database.close();
});

function artifact(kind: PipelineArtifact["kind"], character: string): PipelineArtifact {
  return { artifactRef: `${kind.toLowerCase()}-01`, byteCount: 1024, kind, sha256: sha(character), toolVersion: "test-tool-1" };
}

function plusSeconds(isoUtc: string, seconds: number): string {
  return new Date(new Date(isoUtc).getTime() + seconds * 1000).toISOString();
}

function setup() {
  const database = createMigratedD1();
  databases.push(database);
  database.sqlite.exec(
    `INSERT INTO projects (id, name, status, created_at, updated_at) VALUES ('${projectId}', 'Series', 'ACTIVE', '${t0}', '${t0}')`,
  );
  let counter = 100;
  const newId = () => `00000000-0000-4000-8000-${String((counter += 1)).padStart(12, "0")}`;
  const jobs = new JobService(database, newId);
  const leases = new LeaseService(database as unknown as LeaseDatabase, undefined, newId);
  const approvals = new ApprovalService(database, newId);
  const retention = new RetentionService(database, newId);
  const job = (jobId: string) =>
    database.sqlite.prepare("SELECT * FROM jobs WHERE id = ?").get(jobId) as Record<string, unknown> & {
      attempt_count: number;
      retention_deadline: string | null;
      status: ProcessingState;
      step: string;
      version: number;
    };

  async function importVideo(allowedProcessors: string[] = ["GOOGLE_GEMINI", "AZURE_SPEECH"]) {
    const jobId = newId();
    const sourceAssetId = newId();
    const created = await jobs.createImport({
      actorId: "admin",
      actorType: "admin",
      allowedProcessors,
      jobId,
      nowUtc: t0,
      projectId,
      requestId: newId(),
      rightsEvidence: "Signed licence 2026-10",
      sourceAssetId,
      sourceExternalId: url,
      sourceMetadataJson: JSON.stringify({ locator: { kind: "CREATOR_HTTPS_URL", url } }),
      sourceProvider: "CREATOR_HTTPS_URL",
    });
    return created;
  }

  async function claim(state: ProcessingState, nowUtc = t0, jobTypes: JobType[] = ["IMPORT"]): Promise<ClaimedLease | null> {
    return leases.claim({
      agentId,
      leaseExpiresAtUtc: plusSeconds(nowUtc, 300),
      nowUtc,
      supportedJobTypes: jobTypes,
      supportedStates: [state],
    });
  }

  type CompletionExtra = Partial<Parameters<LeaseService["complete"]>[0]>;

  async function complete(lease: ClaimedLease, nextState: ProcessingState, extra: CompletionExtra = {}, nowUtc = t0) {
    await leases.complete({
      agentId,
      expectedState: lease.state,
      jobId: lease.jobId,
      jobVersion: lease.jobVersion,
      leaseToken: lease.leaseToken,
      nextState,
      nowUtc,
      step: nextState.toLowerCase(),
      ...extra,
    });
  }

  async function advance(state: ProcessingState, nextState: ProcessingState, extra: CompletionExtra = {}, nowUtc = t0) {
    const lease = await claim(state, nowUtc);
    if (!lease) throw new Error(`No ${state} claim was available.`);
    await complete(lease, nextState, extra, nowUtc);
    return lease;
  }

  async function runToTranslation() {
    await advance("QUEUED", "INGESTING");
    await advance("INGESTING", "PROBING", {
      ingestResult: {
        normalizedByteCount: 2048,
        normalizedHash: sha("1"),
        sourceByteCount: 4096,
        sourceHash: sha("2"),
        technicalMetadata: { durationSeconds: 600, formatName: "mov,mp4", hasAudio: true, hasVideo: true },
      },
    });
    await advance("PROBING", "EXTRACTING_AUDIO");
    await advance("EXTRACTING_AUDIO", "TRANSCRIBING", { pipelineArtifacts: [artifact("EXTRACTED_AUDIO", "3")] });
    await advance("TRANSCRIBING", "TRANSLATING", { pipelineArtifacts: [artifact("TRANSCRIPT", "4")] });
  }

  function jobDetailRoutes() {
    return createBrowserWorkflowRoutes({
      authenticate: async (context: { set(key: "auth", value: AuthContext["auth"]): void }, next: () => Promise<void>) => {
        context.set("auth", { actorId: "admin", email: "admin@example.test", kind: "browser", role: "admin" });
        await next();
      },
      csrf: async (_context: unknown, next: () => Promise<void>) => next(),
      database,
    });
  }

  return { advance, approvals, claim, complete, database, importVideo, job, jobDetailRoutes, jobs, leases, retention, runToTranslation };
}

describe("cross-plane job lifecycle", () => {
  it("carries an import through script approval, preview retention, expiry cleanup, and re-import", async () => {
    const { advance, approvals, claim, complete, importVideo, job, jobDetailRoutes, retention, runToTranslation } = setup();
    const { jobId, sourceAssetId } = await importVideo();
    await runToTranslation();

    const translation = await claim("TRANSLATING");
    expect(translation?.pipelineInput).toMatchObject({ googleProcessingAllowed: true, state: "TRANSLATING" });
    await complete(translation!, "WAITING_SCRIPT_REVIEW", { pipelineArtifacts: [artifact("SCRIPT", "5")] });

    const detail = await jobDetailRoutes().request(`https://control.example.test/jobs/${jobId}`);
    expect(detail.status).toBe(200);
    await expect(detail.json()).resolves.toMatchObject({
      jobId,
      latestScript: { contentHash: sha("5"), version: 1 },
      state: "WAITING_SCRIPT_REVIEW",
    });

    await approvals.approveScript({
      actorId: "admin",
      contentHash: sha("5"),
      jobId,
      nowUtc: t0,
      requestId: "00000000-0000-4000-8000-000000000999",
      scriptVersion: 1,
    });
    expect(job(jobId).status).toBe("GENERATING_VOICE");

    const voice = await advance("GENERATING_VOICE", "MIXING", { pipelineArtifacts: [artifact("VOICE", "6")] });
    expect(voice.pipelineInput).toMatchObject({ scriptSha256: sha("5"), scriptVersion: 1, state: "GENERATING_VOICE" });
    await advance("MIXING", "RENDERING", { pipelineArtifacts: [artifact("MIXED_AUDIO", "7"), artifact("SUBTITLES", "8")] });
    await advance("RENDERING", "READY_FOR_PREVIEW", { pipelineArtifacts: [artifact("RENDER", "9"), artifact("SUBTITLES", "8")] });
    expect(job(jobId).retention_deadline).toBe(plusSeconds(t0, 7 * 24 * 3600));

    await retention.run({ nowUtc: plusSeconds(t0, 7 * 24 * 3600 + 1), requestId: "00000000-0000-4000-8000-000000000998" });
    expect(job(jobId).status).toBe("EXPIRED");

    const cleanupAt = plusSeconds(t0, 7 * 24 * 3600 + 2);
    const cleanup = await claim("QUEUED", cleanupAt, ["DELETE_EXPIRED"]);
    expect(cleanup).toMatchObject({ cleanupInput: { targetJobId: jobId }, jobType: "DELETE_EXPIRED" });
    await complete(cleanup!, "COMPLETED", {}, cleanupAt);
    expect(job(cleanup!.jobId).status).toBe("COMPLETED");

    const reimport = await importVideo();
    expect(reimport.sourceAssetId).toBe(sourceAssetId);
    expect(reimport.jobId).not.toBe(jobId);
    expect(job(reimport.jobId).status).toBe("QUEUED");

  });

  it("stops at manual review instead of looping when Gemini processing was not consented", async () => {
    const { claim, importVideo, job, runToTranslation } = setup();
    const { jobId } = await importVideo([]);
    await runToTranslation();

    expect(job(jobId)).toMatchObject({ status: "MANUAL_REVIEW_REQUIRED", step: "TRANSLATION_CONSENT_MISSING" });
    expect(await claim("TRANSLATING")).toBeNull();
  });

  it("never hands a TRANSLATING claim to the agent for a legacy row without consent", async () => {
    const { claim, database, importVideo, job, runToTranslation } = setup();
    const { jobId } = await importVideo([]);
    await runToTranslation();
    database.sqlite.exec(`UPDATE jobs SET status = 'TRANSLATING' WHERE id = '${jobId}'`);

    expect(await claim("TRANSLATING")).toBeNull();
    expect(job(jobId).lease_owner).toBeNull();
  });

  it("does not claim any pipeline step for a revoked source", async () => {
    const { advance, claim, database, importVideo } = setup();
    const { sourceAssetId } = await importVideo();
    await advance("QUEUED", "INGESTING");
    database.sqlite.exec(`UPDATE source_assets SET rights_revoked_at = '${t0}' WHERE id = '${sourceAssetId}'`);

    expect(await claim("INGESTING")).toBeNull();
  });

  it("rejects re-import while a job is active and re-import of a revoked source", async () => {
    const { database, importVideo } = setup();
    const { sourceAssetId } = await importVideo();

    await expect(importVideo()).rejects.toBeInstanceOf(ActiveImportExistsError);

    database.sqlite.exec(`UPDATE jobs SET status = 'EXPIRED'`);
    database.sqlite.exec(`UPDATE source_assets SET rights_revoked_at = '${t0}' WHERE id = '${sourceAssetId}'`);
    await expect(importVideo()).rejects.toBeInstanceOf(SourceRightsRevokedError);
  });

  it("refuses script approval outside script review and for a superseded version", async () => {
    const { approvals, importVideo } = setup();
    const { jobId } = await importVideo();

    await expect(
      approvals.approveScript({
        actorId: "admin",
        contentHash: sha("5"),
        jobId,
        nowUtc: t0,
        requestId: "00000000-0000-4000-8000-000000000997",
        scriptVersion: 1,
      }),
    ).rejects.toBeInstanceOf(ApprovalMismatchError);
  });

  it("caps automated attempts per step and sweeps exhausted jobs to manual review", async () => {
    const { advance, claim, database, importVideo, job, retention } = setup();
    const { jobId } = await importVideo();
    await advance("QUEUED", "INGESTING");
    expect(job(jobId).attempt_count).toBe(0);

    database.sqlite.exec(`UPDATE jobs SET attempt_count = 5 WHERE id = '${jobId}'`);
    expect(await claim("INGESTING")).toBeNull();

    await retention.run({ nowUtc: plusSeconds(t0, 60), requestId: "00000000-0000-4000-8000-000000000996" });
    expect(job(jobId)).toMatchObject({ status: "MANUAL_REVIEW_REQUIRED", step: "ATTEMPTS_EXHAUSTED" });
  });

  it("records an agent failure code and starts the failed-artifact retention window", async () => {
    const { claim, importVideo, job, leases } = setup();
    const { jobId } = await importVideo();
    const lease = await claim("QUEUED");

    await leases.complete({
      agentId,
      expectedState: "QUEUED",
      failureCode: "MEDIA_INVALID",
      jobId,
      jobVersion: lease!.jobVersion,
      leaseToken: lease!.leaseToken,
      nextState: "FAILED",
      nowUtc: t0,
      step: "source_rejected",
    });

    expect(job(jobId)).toMatchObject({ retention_deadline: plusSeconds(t0, 7 * 24 * 3600), status: "FAILED" });
  });
  it("writes no cleanup, destination, or audit rows when the job changed after candidate selection", async () => {
    const { database, importVideo, job } = setup();
    const { jobId } = await importVideo();
    database.sqlite.exec(
      `UPDATE jobs SET status = 'WAITING_PUBLISH_APPROVAL', retention_deadline = '${t0}' WHERE id = '${jobId}'`,
    );
    database.sqlite.exec(
      `INSERT INTO destination_publications (id, job_id, destination, status, metadata_hash, created_at, updated_at) VALUES ('publication-1', '${jobId}', 'YOUTUBE', 'STAGED_HIDDEN', 'hash', '${t0}', '${t0}')`,
    );
    // A concurrent writer bumps the job version between the candidate SELECT and the batch.
    const racing = {
      batch: async (statements: Parameters<SqliteD1["batch"]>[0]) => {
        database.sqlite.exec(`UPDATE jobs SET version = version + 1 WHERE id = '${jobId}'`);
        return database.batch(statements);
      },
      prepare: (sql: string) => database.prepare(sql),
    };

    await new RetentionService(racing).run({ nowUtc: plusSeconds(t0, 1), requestId: "00000000-0000-4000-8000-000000000995" });

    expect(job(jobId).status).toBe("WAITING_PUBLISH_APPROVAL");
    expect(database.sqlite.prepare("SELECT status FROM destination_publications").get()).toEqual({ status: "STAGED_HIDDEN" });
    expect(database.sqlite.prepare("SELECT COUNT(*) AS count FROM jobs WHERE job_type = 'DELETE_EXPIRED'").get()).toEqual({ count: 0 });
    expect(
      database.sqlite.prepare("SELECT COUNT(*) AS count FROM audit_events WHERE operation = 'RETENTION_EXPIRY_REQUESTED'").get(),
    ).toEqual({ count: 0 });
  });

  it("flags a published destination for manual review on revocation without altering its state", async () => {
    const { database, importVideo, job, retention } = setup();
    const { jobId, sourceAssetId } = await importVideo();
    database.sqlite.exec(`UPDATE jobs SET status = 'WAITING_PUBLISH_APPROVAL' WHERE id = '${jobId}'`);
    database.sqlite.exec(
      `INSERT INTO destination_publications (id, job_id, destination, status, metadata_hash, created_at, updated_at) VALUES ('publication-yt', '${jobId}', 'YOUTUBE', 'PUBLISHED', 'hash', '${t0}', '${t0}'), ('publication-fb', '${jobId}', 'FACEBOOK', 'READY_TO_UPLOAD', 'hash', '${t0}', '${t0}')`,
    );
    database.sqlite.exec(`UPDATE source_assets SET rights_revoked_at = '${t0}' WHERE id = '${sourceAssetId}'`);

    await retention.run({ nowUtc: plusSeconds(t0, 1), requestId: "00000000-0000-4000-8000-000000000994" });

    expect(job(jobId).status).toBe("RIGHTS_BLOCKED");
    expect(
      database.sqlite.prepare("SELECT destination, status, rights_review_required_at FROM destination_publications ORDER BY destination").all(),
    ).toEqual([
      { destination: "FACEBOOK", rights_review_required_at: null, status: "MANUAL_ACTION_REQUIRED" },
      { destination: "YOUTUBE", rights_review_required_at: plusSeconds(t0, 1), status: "PUBLISHED" },
    ]);
  });
});
