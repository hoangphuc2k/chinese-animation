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
  staleBeforeUtc: string;
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

  /**
   * Inserts a pending record; the unique key makes concurrent duplicates lose atomically. A pending record
   * older than `staleBeforeUtc` belongs to a request that died, so it is taken over instead.
   */
  async reserve(input: ReserveIdempotencyRecordInput): Promise<boolean> {
    const inserted = await this.database
      .prepare(
        "INSERT INTO idempotency_records (id, actor_id, operation, idempotency_key, request_hash, response_status, response_json, created_at) VALUES (?, ?, ?, ?, ?, 0, 'null', ?) ON CONFLICT (actor_id, operation, idempotency_key) DO UPDATE SET id = excluded.id, request_hash = excluded.request_hash, created_at = excluded.created_at WHERE idempotency_records.response_status = 0 AND idempotency_records.created_at < ? RETURNING id",
      )
      .bind(
        input.id,
        input.actorId,
        input.operation,
        input.idempotencyKey,
        input.requestHash,
        input.createdAtUtc,
        input.staleBeforeUtc,
      )
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
