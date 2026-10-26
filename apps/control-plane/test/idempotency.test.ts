import { describe, expect, it } from "vitest";

import {
  IdempotencyConflictError,
  IdempotencyInProgressError,
  IdempotencyService,
  type IdempotencyStore,
} from "../src/services/idempotency-service";

class MemoryIdempotencyStore implements IdempotencyStore {
  readonly records = new Map<string, { requestHash: string; responseBody: unknown; responseStatus: number }>();

  private key(input: { actorId: string; idempotencyKey: string; operation: string }): string {
    return `${input.actorId}:${input.operation}:${input.idempotencyKey}`;
  }

  async reserve(input: { actorId: string; idempotencyKey: string; operation: string; requestHash: string }): Promise<boolean> {
    if (this.records.has(this.key(input))) return false;
    this.records.set(this.key(input), { requestHash: input.requestHash, responseBody: null, responseStatus: 0 });
    return true;
  }

  async complete(input: {
    actorId: string;
    idempotencyKey: string;
    operation: string;
    responseBody: unknown;
    responseStatus: number;
  }): Promise<void> {
    const record = this.records.get(this.key(input))!;
    this.records.set(this.key(input), { ...record, responseBody: input.responseBody, responseStatus: input.responseStatus });
  }

  async release(input: { actorId: string; idempotencyKey: string; operation: string }): Promise<void> {
    this.records.delete(this.key(input));
  }

  async find(input: { actorId: string; idempotencyKey: string; operation: string }) {
    return this.records.get(this.key(input)) ?? null;
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
  it("runs a mutation once when duplicate requests race on the same key", async () => {
    const store = new MemoryIdempotencyStore();
    const service = new IdempotencyService(store, () => "record-1", () => "2026-08-04T00:00:00Z");
    const input = {
      actorId: "admin-1",
      body: { title: "Pilot" },
      idempotencyKey: "client-key-race",
      operation: "CREATE_IMPORT",
      route: "/api/v1/projects/project-1/imports",
    };
    let executions = 0;
    let finish: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      finish = resolve;
    });

    const first = service.execute(input, async () => {
      executions += 1;
      await gate;
      return { body: { jobId: "job-1" }, status: 202 };
    });
    const duplicate = service.execute(input, async () => {
      executions += 1;
      return { body: { jobId: "job-2" }, status: 202 };
    });

    await expect(duplicate).rejects.toBeInstanceOf(IdempotencyInProgressError);
    finish();
    await expect(first).resolves.toMatchObject({ body: { jobId: "job-1" }, replayed: false });
    expect(executions).toBe(1);
  });

  it("releases the key when the mutation fails so the client can retry", async () => {
    const store = new MemoryIdempotencyStore();
    const service = new IdempotencyService(store, () => "record-1", () => "2026-08-04T00:00:00Z");
    const input = {
      actorId: "admin-1",
      body: { title: "Pilot" },
      idempotencyKey: "client-key-retry",
      operation: "CREATE_IMPORT",
      route: "/api/v1/projects/project-1/imports",
    };

    await expect(
      service.execute(input, async () => {
        throw new Error("transient");
      }),
    ).rejects.toThrow("transient");
    await expect(service.execute(input, async () => ({ body: { jobId: "job-1" }, status: 202 }))).resolves.toMatchObject({
      replayed: false,
    });
  });
});
