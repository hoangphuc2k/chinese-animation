import { describe, expect, it } from "vitest";

import {
  IdempotencyConflictError,
  IdempotencyService,
  type IdempotencyStore,
} from "../src/services/idempotency-service";

class MemoryIdempotencyStore implements IdempotencyStore {
  readonly records = new Map<string, { requestHash: string; responseBody: unknown; responseStatus: number }>();

  async create(input: {
    actorId: string;
    idempotencyKey: string;
    operation: string;
    requestHash: string;
    responseBody: unknown;
    responseStatus: number;
  }): Promise<void> {
    this.records.set(`${input.actorId}:${input.operation}:${input.idempotencyKey}`, input);
  }

  async find(input: { actorId: string; idempotencyKey: string; operation: string }) {
    return this.records.get(`${input.actorId}:${input.operation}:${input.idempotencyKey}`) ?? null;
  }
}

describe("idempotent mutation service", () => {
  it("replays the original status and body for the same canonical mutation without rerunning it", async () => {
    const store = new MemoryIdempotencyStore();
    const service = new IdempotencyService(store, () => "record-1", () => "2026-08-04T00:00:00Z");
    let executions = 0;
    const input = {
      actorId: "admin-1",
      body: { source: { url: "https://creator.example.test/video" }, title: "Pilot" },
      idempotencyKey: "client-key-1",
      operation: "CREATE_IMPORT",
      route: "/api/v1/projects/project-1/imports",
    };

    const first = await service.execute(input, async () => {
      executions += 1;
      return { body: { jobId: "job-1", status: "QUEUED" }, status: 202 };
    });
    const replay = await service.execute(input, async () => {
      executions += 1;
      return { body: { jobId: "job-2" }, status: 202 };
    });

    expect(first).toEqual({ body: { jobId: "job-1", status: "QUEUED" }, replayed: false, status: 202 });
    expect(replay).toEqual({ body: { jobId: "job-1", status: "QUEUED" }, replayed: true, status: 202 });
    expect(executions).toBe(1);
  });

  it("rejects a key reused for a different canonical request body", async () => {
    const store = new MemoryIdempotencyStore();
    const service = new IdempotencyService(store, () => "record-1", () => "2026-08-04T00:00:00Z");
    const base = {
      actorId: "admin-1",
      idempotencyKey: "client-key-1",
      operation: "CREATE_IMPORT",
      route: "/api/v1/projects/project-1/imports",
    };
    await service.execute({ ...base, body: { priority: 1 } }, async () => ({ body: { jobId: "job-1" }, status: 202 }));

    await expect(
      service.execute({ ...base, body: { priority: 2 } }, async () => ({ body: { jobId: "job-2" }, status: 202 })),
    ).rejects.toThrow(IdempotencyConflictError);
  });

  it("canonicalizes object field order before hashing actor, route, and body", async () => {
    const store = new MemoryIdempotencyStore();
    const service = new IdempotencyService(store, () => "record-1", () => "2026-08-04T00:00:00Z");
    const base = {
      actorId: "admin-1",
      idempotencyKey: "client-key-1",
      operation: "CREATE_IMPORT",
      route: "/api/v1/projects/project-1/imports",
    };
    await service.execute({ ...base, body: { nested: { a: 1, b: 2 }, priority: 1 } }, async () => ({ body: { jobId: "job-1" }, status: 202 }));

    const replay = await service.execute(
      { ...base, body: { priority: 1, nested: { b: 2, a: 1 } } },
      async () => ({ body: { jobId: "job-2" }, status: 202 }),
    );

    expect(replay).toEqual({ body: { jobId: "job-1" }, replayed: true, status: 202 });
  });
});
