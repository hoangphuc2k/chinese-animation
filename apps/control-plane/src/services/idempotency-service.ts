export interface StoredIdempotencyResult {
  requestHash: string;
  responseBody: unknown;
  responseStatus: number;
}

export interface IdempotencyKeyInput {
  actorId: string;
  idempotencyKey: string;
  operation: string;
}

/** A reserved key whose mutation has not finished yet stores this response status. */
export const PENDING_RESPONSE_STATUS = 0;
/** A pending reservation older than this is treated as abandoned (the request died) and may be reclaimed. */
export const PENDING_RESERVATION_TTL_MS = 5 * 60 * 1000;

export interface IdempotencyStore {
  /**
   * Atomically claims the key. Returns false when a finished record holds it, or a pending one created at
   * or after `staleBeforeUtc`; an older pending record is reclaimed.
   */
  reserve(
    input: IdempotencyKeyInput & { createdAtUtc: string; id: string; requestHash: string; staleBeforeUtc: string },
  ): Promise<boolean>;
  complete(input: IdempotencyKeyInput & { responseBody: unknown; responseStatus: number }): Promise<void>;
  release(input: IdempotencyKeyInput): Promise<void>;
  find(input: IdempotencyKeyInput): Promise<StoredIdempotencyResult | null>;
}

export interface IdempotentMutationInput {
  actorId: string;
  body: unknown;
  idempotencyKey: string;
  operation: string;
  route: string;
}

export interface MutationResponse {
  body: unknown;
  status: number;
}

export interface IdempotentMutationResponse extends MutationResponse {
  replayed: boolean;
}

export class IdempotencyConflictError extends Error {
  constructor() {
    super("Idempotency-Key was already used for a different request.");
  }
}

export class IdempotencyInProgressError extends IdempotencyConflictError {
  constructor() {
    super();
    this.message = "A request with this Idempotency-Key is still in progress; retry later.";
  }
}

export function createIdempotencyStore(repository: IdempotencyRepository): IdempotencyStore {
  return {
    reserve: (input) => repository.reserve(input),
    async complete(input) {
      await repository.complete({ ...input, responseJson: JSON.stringify(input.responseBody) });
    },
    release: (input) => repository.release(input),
    async find(input) {
      const record = await repository.find(input);
      if (!record) return null;
      try {
        return {
          requestHash: record.request_hash,
          responseBody: JSON.parse(record.response_json) as unknown,
          responseStatus: record.response_status,
        };
      } catch {
        throw new TypeError("Stored idempotency response is invalid JSON.");
      }
    },
  };
}

export function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "string" || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new TypeError("Mutation body must use finite numbers.");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value === "object") {
    const object = value as Record<string, unknown>;
    return `{${Object.keys(object)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(object[key])}`)
      .join(",")}}`;
  }
  throw new TypeError("Mutation body must be JSON-compatible.");
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export class IdempotencyService {
  constructor(
    private readonly store: IdempotencyStore,
    private readonly newId: () => string = () => crypto.randomUUID(),
    private readonly nowUtc: () => string = () => new Date().toISOString(),
  ) {}

  async execute(
    input: IdempotentMutationInput,
    mutation: () => Promise<MutationResponse>,
  ): Promise<IdempotentMutationResponse> {
    if (!input.idempotencyKey) throw new TypeError("Idempotency-Key is required.");
    const requestHash = await sha256(canonicalJson({ actorId: input.actorId, body: input.body, route: input.route }));
    const key = { actorId: input.actorId, idempotencyKey: input.idempotencyKey, operation: input.operation };
    const createdAtUtc = this.nowUtc();
    const staleBeforeUtc = new Date(new Date(createdAtUtc).getTime() - PENDING_RESERVATION_TTL_MS).toISOString();
    const reserved = await this.store.reserve({ ...key, createdAtUtc, id: this.newId(), requestHash, staleBeforeUtc });
    if (!reserved) {
      const existing = await this.store.find(key);
      if (existing && existing.requestHash !== requestHash) throw new IdempotencyConflictError();
      if (!existing || existing.responseStatus === PENDING_RESPONSE_STATUS) throw new IdempotencyInProgressError();
      return { body: existing.responseBody, replayed: true, status: existing.responseStatus };
    }
    let response: MutationResponse;
    try {
      response = await mutation();
    } catch (error) {
      await this.store.release(key);
      throw error;
    }
    await this.store.complete({ ...key, responseBody: response.body, responseStatus: response.status });
    return { ...response, replayed: false };
  }
}
import type { IdempotencyRepository } from "../repositories/idempotency";
