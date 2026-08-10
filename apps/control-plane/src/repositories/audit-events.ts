import type { SqlDatabase } from "./sql";

export interface AppendAuditEventInput {
  actorId: string;
  actorType: string;
  createdAtUtc: string;
  detailsJson: string;
  id: string;
  nextState: string | null;
  operation: string;
  previousState: string | null;
  requestId: string;
  targetHash: string | null;
  targetId: string;
  targetType: string;
  targetVersion: number | null;
}

export class AuditEventsRepository {
  constructor(private readonly database: SqlDatabase) {}

  async append(input: AppendAuditEventInput): Promise<void> {
    await this.database
      .prepare(
        "INSERT INTO audit_events (id, actor_id, actor_type, operation, target_type, target_id, target_version, target_hash, request_id, previous_state, next_state, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      )
      .bind(
        input.id,
        input.actorId,
        input.actorType,
        input.operation,
        input.targetType,
        input.targetId,
        input.targetVersion,
        input.targetHash,
        input.requestId,
        input.previousState,
        input.nextState,
        input.detailsJson,
        input.createdAtUtc,
      )
      .run();
  }
}
