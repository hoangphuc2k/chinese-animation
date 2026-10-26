import { MAX_STEP_ATTEMPTS } from "../services/lease-service";

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
    stage: "RETENTION_EXPIRY" | "RIGHTS_REVOCATION" | "FAILED_ARTIFACT_CLEANUP" | "ATTEMPTS_EXHAUSTED";
  }): void;
}

const humanOrTerminalStates = [
  "WAITING_SCRIPT_REVIEW",
  "WAITING_PUBLISH_APPROVAL",
  "TIMING_REVIEW_REQUIRED",
  "MANUAL_REVIEW_REQUIRED",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
  "EXPIRED",
  "RIGHTS_BLOCKED",
] as const;

interface RetentionCandidate extends Record<string, unknown> {
  id: string;
  project_id: string;
  source_asset_id: string;
  status: string;
  version: number;
}

/** Statements after the job update only take effect when that update actually moved the job. */
function transitionedGuard(candidate: RetentionCandidate, nextState: string | null): { sql: string; values: unknown[] } {
  return nextState === null
    ? { sql: "EXISTS (SELECT 1 FROM jobs WHERE id = ? AND status = ? AND version = ?)", values: [candidate.id, candidate.status, candidate.version] }
    : { sql: "EXISTS (SELECT 1 FROM jobs WHERE id = ? AND status = ? AND version = ?)", values: [candidate.id, nextState, candidate.version + 1] };
}

function cleanupInsert(
  database: RetentionDatabase,
  candidate: RetentionCandidate,
  cleanupJobId: string,
  nowUtc: string,
  nextState: string | null,
): RetentionStatement {
  const guard = transitionedGuard(candidate, nextState);
  return database
    .prepare(
      `INSERT INTO jobs (id, project_id, source_asset_id, job_type, status, step, priority, available_at, attempt_count, created_at, updated_at, target_job_id) SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE ${guard.sql} ON CONFLICT DO NOTHING`,
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
      candidate.id,
      ...guard.values,
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
  const guard = transitionedGuard(candidate, nextState);
  return database
    .prepare(
      `INSERT INTO audit_events (id, actor_id, actor_type, operation, target_type, target_id, target_version, target_hash, request_id, previous_state, next_state, details_json, created_at) SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE ${guard.sql}`,
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
      ...guard.values,
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
  const guard = transitionedGuard(candidate, nextState);
  return database
    .prepare(
      `INSERT INTO job_events (id, job_id, event_type, previous_state, next_state, details_json, created_at) SELECT ?, ?, ?, ?, ?, ?, ? WHERE ${guard.sql}`,
    )
    .bind(id, candidate.id, eventType, previousState, nextState, JSON.stringify({ cleanupRequested: true }), nowUtc, ...guard.values);
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

    const failed = await this.expiredFailureCandidates(input.nowUtc);
    for (const candidate of failed) await this.cleanUpFailure(candidate, input);

    const exhausted = await this.exhaustedAttemptCandidates(input.nowUtc);
    for (const candidate of exhausted) await this.requireManualReview(candidate, input);
  }

  private async expiredPreviewCandidates(nowUtc: string): Promise<RetentionCandidate[]> {
    const result = await this.database
      .prepare(
        "SELECT j.id, j.project_id, j.source_asset_id, j.status, j.version FROM jobs j WHERE j.job_type != ? AND j.retention_deadline IS NOT NULL AND j.retention_deadline <= ? AND j.status IN (?, ?) AND NOT EXISTS (SELECT 1 FROM jobs cleanup WHERE cleanup.target_job_id = j.id AND cleanup.job_type = ?)",
      )
      .bind("DELETE_EXPIRED", nowUtc, "READY_FOR_PREVIEW", "WAITING_PUBLISH_APPROVAL", "DELETE_EXPIRED")
      .all<RetentionCandidate>();
    return result.results;
  }

  private async revokedRightsCandidates(nowUtc: string): Promise<RetentionCandidate[]> {
    const result = await this.database
      .prepare(
        "SELECT j.id, j.project_id, j.source_asset_id, j.status, j.version FROM jobs j JOIN source_assets source ON source.id = j.source_asset_id WHERE j.job_type != ? AND source.rights_revoked_at IS NOT NULL AND source.rights_revoked_at <= ? AND j.status NOT IN (?, ?, ?, ?, ?) AND NOT EXISTS (SELECT 1 FROM jobs cleanup WHERE cleanup.target_job_id = j.id AND cleanup.job_type = ?)",
      )
      .bind("DELETE_EXPIRED", nowUtc, "COMPLETED", "CANCELLED", "EXPIRED", "RIGHTS_BLOCKED", "FAILED", "DELETE_EXPIRED")
      .all<RetentionCandidate>();
    return result.results;
  }

  /** Failed-job artifacts are kept for seven days from the terminal failure (spec section 10). */
  private async expiredFailureCandidates(nowUtc: string): Promise<RetentionCandidate[]> {
    const result = await this.database
      .prepare(
        "SELECT j.id, j.project_id, j.source_asset_id, j.status, j.version FROM jobs j WHERE j.job_type != ? AND j.status = ? AND j.retention_deadline IS NOT NULL AND j.retention_deadline <= ? AND NOT EXISTS (SELECT 1 FROM jobs cleanup WHERE cleanup.target_job_id = j.id AND cleanup.job_type = ?)",
      )
      .bind("DELETE_EXPIRED", "FAILED", nowUtc, "DELETE_EXPIRED")
      .all<RetentionCandidate>();
    return result.results;
  }

  /** Jobs whose current step exhausted its automated attempts and no longer hold a live lease. */
  private async exhaustedAttemptCandidates(nowUtc: string): Promise<RetentionCandidate[]> {
    const result = await this.database
      .prepare(
        `SELECT j.id, j.project_id, j.source_asset_id, j.status, j.version FROM jobs j WHERE j.attempt_count >= ? AND (j.lease_until IS NULL OR j.lease_until <= ?) AND j.status NOT IN (${humanOrTerminalStates.map(() => "?").join(", ")})`,
      )
      .bind(MAX_STEP_ATTEMPTS, nowUtc, ...humanOrTerminalStates)
      .all<RetentionCandidate>();
    return result.results;
  }

  private async expire(candidate: RetentionCandidate, input: RetentionRunInput): Promise<void> {
    const startedAt = Date.now();
    const guard = transitionedGuard(candidate, "EXPIRED");
    await this.database.batch([
      this.jobStateUpdate(candidate, "EXPIRED", "EXPIRED", input.nowUtc),
      this.database
        .prepare(
          `UPDATE destination_publications SET status = ?, updated_at = ? WHERE job_id = ? AND status NOT IN (?, ?) AND ${guard.sql}`,
        )
        .bind("MANUAL_ACTION_REQUIRED", input.nowUtc, candidate.id, "PUBLISHED", "CANCELLED", ...guard.values),
      cleanupInsert(this.database, candidate, this.newId(), input.nowUtc, "EXPIRED"),
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
    this.log(candidate, input, startedAt, "RETENTION_EXPIRY");
  }

  private async blockRights(candidate: RetentionCandidate, input: RetentionRunInput): Promise<void> {
    const startedAt = Date.now();
    const guard = transitionedGuard(candidate, "RIGHTS_BLOCKED");
    await this.database.batch([
      this.jobStateUpdate(candidate, "RIGHTS_BLOCKED", "RIGHTS_BLOCKED", input.nowUtc),
      this.database
        .prepare(
          `UPDATE destination_publications SET status = ?, updated_at = ? WHERE job_id = ? AND status NOT IN (?, ?) AND ${guard.sql}`,
        )
        .bind("MANUAL_ACTION_REQUIRED", input.nowUtc, candidate.id, "PUBLISHED", "CANCELLED", ...guard.values),
      // Published content is never altered automatically; it is flagged for manual review (spec section 6).
      this.database
        .prepare(
          `UPDATE destination_publications SET rights_review_required_at = COALESCE(rights_review_required_at, ?), updated_at = ? WHERE job_id = ? AND status = ? AND ${guard.sql}`,
        )
        .bind(input.nowUtc, input.nowUtc, candidate.id, "PUBLISHED", ...guard.values),
      cleanupInsert(this.database, candidate, this.newId(), input.nowUtc, "RIGHTS_BLOCKED"),
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
    this.log(candidate, input, startedAt, "RIGHTS_REVOCATION");
  }

  private async cleanUpFailure(candidate: RetentionCandidate, input: RetentionRunInput): Promise<void> {
    const startedAt = Date.now();
    await this.database.batch([cleanupInsert(this.database, candidate, this.newId(), input.nowUtc, null)]);
    this.log(candidate, input, startedAt, "FAILED_ARTIFACT_CLEANUP");
  }

  private async requireManualReview(candidate: RetentionCandidate, input: RetentionRunInput): Promise<void> {
    const startedAt = Date.now();
    await this.database.batch([
      this.jobStateUpdate(candidate, "MANUAL_REVIEW_REQUIRED", "ATTEMPTS_EXHAUSTED", input.nowUtc),
      eventInsert(this.database, candidate, "ATTEMPTS_EXHAUSTED", candidate.status, "MANUAL_REVIEW_REQUIRED", input.nowUtc, this.newId()),
      auditInsert(
        this.database,
        candidate,
        "ATTEMPTS_EXHAUSTED",
        candidate.status,
        "MANUAL_REVIEW_REQUIRED",
        input.requestId,
        input.nowUtc,
        this.newId(),
      ),
    ]);
    this.log(candidate, input, startedAt, "ATTEMPTS_EXHAUSTED");
  }

  private jobStateUpdate(candidate: RetentionCandidate, nextState: string, step: string, nowUtc: string): RetentionStatement {
    return this.database
      .prepare(
        "UPDATE jobs SET status = ?, step = ?, lease_owner = NULL, lease_token_hash = NULL, lease_until = NULL, version = version + 1, updated_at = ? WHERE id = ? AND status = ? AND version = ?",
      )
      .bind(nextState, step, nowUtc, candidate.id, candidate.status, candidate.version);
  }

  private log(
    candidate: RetentionCandidate,
    input: RetentionRunInput,
    startedAt: number,
    stage: Parameters<RetentionLogger["info"]>[0]["stage"],
  ): void {
    this.logger?.info({
      durationMs: Date.now() - startedAt,
      jobId: candidate.id,
      projectId: candidate.project_id,
      requestId: input.requestId,
      stage,
    });
  }
}
