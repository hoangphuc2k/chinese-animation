interface RetentionStatement {
  all<Row extends Record<string, unknown>>(): Promise<{ results: Row[] }>;
  bind(...values: unknown[]): RetentionStatement;
  first<Row extends Record<string, unknown>>(): Promise<Row | null>;
  run(): Promise<{ meta?: { changes?: number }; results: Record<string, unknown>[] }>;
}

export interface RetentionDatabase {
  batch(statements: RetentionStatement[]): Promise<Array<{ meta?: { changes?: number }; results: Record<string, unknown>[] }>>;
  prepare(sql: string): RetentionStatement;
}

export interface RetentionRunInput {
  nowUtc: string;
  requestId: string;
}

export interface RetentionLogger {
  info(fields: {
    durationMs: number;
    jobId: string;
    projectId: string;
    requestId: string;
    stage: "RETENTION_EXPIRY" | "RIGHTS_REVOCATION";
  }): void;
}

interface RetentionCandidate extends Record<string, unknown> {
  id: string;
  project_id: string;
  source_asset_id: string;
  status: string;
  version: number;
}

function cleanupInsert(
  database: RetentionDatabase,
  candidate: RetentionCandidate,
  cleanupJobId: string,
  nowUtc: string,
): RetentionStatement {
  return database
    .prepare(
      "INSERT INTO jobs (id, project_id, source_asset_id, job_type, status, step, priority, available_at, attempt_count, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
    )
    .bind(
      cleanupJobId,
      candidate.project_id,
      candidate.source_asset_id,
      "DELETE_EXPIRED",
      "QUEUED",
      "DELETE_EXPIRED",
      100,
      nowUtc,
      0,
      nowUtc,
      nowUtc,
    );
}

function auditInsert(
  database: RetentionDatabase,
  candidate: RetentionCandidate,
  operation: string,
  previousState: string,
  nextState: string,
  requestId: string,
  nowUtc: string,
  id: string,
): RetentionStatement {
  return database
    .prepare(
      "INSERT INTO audit_events (id, actor_id, actor_type, operation, target_type, target_id, target_version, target_hash, request_id, previous_state, next_state, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    )
    .bind(
      id,
      "control-plane-retention",
      "SERVICE",
      operation,
      "JOB",
      candidate.id,
      candidate.version + 1,
      null,
      requestId,
      previousState,
      nextState,
      JSON.stringify({ cleanupRequested: true }),
      nowUtc,
    );
}

function eventInsert(
  database: RetentionDatabase,
  candidate: RetentionCandidate,
  eventType: string,
  previousState: string,
  nextState: string,
  nowUtc: string,
  id: string,
): RetentionStatement {
  return database
    .prepare(
      "INSERT INTO job_events (id, job_id, event_type, previous_state, next_state, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
    )
    .bind(id, candidate.id, eventType, previousState, nextState, JSON.stringify({ cleanupRequested: true }), nowUtc);
}

export class RetentionService {
  constructor(
    private readonly database: RetentionDatabase,
    private readonly newId: () => string = () => crypto.randomUUID(),
    private readonly logger?: RetentionLogger,
  ) {}

  async run(input: RetentionRunInput): Promise<void> {
    const expired = await this.expiredPreviewCandidates(input.nowUtc);
    for (const candidate of expired) await this.expire(candidate, input);

    const revoked = await this.revokedRightsCandidates(input.nowUtc);
    for (const candidate of revoked) await this.blockRights(candidate, input);
  }

  private async expiredPreviewCandidates(nowUtc: string): Promise<RetentionCandidate[]> {
    const result = await this.database
      .prepare(
        "SELECT j.id, j.project_id, j.source_asset_id, j.status, j.version FROM jobs j WHERE j.job_type != ? AND j.retention_deadline IS NOT NULL AND j.retention_deadline <= ? AND j.status IN (?, ?) AND NOT EXISTS (SELECT 1 FROM jobs cleanup WHERE cleanup.source_asset_id = j.source_asset_id AND cleanup.job_type = ?)",
      )
      .bind("DELETE_EXPIRED", nowUtc, "READY_FOR_PREVIEW", "WAITING_PUBLISH_APPROVAL", "DELETE_EXPIRED")
      .all<RetentionCandidate>();
    return result.results;
  }

  private async revokedRightsCandidates(nowUtc: string): Promise<RetentionCandidate[]> {
    const result = await this.database
      .prepare(
        "SELECT j.id, j.project_id, j.source_asset_id, j.status, j.version FROM jobs j JOIN source_assets source ON source.id = j.source_asset_id WHERE j.job_type != ? AND source.rights_revoked_at IS NOT NULL AND source.rights_revoked_at <= ? AND j.status NOT IN (?, ?, ?, ?, ?) AND NOT EXISTS (SELECT 1 FROM jobs cleanup WHERE cleanup.source_asset_id = j.source_asset_id AND cleanup.job_type = ?)",
      )
      .bind("DELETE_EXPIRED", nowUtc, "COMPLETED", "CANCELLED", "EXPIRED", "RIGHTS_BLOCKED", "FAILED", "DELETE_EXPIRED")
      .all<RetentionCandidate>();
    return result.results;
  }

  private async expire(candidate: RetentionCandidate, input: RetentionRunInput): Promise<void> {
    const startedAt = Date.now();
    await this.database.batch([
      this.database
        .prepare(
          "UPDATE jobs SET status = ?, step = ?, lease_owner = NULL, lease_token_hash = NULL, lease_until = NULL, version = version + 1, updated_at = ? WHERE id = ? AND status = ? AND version = ?",
        )
        .bind("EXPIRED", "EXPIRED", input.nowUtc, candidate.id, candidate.status, candidate.version),
      this.database
        .prepare(
          "UPDATE destination_publications SET status = ?, updated_at = ? WHERE job_id = ? AND status NOT IN (?, ?)",
        )
        .bind("MANUAL_ACTION_REQUIRED", input.nowUtc, candidate.id, "PUBLISHED", "CANCELLED"),
      cleanupInsert(this.database, candidate, this.newId(), input.nowUtc),
      eventInsert(this.database, candidate, "RETENTION_EXPIRED", candidate.status, "EXPIRED", input.nowUtc, this.newId()),
      auditInsert(
        this.database,
        candidate,
        "RETENTION_EXPIRY_REQUESTED",
        candidate.status,
        "EXPIRED",
        input.requestId,
        input.nowUtc,
        this.newId(),
      ),
    ]);
    this.logger?.info({
      durationMs: Date.now() - startedAt,
      jobId: candidate.id,
      projectId: candidate.project_id,
      requestId: input.requestId,
      stage: "RETENTION_EXPIRY",
    });
  }

  private async blockRights(candidate: RetentionCandidate, input: RetentionRunInput): Promise<void> {
    const startedAt = Date.now();
    await this.database.batch([
      this.database
        .prepare(
          "UPDATE jobs SET status = ?, step = ?, lease_owner = NULL, lease_token_hash = NULL, lease_until = NULL, version = version + 1, updated_at = ? WHERE id = ? AND status = ? AND version = ?",
        )
        .bind("RIGHTS_BLOCKED", "RIGHTS_BLOCKED", input.nowUtc, candidate.id, candidate.status, candidate.version),
      this.database
        .prepare(
          "UPDATE destination_publications SET status = ?, updated_at = ? WHERE job_id = ? AND status NOT IN (?, ?)",
        )
        .bind("MANUAL_ACTION_REQUIRED", input.nowUtc, candidate.id, "PUBLISHED", "CANCELLED"),
      cleanupInsert(this.database, candidate, this.newId(), input.nowUtc),
      eventInsert(this.database, candidate, "RIGHTS_REVOKED", candidate.status, "RIGHTS_BLOCKED", input.nowUtc, this.newId()),
      auditInsert(
        this.database,
        candidate,
        "RIGHTS_REVOKED",
        candidate.status,
        "RIGHTS_BLOCKED",
        input.requestId,
        input.nowUtc,
        this.newId(),
      ),
    ]);
    this.logger?.info({
      durationMs: Date.now() - startedAt,
      jobId: candidate.id,
      projectId: candidate.project_id,
      requestId: input.requestId,
      stage: "RIGHTS_REVOCATION",
    });
  }
}
