import type { SqlDatabase } from "./sql";

export interface IdempotencyRecord extends Record<string, unknown> {
  actor_id: string;
  created_at: string;
  id: string;
  idempotency_key: string;
  operation: string;
  request_hash: string;
  response_json: string;
  response_status: number;
}

export interface FindIdempotencyRecordInput {
  actorId: string;
  idempotencyKey: string;
  operation: string;
}

export interface CreateIdempotencyRecordInput {
  actorId: string;
  createdAtUtc: string;
  id: string;
  idempotencyKey: string;
  operation: string;
  requestHash: string;
  responseJson: string;
  responseStatus: number;
}

export class IdempotencyRepository {
  constructor(private readonly database: SqlDatabase) {}

  async find(input: FindIdempotencyRecordInput): Promise<IdempotencyRecord | null> {
    return this.database
      .prepare(
        "SELECT * FROM idempotency_records WHERE actor_id = ? AND operation = ? AND idempotency_key = ?",
      )
      .bind(input.actorId, input.operation, input.idempotencyKey)
      .first<IdempotencyRecord>();
  }

  async create(input: CreateIdempotencyRecordInput): Promise<void> {
    await this.database
      .prepare(
        "INSERT INTO idempotency_records (id, actor_id, operation, idempotency_key, request_hash, response_status, response_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
      )
      .bind(
        input.id,
        input.actorId,
        input.operation,
        input.idempotencyKey,
        input.requestHash,
        input.responseStatus,
        input.responseJson,
        input.createdAtUtc,
      )
      .run();
  }
}
