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

export interface ReserveIdempotencyRecordInput extends FindIdempotencyRecordInput {
  createdAtUtc: string;
  id: string;
  requestHash: string;
}

export interface CompleteIdempotencyRecordInput extends FindIdempotencyRecordInput {
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

  /** Inserts a pending record; the unique key makes concurrent duplicates lose atomically. */
  async reserve(input: ReserveIdempotencyRecordInput): Promise<boolean> {
    const inserted = await this.database
      .prepare(
        "INSERT INTO idempotency_records (id, actor_id, operation, idempotency_key, request_hash, response_status, response_json, created_at) VALUES (?, ?, ?, ?, ?, 0, 'null', ?) ON CONFLICT (actor_id, operation, idempotency_key) DO NOTHING RETURNING id",
      )
      .bind(input.id, input.actorId, input.operation, input.idempotencyKey, input.requestHash, input.createdAtUtc)
      .first<{ id: string }>();
    return inserted !== null;
  }

  async complete(input: CompleteIdempotencyRecordInput): Promise<void> {
    await this.database
      .prepare(
        "UPDATE idempotency_records SET response_status = ?, response_json = ? WHERE actor_id = ? AND operation = ? AND idempotency_key = ? AND response_status = 0",
      )
      .bind(input.responseStatus, input.responseJson, input.actorId, input.operation, input.idempotencyKey)
      .run();
  }

  async release(input: FindIdempotencyRecordInput): Promise<void> {
    await this.database
      .prepare(
        "DELETE FROM idempotency_records WHERE actor_id = ? AND operation = ? AND idempotency_key = ? AND response_status = 0",
      )
      .bind(input.actorId, input.operation, input.idempotencyKey)
      .run();
  }
}
