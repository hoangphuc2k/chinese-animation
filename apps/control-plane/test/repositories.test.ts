import { describe, expect, it } from "vitest";

import { createRepositories } from "../src/repositories/index";

interface QueryCall {
  method?: "all" | "first" | "run";
  sql: string;
  values: unknown[];
}

interface QueryResult<Row> {
  results: Row[];
}

class RecordingStatement {
  constructor(private readonly call: QueryCall) {}

  bind(...values: unknown[]): RecordingStatement {
    this.call.values = values;
    return this;
  }

  async all<Row extends Record<string, unknown>>(): Promise<QueryResult<Row>> {
    this.call.method = "all";
    return { results: [] };
  }

  async first<Row extends Record<string, unknown>>(): Promise<Row | null> {
    this.call.method = "first";
    return null;
  }

  async run<Row extends Record<string, unknown>>(): Promise<QueryResult<Row>> {
    this.call.method = "run";
    return { results: [] };
  }
}

class RecordingDatabase {
  readonly calls: QueryCall[] = [];

  prepare(sql: string): RecordingStatement {
    const call: QueryCall = { sql, values: [] };
    this.calls.push(call);
    return new RecordingStatement(call);
  }
}

describe("control-plane repositories", () => {
  it("writes project and source-asset integrity data through bound SQL", async () => {
    const database = new RecordingDatabase();
    const repositories = createRepositories(database);

    await repositories.projects.create({
      createdAtUtc: "2026-08-03T00:00:00Z",
      id: "project-1",
      name: "Authorized pilot",
      status: "ACTIVE",
      updatedAtUtc: "2026-08-03T00:00:00Z",
    });
    await repositories.sourceAssets.create({
      byteCount: 4096,
      contentHash: "source-hash-1",
      createdAtUtc: "2026-08-03T00:00:00Z",
      externalId: "video-1",
      id: "source-1",
      metadataJson: "{\"title\":\"Fixture\"}",
      projectId: "project-1",
      rightsExpiresAtUtc: "2026-08-10T00:00:00Z",
      rightsMetadataJson: "{\"permission\":\"written\"}",
      sourceProvider: "BILIBILI",
    });

    expect(database.calls).toEqual([
      {
        method: "run",
        sql: "INSERT INTO projects (id, name, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        values: [
          "project-1",
          "Authorized pilot",
          "ACTIVE",
          "2026-08-03T00:00:00Z",
          "2026-08-03T00:00:00Z",
        ],
      },
      {
        method: "run",
        sql: "INSERT INTO source_assets (id, project_id, source_provider, external_id, metadata_json, content_hash, byte_count, rights_metadata_json, rights_expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        values: [
          "source-1",
          "project-1",
          "BILIBILI",
          "video-1",
          "{\"title\":\"Fixture\"}",
          "source-hash-1",
          4096,
          "{\"permission\":\"written\"}",
          "2026-08-10T00:00:00Z",
          "2026-08-03T00:00:00Z",
        ],
      },
    ]);
  });

  it("queries instead of transitioning runnable, expiring, active, and retention jobs", async () => {
    const database = new RecordingDatabase();
    const repositories = createRepositories(database);

    await repositories.jobs.listRunnable({
      availableBeforeUtc: "2026-08-03T00:00:00Z",
      limit: 5,
    });
    await repositories.jobs.listExpiringLeases({
      leaseExpiresBeforeUtc: "2026-08-03T00:00:00Z",
      limit: 5,
    });
    await repositories.jobs.listProjectActivity({ limit: 5, projectId: "project-1" });
    await repositories.jobs.listRetentionDue({
      retentionDueBeforeUtc: "2026-08-03T00:00:00Z",
      limit: 5,
    });

    expect(database.calls).toEqual([
      {
        method: "all",
        sql: "SELECT * FROM jobs WHERE status = ? AND available_at <= ? AND (lease_until IS NULL OR lease_until <= ?) ORDER BY priority DESC, created_at ASC LIMIT ?",
        values: ["QUEUED", "2026-08-03T00:00:00Z", "2026-08-03T00:00:00Z", 5],
      },
      {
        method: "all",
        sql: "SELECT * FROM jobs WHERE lease_until IS NOT NULL AND lease_until <= ? ORDER BY lease_until ASC LIMIT ?",
        values: ["2026-08-03T00:00:00Z", 5],
      },
      {
        method: "all",
        sql: "SELECT * FROM jobs WHERE project_id = ? ORDER BY updated_at DESC LIMIT ?",
        values: ["project-1", 5],
      },
      {
        method: "all",
        sql: "SELECT * FROM jobs WHERE retention_deadline IS NOT NULL AND retention_deadline <= ? ORDER BY retention_deadline ASC LIMIT ?",
        values: ["2026-08-03T00:00:00Z", 5],
      },
    ]);
  });

  it("writes versioned renders and destination publication identities through bound SQL", async () => {
    const database = new RecordingDatabase();
    const repositories = createRepositories(database);

    await repositories.scriptVersions.create({
      contentHash: "script-hash-1",
      createdAtUtc: "2026-08-03T00:00:00Z",
      id: "script-1",
      jobId: "job-1",
      metadataHash: "metadata-hash-1",
      transcriptHash: "transcript-hash-1",
      translationHash: "translation-hash-1",
      version: 1,
      voiceSettingsHash: "voice-hash-1",
    });
    await repositories.renderVersions.create({
      artifactRef: "render-opaque-1",
      byteCount: 8192,
      createdAtUtc: "2026-08-03T00:00:00Z",
      id: "render-1",
      jobId: "job-1",
      renderHash: "render-hash-1",
      version: 1,
    });
    await repositories.destinationPublications.create({
      createdAtUtc: "2026-08-03T00:00:00Z",
      destination: "YOUTUBE",
      id: "publication-1",
      jobId: "job-1",
      metadataHash: "metadata-hash-1",
      providerAssetId: "youtube-asset-1",
      providerSessionId: "upload-session-1",
      renderVersionId: "render-1",
      scheduledAtUtc: null,
      status: "UPLOADING",
      updatedAtUtc: "2026-08-03T00:00:00Z",
    });

    expect(database.calls).toEqual([
      {
        method: "run",
        sql: "INSERT INTO script_versions (id, job_id, version, content_hash, transcript_hash, translation_hash, metadata_hash, voice_settings_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        values: [
          "script-1",
          "job-1",
          1,
          "script-hash-1",
          "transcript-hash-1",
          "translation-hash-1",
          "metadata-hash-1",
          "voice-hash-1",
          "2026-08-03T00:00:00Z",
        ],
      },
      {
        method: "run",
        sql: "INSERT INTO render_versions (id, job_id, version, render_hash, byte_count, artifact_ref, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        values: [
          "render-1",
          "job-1",
          1,
          "render-hash-1",
          8192,
          "render-opaque-1",
          "2026-08-03T00:00:00Z",
        ],
      },
      {
        method: "run",
        sql: "INSERT INTO destination_publications (id, job_id, destination, provider_asset_id, provider_session_id, render_version_id, status, metadata_hash, scheduled_at_utc, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        values: [
          "publication-1",
          "job-1",
          "YOUTUBE",
          "youtube-asset-1",
          "upload-session-1",
          "render-1",
          "UPLOADING",
          "metadata-hash-1",
          null,
          "2026-08-03T00:00:00Z",
          "2026-08-03T00:00:00Z",
        ],
      },
    ]);
  });

  it("appends approvals and audit events while retrieving and storing idempotency records", async () => {
    const database = new RecordingDatabase();
    const repositories = createRepositories(database);

    await repositories.jobEvents.append({
      createdAtUtc: "2026-08-03T00:00:00Z",
      detailsJson: "{\"origin\":\"test\"}",
      eventType: "IMPORT_REQUESTED",
      id: "job-event-1",
      jobId: "job-1",
      nextState: "QUEUED",
      previousState: null,
    });
    await repositories.approvals.append({
      actorId: "admin-1",
      approvalType: "SCRIPT_APPROVED",
      approvedAtUtc: "2026-08-03T00:00:00Z",
      contentHash: "script-hash-1",
      destinationsJson: null,
      id: "approval-1",
      jobId: "job-1",
      metadataHash: null,
      scheduleAtUtc: null,
      version: 1,
    });
    await repositories.idempotency.find({
      actorId: "admin-1",
      idempotencyKey: "idem-key-1",
      operation: "APPROVE_SCRIPT",
    });
    await repositories.idempotency.reserve({
      actorId: "admin-1",
      createdAtUtc: "2026-08-03T00:00:00Z",
      id: "idem-1",
      idempotencyKey: "idem-key-1",
      operation: "APPROVE_SCRIPT",
      requestHash: "request-hash-1",
    });
    await repositories.idempotency.complete({
      actorId: "admin-1",
      idempotencyKey: "idem-key-1",
      operation: "APPROVE_SCRIPT",
      responseJson: "{\"status\":\"accepted\"}",
      responseStatus: 202,
    });
    await repositories.auditEvents.append({
      actorId: "admin-1",
      actorType: "ADMINISTRATOR",
      createdAtUtc: "2026-08-03T00:00:00Z",
      detailsJson: "{\"reason\":\"approved\"}",
      id: "audit-1",
      nextState: "WAITING_PUBLISH_APPROVAL",
      operation: "APPROVE_SCRIPT",
      previousState: "WAITING_SCRIPT_REVIEW",
      requestId: "request-1",
      targetHash: "script-hash-1",
      targetId: "job-1",
      targetType: "JOB",
      targetVersion: 1,
    });

    expect(database.calls).toEqual([
      {
        method: "run",
        sql: "INSERT INTO job_events (id, job_id, event_type, previous_state, next_state, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        values: [
          "job-event-1",
          "job-1",
          "IMPORT_REQUESTED",
          null,
          "QUEUED",
          "{\"origin\":\"test\"}",
          "2026-08-03T00:00:00Z",
        ],
      },
      {
        method: "run",
        sql: "INSERT INTO approvals (id, job_id, approval_type, version, content_hash, metadata_hash, destinations_json, schedule_at_utc, actor_id, approved_at, invalidated_at, invalidation_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
        values: [
          "approval-1",
          "job-1",
          "SCRIPT_APPROVED",
          1,
          "script-hash-1",
          null,
          null,
          null,
          "admin-1",
          "2026-08-03T00:00:00Z",
        ],
      },
      {
        method: "first",
        sql: "SELECT * FROM idempotency_records WHERE actor_id = ? AND operation = ? AND idempotency_key = ?",
        values: ["admin-1", "APPROVE_SCRIPT", "idem-key-1"],
      },
      {
        method: "first",
        sql: "INSERT INTO idempotency_records (id, actor_id, operation, idempotency_key, request_hash, response_status, response_json, created_at) VALUES (?, ?, ?, ?, ?, 0, 'null', ?) ON CONFLICT (actor_id, operation, idempotency_key) DO NOTHING RETURNING id",
        values: ["idem-1", "admin-1", "APPROVE_SCRIPT", "idem-key-1", "request-hash-1", "2026-08-03T00:00:00Z"],
      },
      {
        method: "run",
        sql: "UPDATE idempotency_records SET response_status = ?, response_json = ? WHERE actor_id = ? AND operation = ? AND idempotency_key = ? AND response_status = 0",
        values: [202, "{\"status\":\"accepted\"}", "admin-1", "APPROVE_SCRIPT", "idem-key-1"],
      },
      {
        method: "run",
        sql: "INSERT INTO audit_events (id, actor_id, actor_type, operation, target_type, target_id, target_version, target_hash, request_id, previous_state, next_state, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        values: [
          "audit-1",
          "admin-1",
          "ADMINISTRATOR",
          "APPROVE_SCRIPT",
          "JOB",
          "job-1",
          1,
          "script-hash-1",
          "request-1",
          "WAITING_SCRIPT_REVIEW",
          "WAITING_PUBLISH_APPROVAL",
          "{\"reason\":\"approved\"}",
          "2026-08-03T00:00:00Z",
        ],
      },
    ]);
  });
});
