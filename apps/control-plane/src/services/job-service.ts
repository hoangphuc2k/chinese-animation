import type { DestinationState, ProcessingState } from "../../../../packages/contracts/src/states";

import { assertDestinationTransition } from "../domain/destination-state-machine";
import { assertJobTransition } from "../domain/job-state-machine";
import type { SqlDatabase } from "../repositories/sql";

export interface WorkflowStatement {
  bind(...values: unknown[]): WorkflowStatement;
}

export interface WorkflowBatchResult {
  meta?: { changes?: number };
  results: Record<string, unknown>[];
}

export interface WorkflowDatabase {
  batch(statements: WorkflowStatement[]): Promise<WorkflowBatchResult[]>;
  prepare(sql: string): WorkflowStatement;
}

export interface TransitionJobInput {
  actorId: string;
  actorType: string;
  eventId: string;
  expectedState: ProcessingState;
  expectedVersion: number;
  jobId: string;
  nextState: ProcessingState;
  nowUtc: string;
  operation: string;
  requestId: string;
}

export interface TransitionDestinationInput {
  actorId: string;
  actorType: string;
  destination: string;
  eventId: string;
  expectedState: DestinationState;
  jobId: string;
  nextState: DestinationState;
  nowUtc: string;
  operation: string;
  publicationId: string;
  requestId: string;
}

export interface CreateImportJobInput {
  actorId: string;
  actorType: string;
  allowedProcessors: readonly string[];
  jobId: string;
  nowUtc: string;
  projectId: string;
  requestId: string;
  rightsEvidence: string;
  sourceAssetId: string;
  sourceExternalId: string;
  sourceMetadataJson: string;
  sourceProvider: string;
}

export interface RetryJobInput {
  actorId: string;
  actorType: string;
  eventId: string;
  expectedState: ProcessingState;
  expectedVersion: number;
  jobId: string;
  nowUtc: string;
  operation: string;
  requestId: string;
}

export class StaleWorkflowError extends Error {
  constructor() {
    super("The workflow state or version is stale.");
  }
}

export interface CreatedImport {
  jobId: string;
  sourceAssetId: string;
}

export class ActiveImportExistsError extends Error {
  constructor(readonly activeJobId: string | null) {
    super("The source already has an import in progress.");
  }
}

export class SourceRightsRevokedError extends Error {
  constructor() {
    super("The source's rights were revoked; it cannot be imported again.");
  }
}

const terminalJobStates = ["COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "RIGHTS_BLOCKED"] as const;

function isUniqueConstraintError(error: unknown): boolean {
  return error instanceof Error && /UNIQUE constraint failed/i.test(error.message);
}

export class RetryNotAllowedError extends Error {
  constructor() {
    super("Only a failed or cancelled job can be retried.");
  }
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export class JobService {
  constructor(
    private readonly database: SqlDatabase & WorkflowDatabase,
    private readonly newId: () => string = () => crypto.randomUUID(),
  ) {}

  async createImport(input: CreateImportJobInput): Promise<CreatedImport> {
    const sourceContentHash = await sha256(input.sourceMetadataJson);
    const rightsEvidenceHash = await sha256(input.rightsEvidence);
    const rightsMetadataJson = JSON.stringify({ allowedProcessors: [...input.allowedProcessors], evidenceHash: rightsEvidenceHash });
    const existing = await this.database
      .prepare("SELECT id, rights_revoked_at FROM source_assets WHERE source_provider = ? AND external_id = ?")
      .bind(input.sourceProvider, input.sourceExternalId)
      .first<{ id: string; rights_revoked_at: string | null }>();
    if (existing?.rights_revoked_at) throw new SourceRightsRevokedError();
    if (existing) {
      const active = await this.database
        .prepare(
          `SELECT id FROM jobs WHERE source_asset_id = ? AND job_type = 'IMPORT' AND status NOT IN (${terminalJobStates.map(() => "?").join(", ")}) LIMIT 1`,
        )
        .bind(existing.id, ...terminalJobStates)
        .first<{ id: string }>();
      if (active) throw new ActiveImportExistsError(active.id);
    }
    const sourceAssetId = existing?.id ?? input.sourceAssetId;
    // A re-import reuses the asset with a fresh rights snapshot (spec section 10).
    const assetStatement = existing
      ? this.database
          .prepare("UPDATE source_assets SET metadata_json = ?, content_hash = ?, rights_metadata_json = ? WHERE id = ? AND rights_revoked_at IS NULL")
          .bind(input.sourceMetadataJson, sourceContentHash, rightsMetadataJson, sourceAssetId)
      : this.database
          .prepare(
            "INSERT INTO source_assets (id, project_id, source_provider, external_id, metadata_json, content_hash, byte_count, rights_metadata_json, rights_expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
          )
          .bind(
            sourceAssetId,
            input.projectId,
            input.sourceProvider,
            input.sourceExternalId,
            input.sourceMetadataJson,
            sourceContentHash,
            0,
            rightsMetadataJson,
            null,
            input.nowUtc,
          );
    const statements = [
      assetStatement,
      this.database
        .prepare(
          "INSERT INTO jobs (id, project_id, source_asset_id, job_type, status, step, priority, available_at, attempt_count, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        )
        .bind(
          input.jobId,
          input.projectId,
          sourceAssetId,
          "IMPORT",
          "QUEUED",
          "QUEUED",
          0,
          input.nowUtc,
          0,
          input.nowUtc,
          input.nowUtc,
        ),
      this.database
        .prepare(
          "INSERT INTO job_events (id, job_id, event_type, previous_state, next_state, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        )
        .bind(this.newId(), input.jobId, "IMPORT_REQUESTED", null, "QUEUED", "{}", input.nowUtc),
      this.database
        .prepare(
          "INSERT INTO audit_events (id, actor_id, actor_type, operation, target_type, target_id, target_version, target_hash, request_id, previous_state, next_state, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        )
        .bind(
          this.newId(),
          input.actorId,
          input.actorType,
          "IMPORT_REQUESTED",
          "JOB",
          input.jobId,
          1,
          null,
          input.requestId,
          null,
          "QUEUED",
          "{}",
          input.nowUtc,
        ),
    ];
    try {
      await this.database.batch(statements);
    } catch (error) {
      if (isUniqueConstraintError(error)) throw new ActiveImportExistsError(null);
      throw error;
    }
    return { jobId: input.jobId, sourceAssetId };
  }

  async transition(input: TransitionJobInput): Promise<void> {
    assertJobTransition(input.expectedState, input.nextState);
    const statements = [
      this.database
        .prepare("UPDATE jobs SET status = ?, step = ?, version = version + 1, updated_at = ? WHERE id = ? AND status = ? AND version = ?")
        .bind(input.nextState, input.nextState, input.nowUtc, input.jobId, input.expectedState, input.expectedVersion),
      this.database
        .prepare(
          "INSERT INTO job_events (id, job_id, event_type, previous_state, next_state, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        )
        .bind(input.eventId, input.jobId, input.operation, input.expectedState, input.nextState, "{}", input.nowUtc),
      this.database
        .prepare(
          "INSERT INTO audit_events (id, actor_id, actor_type, operation, target_type, target_id, target_version, target_hash, request_id, previous_state, next_state, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        )
        .bind(
          this.newId(),
          input.actorId,
          input.actorType,
          input.operation,
          "JOB",
          input.jobId,
          input.expectedVersion + 1,
          null,
          input.requestId,
          input.expectedState,
          input.nextState,
          "{}",
          input.nowUtc,
        ),
    ];
    const results = await this.database.batch(statements);
    if (results[0]?.meta?.changes !== 1) throw new StaleWorkflowError();
  }

  async transitionDestination(input: TransitionDestinationInput): Promise<void> {
    assertDestinationTransition(input.expectedState, input.nextState);
    const statements = [
      this.database
        .prepare("UPDATE destination_publications SET status = ?, updated_at = ? WHERE id = ? AND destination = ? AND status = ?")
        .bind(input.nextState, input.nowUtc, input.publicationId, input.destination, input.expectedState),
      this.database
        .prepare(
          "INSERT INTO job_events (id, job_id, event_type, previous_state, next_state, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        )
        .bind(input.eventId, input.jobId, input.operation, input.expectedState, input.nextState, "{}", input.nowUtc),
      this.database
        .prepare(
          "INSERT INTO audit_events (id, actor_id, actor_type, operation, target_type, target_id, target_version, target_hash, request_id, previous_state, next_state, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        )
        .bind(
          this.newId(),
          input.actorId,
          input.actorType,
          input.operation,
          "DESTINATION_PUBLICATION",
          input.publicationId,
          null,
          null,
          input.requestId,
          input.expectedState,
          input.nextState,
          "{}",
          input.nowUtc,
        ),
    ];
    const results = await this.database.batch(statements);
    if (results[0]?.meta?.changes !== 1) throw new StaleWorkflowError();
  }

  async retry(input: RetryJobInput): Promise<void> {
    if (input.expectedState !== "FAILED" && input.expectedState !== "CANCELLED") throw new RetryNotAllowedError();
    const statements = [
      this.database
        .prepare(
          "UPDATE jobs SET status = ?, step = ?, version = version + 1, attempt_count = attempt_count + 1, available_at = ?, updated_at = ? WHERE id = ? AND status = ? AND version = ?",
        )
        .bind("QUEUED", "QUEUED", input.nowUtc, input.nowUtc, input.jobId, input.expectedState, input.expectedVersion),
      this.database
        .prepare(
          "INSERT INTO job_events (id, job_id, event_type, previous_state, next_state, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        )
        .bind(input.eventId, input.jobId, input.operation, input.expectedState, "QUEUED", "{}", input.nowUtc),
      this.database
        .prepare(
          "INSERT INTO audit_events (id, actor_id, actor_type, operation, target_type, target_id, target_version, target_hash, request_id, previous_state, next_state, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        )
        .bind(
          this.newId(),
          input.actorId,
          input.actorType,
          input.operation,
          "JOB",
          input.jobId,
          input.expectedVersion + 1,
          null,
          input.requestId,
          input.expectedState,
          "QUEUED",
          "{}",
          input.nowUtc,
        ),
    ];
    const results = await this.database.batch(statements);
    if (results[0]?.meta?.changes !== 1) throw new StaleWorkflowError();
  }
}
