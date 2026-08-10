import type { SqlDatabase } from "./sql";

export interface AppendApprovalInput {
  actorId: string;
  approvalType: string;
  approvedAtUtc: string;
  contentHash: string | null;
  destinationsJson: string | null;
  id: string;
  jobId: string;
  metadataHash: string | null;
  scheduleAtUtc: string | null;
  version: number;
}

export class ApprovalsRepository {
  constructor(private readonly database: SqlDatabase) {}

  async append(input: AppendApprovalInput): Promise<void> {
    await this.database
      .prepare(
        "INSERT INTO approvals (id, job_id, approval_type, version, content_hash, metadata_hash, destinations_json, schedule_at_utc, actor_id, approved_at, invalidated_at, invalidation_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
      )
      .bind(
        input.id,
        input.jobId,
        input.approvalType,
        input.version,
        input.contentHash,
        input.metadataHash,
        input.destinationsJson,
        input.scheduleAtUtc,
        input.actorId,
        input.approvedAtUtc,
      )
      .run();
  }
}
