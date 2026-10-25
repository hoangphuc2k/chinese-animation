import type { SqlDatabase } from "../repositories/sql";
import type { WorkflowDatabase } from "./job-service";

type ApprovalDatabase = SqlDatabase & WorkflowDatabase;

export interface SaveScriptEditInput {
  actorId: string;
  contentHash: string;
  jobId: string;
  metadataHash: string | null;
  nowUtc: string;
  requestId: string;
  scriptId: string;
  transcriptHash: string | null;
  translationHash: string | null;
  voiceSettingsHash: string | null;
}

export interface ApproveScriptInput {
  actorId: string;
  contentHash: string;
  jobId: string;
  nowUtc: string;
  requestId: string;
  scriptVersion: number;
}

export interface SaveRenderInput {
  actorId: string;
  artifactRef: string;
  byteCount: number;
  jobId: string;
  nowUtc: string;
  renderHash: string;
  renderId: string;
  requestId: string;
}

export interface ApprovePublishInput {
  actorId: string;
  destinations: readonly string[];
  jobId: string;
  metadataHash: string;
  nowUtc: string;
  renderId: string;
  renderVersion: number;
  requestId: string;
  scheduleAtUtc: string | null;
}

interface VersionRow extends Record<string, unknown> {
  content_hash: string;
  version: number;
}

interface JobStateRow extends Record<string, unknown> {
  status: string;
  version: number;
}

interface NextVersionRow extends Record<string, unknown> {
  version: number;
}

interface RenderVersionRow extends Record<string, unknown> {
  id: string;
  render_hash: string;
  version: number;
}

export class ApprovalMismatchError extends Error {
  constructor() {
    super("The requested version and hash do not match a current immutable record.");
  }
}

export class ApprovalService {
  constructor(
    private readonly database: ApprovalDatabase,
    private readonly newId: () => string = () => crypto.randomUUID(),
  ) {}

  async saveScriptEdit(input: SaveScriptEditInput): Promise<number> {
    const next = await this.database
      .prepare("SELECT COALESCE(MAX(version), 0) + 1 AS version FROM script_versions WHERE job_id = ?")
      .bind(input.jobId)
      .first<NextVersionRow>();
    const version = next?.version ?? 1;
    await this.database.batch([
      this.database
        .prepare(
          "INSERT INTO script_versions (id, job_id, version, content_hash, transcript_hash, translation_hash, metadata_hash, voice_settings_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        )
        .bind(
          input.scriptId,
          input.jobId,
          version,
          input.contentHash,
          input.transcriptHash,
          input.translationHash,
          input.metadataHash,
          input.voiceSettingsHash,
          input.nowUtc,
        ),
      this.database
        .prepare(
          "UPDATE approvals SET invalidated_at = ?, invalidation_reason = ? WHERE job_id = ? AND approval_type = ? AND invalidated_at IS NULL",
        )
        .bind(input.nowUtc, "SCRIPT_EDITED", input.jobId, "SCRIPT_APPROVED"),
      this.database
        .prepare(
          "INSERT INTO audit_events (id, actor_id, actor_type, operation, target_type, target_id, target_version, target_hash, request_id, previous_state, next_state, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        )
        .bind(
          this.newId(),
          input.actorId,
          "browser",
          "SCRIPT_EDITED",
          "SCRIPT_VERSION",
          input.scriptId,
          version,
          input.contentHash,
          input.requestId,
          null,
          null,
          "{}",
          input.nowUtc,
        ),
    ]);
    return version;
  }

  async approveScript(input: ApproveScriptInput): Promise<string> {
    const script = await this.database
      .prepare("SELECT version, content_hash FROM script_versions WHERE job_id = ? AND version = ?")
      .bind(input.jobId, input.scriptVersion)
      .first<VersionRow>();
    if (!script || script.content_hash !== input.contentHash) throw new ApprovalMismatchError();
    const latest = await this.database
      .prepare("SELECT MAX(version) AS version FROM script_versions WHERE job_id = ?")
      .bind(input.jobId)
      .first<NextVersionRow>();
    if (latest?.version !== input.scriptVersion) throw new ApprovalMismatchError();
    const job = await this.database
      .prepare("SELECT status, version FROM jobs WHERE id = ?")
      .bind(input.jobId)
      .first<JobStateRow>();
    const approvalId = this.newId();
    // Approving the latest script releases a job waiting for review to voice generation.
    const advance =
      job?.status === "WAITING_SCRIPT_REVIEW"
        ? [
            this.database
              .prepare(
                "UPDATE jobs SET status = 'GENERATING_VOICE', step = 'GENERATING_VOICE', version = version + 1, available_at = ?, updated_at = ? WHERE id = ? AND status = 'WAITING_SCRIPT_REVIEW' AND version = ?",
              )
              .bind(input.nowUtc, input.nowUtc, input.jobId, job.version),
            this.database
              .prepare(
                "INSERT INTO job_events (id, job_id, event_type, previous_state, next_state, details_json, created_at) SELECT ?, id, 'SCRIPT_APPROVED', 'WAITING_SCRIPT_REVIEW', 'GENERATING_VOICE', '{}', ? FROM jobs WHERE id = ? AND status = 'GENERATING_VOICE' AND version = ?",
              )
              .bind(this.newId(), input.nowUtc, input.jobId, job.version + 1),
          ]
        : [];
    await this.database.batch([
      ...advance,
      this.database
        .prepare(
          "UPDATE approvals SET invalidated_at = ?, invalidation_reason = ? WHERE job_id = ? AND approval_type = ? AND invalidated_at IS NULL",
        )
        .bind(input.nowUtc, "SUPERSEDED", input.jobId, "SCRIPT_APPROVED"),
      this.database
        .prepare(
          "INSERT INTO approvals (id, job_id, approval_type, version, content_hash, metadata_hash, destinations_json, schedule_at_utc, actor_id, approved_at, invalidated_at, invalidation_reason) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, NULL, NULL)",
        )
        .bind(approvalId, input.jobId, "SCRIPT_APPROVED", input.scriptVersion, input.contentHash, input.actorId, input.nowUtc),
      this.database
        .prepare(
          "INSERT INTO audit_events (id, actor_id, actor_type, operation, target_type, target_id, target_version, target_hash, request_id, previous_state, next_state, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        )
        .bind(
          this.newId(),
          input.actorId,
          "browser",
          "SCRIPT_APPROVED",
          "APPROVAL",
          approvalId,
          input.scriptVersion,
          input.contentHash,
          input.requestId,
          null,
          null,
          "{}",
          input.nowUtc,
        ),
    ]);
    return approvalId;
  }

  async saveRender(input: SaveRenderInput): Promise<number> {
    const next = await this.database
      .prepare("SELECT COALESCE(MAX(version), 0) + 1 AS version FROM render_versions WHERE job_id = ?")
      .bind(input.jobId)
      .first<NextVersionRow>();
    const version = next?.version ?? 1;
    await this.database.batch([
      this.database
        .prepare(
          "INSERT INTO render_versions (id, job_id, version, render_hash, byte_count, artifact_ref, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        )
        .bind(input.renderId, input.jobId, version, input.renderHash, input.byteCount, input.artifactRef, input.nowUtc),
      this.database
        .prepare(
          "UPDATE approvals SET invalidated_at = ?, invalidation_reason = ? WHERE job_id = ? AND approval_type = ? AND invalidated_at IS NULL",
        )
        .bind(input.nowUtc, "RENDER_CHANGED", input.jobId, "PUBLISH_APPROVED"),
      this.database
        .prepare(
          "INSERT INTO audit_events (id, actor_id, actor_type, operation, target_type, target_id, target_version, target_hash, request_id, previous_state, next_state, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        )
        .bind(
          this.newId(),
          input.actorId,
          "browser",
          "RENDER_RECORDED",
          "RENDER_VERSION",
          input.renderId,
          version,
          input.renderHash,
          input.requestId,
          null,
          null,
          "{}",
          input.nowUtc,
        ),
    ]);
    return version;
  }

  async approvePublish(input: ApprovePublishInput): Promise<{ approvalId: string; renderHash: string }> {
    const render = await this.database
      .prepare("SELECT id, version, render_hash FROM render_versions WHERE id = ? AND job_id = ? AND version = ?")
      .bind(input.renderId, input.jobId, input.renderVersion)
      .first<RenderVersionRow>();
    if (!render) throw new ApprovalMismatchError();
    const approvalId = this.newId();
    const statements = [
      this.database
        .prepare(
          "UPDATE approvals SET invalidated_at = ?, invalidation_reason = ? WHERE job_id = ? AND approval_type = ? AND invalidated_at IS NULL",
        )
        .bind(input.nowUtc, "SUPERSEDED", input.jobId, "PUBLISH_APPROVED"),
      this.database
        .prepare(
          "INSERT INTO approvals (id, job_id, approval_type, version, content_hash, metadata_hash, destinations_json, schedule_at_utc, actor_id, approved_at, invalidated_at, invalidation_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
        )
        .bind(
          approvalId,
          input.jobId,
          "PUBLISH_APPROVED",
          input.renderVersion,
          render.render_hash,
          input.metadataHash,
          JSON.stringify(input.destinations),
          input.scheduleAtUtc,
          input.actorId,
          input.nowUtc,
        ),
      ...input.destinations.map((destination) =>
        this.database
          .prepare(
            "INSERT INTO destination_publications (id, job_id, destination, provider_asset_id, provider_session_id, render_version_id, status, metadata_hash, scheduled_at_utc, created_at, updated_at) VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?) ON CONFLICT(job_id, destination) DO UPDATE SET render_version_id = excluded.render_version_id, status = excluded.status, metadata_hash = excluded.metadata_hash, scheduled_at_utc = excluded.scheduled_at_utc, updated_at = excluded.updated_at",
          )
          .bind(
            this.newId(),
            input.jobId,
            destination,
            input.renderId,
            "READY_TO_UPLOAD",
            input.metadataHash,
            input.scheduleAtUtc,
            input.nowUtc,
            input.nowUtc,
          ),
      ),
      this.database
        .prepare(
          "INSERT INTO audit_events (id, actor_id, actor_type, operation, target_type, target_id, target_version, target_hash, request_id, previous_state, next_state, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        )
        .bind(
          this.newId(),
          input.actorId,
          "browser",
          "PUBLISH_APPROVED",
          "APPROVAL",
          approvalId,
          input.renderVersion,
          render.render_hash,
          input.requestId,
          null,
          null,
          JSON.stringify({ destinations: input.destinations, metadataHash: input.metadataHash }),
          input.nowUtc,
        ),
    ];
    await this.database.batch(statements);
    return { approvalId, renderHash: render.render_hash };
  }
}
