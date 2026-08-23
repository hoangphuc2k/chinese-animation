import { describe, expect, it } from "vitest";

import {
  assertDestinationTransition,
  DestinationTransitionError,
} from "../src/domain/destination-state-machine";
import { assertJobTransition, JobTransitionError } from "../src/domain/job-state-machine";
import { JobService, StaleWorkflowError, type WorkflowDatabase } from "../src/services/job-service";

class RecordingWorkflowDatabase implements WorkflowDatabase {
  readonly batches: Array<{ sql: string; values: unknown[] }[]> = [];

  constructor(private readonly changedRows = 1) {}

  prepare(sql: string) {
    const values: unknown[] = [];
    return {
      all: async () => ({ results: [] }),
      bind: (...next: unknown[]) => {
        values.push(...next);
        return this.prepareBound(sql, values);
      },
      first: async () => null,
      run: async () => ({ results: [] }),
    };
  }

  async batch(statements: ReturnType<RecordingWorkflowDatabase["prepare"]>[]) {
    this.batches.push(
      statements.map((statement) => {
        const entry = statement as unknown as { sql: string; values: unknown[] };
        return { sql: entry.sql, values: entry.values };
      }),
    );
    return [{ meta: { changes: this.changedRows }, results: [] }, { results: [] }, { results: [] }];
  }

  private prepareBound(sql: string, values: unknown[]) {
    return {
      all: async () => ({ results: [] }),
      bind: (...next: unknown[]) => this.prepareBound(sql, [...values, ...next]),
      first: async () => null,
      run: async () => ({ results: [] }),
      sql,
      values,
    };
  }
}

describe("job state machine", () => {
  it("allows only the next canonical processing step", () => {
    expect(() => assertJobTransition("QUEUED", "INGESTING")).not.toThrow();
    expect(() => assertJobTransition("INGESTING", "PROBING")).not.toThrow();
    expect(() => assertJobTransition("QUEUED", "PROBING")).toThrow(JobTransitionError);
  });

  it("rejects transitions from terminal processing states", () => {
    expect(() => assertJobTransition("COMPLETED", "QUEUED")).toThrow(JobTransitionError);
    expect(() => assertJobTransition("CANCELLED", "INGESTING")).toThrow(JobTransitionError);
  });

  it("routes voice approval and timing exceptions back to human review", () => {
    expect(() => assertJobTransition("GENERATING_VOICE", "WAITING_SCRIPT_REVIEW")).not.toThrow();
    expect(() => assertJobTransition("GENERATING_VOICE", "TIMING_REVIEW_REQUIRED")).not.toThrow();
  });
});

describe("destination state machine", () => {
  it("allows only destination-local publishing progression", () => {
    expect(() => assertDestinationTransition("READY_TO_UPLOAD", "UPLOAD_QUEUED")).not.toThrow();
    expect(() => assertDestinationTransition("UPLOAD_QUEUED", "PUBLISHED")).toThrow(DestinationTransitionError);
  });

  it("rejects terminal destination transitions", () => {
    expect(() => assertDestinationTransition("PUBLISHED", "UPLOADING")).toThrow(DestinationTransitionError);
  });
});

describe("atomic workflow transition service", () => {
  const input = {
    actorId: "admin-1",
    actorType: "ADMINISTRATOR",
    eventId: "event-1",
    expectedState: "QUEUED" as const,
    expectedVersion: 1,
    jobId: "job-1",
    nextState: "INGESTING" as const,
    nowUtc: "2026-08-04T00:00:00Z",
    operation: "IMPORT_START",
    requestId: "request-1",
  };

  it("updates the expected job version and appends job and audit events in one batch", async () => {
    const database = new RecordingWorkflowDatabase();
    const service = new JobService(database, () => "audit-1");

    await service.transition(input);

    expect(database.batches).toHaveLength(1);
    expect(database.batches[0]).toHaveLength(3);
    expect(database.batches[0]![0]!.sql).toContain("WHERE id = ? AND status = ? AND version = ?");
    expect(database.batches[0]![1]!.sql).toContain("INSERT INTO job_events");
    expect(database.batches[0]![2]!.sql).toContain("INSERT INTO audit_events");
    expect(database.batches[0]![0]!.values).toEqual([
      "INGESTING",
      "INGESTING",
      "2026-08-04T00:00:00Z",
      "job-1",
      "QUEUED",
      1,
    ]);
  });

  it("rejects skipped processing states before writing", async () => {
    const database = new RecordingWorkflowDatabase();
    const service = new JobService(database, () => "audit-1");

    await expect(service.transition({ ...input, nextState: "PROBING" })).rejects.toThrow(JobTransitionError);
    expect(database.batches).toHaveLength(0);
  });

  it("rejects a stale state/version conditional write", async () => {
    const database = new RecordingWorkflowDatabase(0);
    const service = new JobService(database, () => "audit-1");

    await expect(service.transition(input)).rejects.toThrow(StaleWorkflowError);
  });

  it("conditions a destination transition on its own destination to reject cross-destination writes", async () => {
    const database = new RecordingWorkflowDatabase(0);
    const service = new JobService(database, () => "audit-1");

    await expect(
      service.transitionDestination({
        actorId: "admin-1",
        actorType: "ADMINISTRATOR",
        destination: "YOUTUBE",
        eventId: "event-2",
        expectedState: "READY_TO_UPLOAD",
        jobId: "job-1",
        nextState: "UPLOAD_QUEUED",
        nowUtc: "2026-08-04T00:00:00Z",
        operation: "QUEUE_UPLOAD",
        publicationId: "publication-1",
        requestId: "request-2",
      }),
    ).rejects.toThrow(StaleWorkflowError);

    expect(database.batches[0]![0]!.sql).toContain("WHERE id = ? AND destination = ? AND status = ?");
    expect(database.batches[0]![0]!.values).toEqual([
      "UPLOAD_QUEUED",
      "2026-08-04T00:00:00Z",
      "publication-1",
      "YOUTUBE",
      "READY_TO_UPLOAD",
    ]);
  });
});
