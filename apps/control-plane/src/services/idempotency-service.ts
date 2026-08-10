export interface StoredIdempotencyResult {
  requestHash: string;
  responseBody: unknown;
  responseStatus: number;
}

export interface IdempotencyStore {
  create(input: {
    actorId: string;
    createdAtUtc: string;
    id: string;
    idempotencyKey: string;
    operation: string;
    requestHash: string;
    responseBody: unknown;
    responseStatus: number;
  }): Promise<void>;
  find(input: { actorId: string; idempotencyKey: string; operation: string }): Promise<StoredIdempotencyResult | null>;
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

export function createIdempotencyStore(repository: IdempotencyRepository): IdempotencyStore {
  return {
    async create(input) {
      await repository.create({
        actorId: input.actorId,
        createdAtUtc: input.createdAtUtc,
        id: input.id,
        idempotencyKey: input.idempotencyKey,
        operation: input.operation,
        requestHash: input.requestHash,
        responseJson: JSON.stringify(input.responseBody),
        responseStatus: input.responseStatus,
      });
    },
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

function canonicalJson(value: unknown): string {
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
    const existing = await this.store.find(input);
    if (existing) {
      if (existing.requestHash !== requestHash) throw new IdempotencyConflictError();
      return { body: existing.responseBody, replayed: true, status: existing.responseStatus };
    }
    const response = await mutation();
    await this.store.create({
      actorId: input.actorId,
      createdAtUtc: this.nowUtc(),
      id: this.newId(),
      idempotencyKey: input.idempotencyKey,
      operation: input.operation,
      requestHash,
      responseBody: response.body,
      responseStatus: response.status,
    });
    return { ...response, replayed: false };
  }
}
import type { IdempotencyRepository } from "../repositories/idempotency";
