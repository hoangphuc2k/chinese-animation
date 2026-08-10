import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import { RetentionService } from "../src/scheduled/retention";
import worker from "../src/index";
import { createTestEnv } from "./worker-harness";

const controlPlaneDirectory = fileURLToPath(new URL("../", import.meta.url));
const databases: DatabaseSync[] = [];
const nowUtc = "2026-08-05T00:00:00.000Z";

function d1Result<Row>(results: Row[], changes = 0): D1Result<Row> {
  return {
    meta: {
      changed_db: changes > 0,
      changes,
      duration: 0,
      last_row_id: 0,
      rows_read: 0,
      rows_written: changes,
      size_after: 0,
    },
    results,
    success: true,
  };
}

function sqliteValues(values: unknown[]): Array<bigint | null | number | string> {
  return values.map((value) => {
    if (value === null || typeof value === "bigint" || typeof value === "number" || typeof value === "string") return value;
    throw new TypeError("The SQLite retention test adapter only accepts scalar D1 bind values.");
  });
}

class SqliteStatement {
  private values: unknown[] = [];

  constructor(
    private readonly database: DatabaseSync,
    private readonly sql: string,
  ) {}

  bind(...values: unknown[]): this {
    this.values = values;
    return this;
  }

  async all<Row = Record<string, unknown>>(): Promise<D1Result<Row>> {
    return d1Result(this.database.prepare(this.sql).all(...sqliteValues(this.values)) as Row[]);
  }

  async first<Row = unknown>(colName: string): Promise<Row | null>;
  async first<Row extends Record<string, unknown> = Record<string, unknown>>(): Promise<Row | null>;
  async first<Row extends Record<string, unknown> = Record<string, unknown>>(colName?: string): Promise<Row | null> {
    const row = (this.database.prepare(this.sql).get(...sqliteValues(this.values)) as Row | undefined) ?? null;
    if (colName === undefined || row === null) return row;
    return (row[colName] as Row) ?? null;
  }

  async raw<Row = unknown[]>(options: { columnNames: true }): Promise<[string[], ...Row[]]>;
  async raw<Row = unknown[]>(options?: { columnNames?: false }): Promise<Row[]>;
  async raw<Row = unknown[]>(options?: { columnNames?: boolean }): Promise<Row[] | [string[], ...Row[]]> {
    const statement = this.database.prepare(this.sql);
    const columns = statement.columns().map((column) => column.name);
    const rows = statement.all(...sqliteValues(this.values)) as Row[];
    return options?.columnNames ? [columns, ...rows] : rows;
  }

  async run<Row = Record<string, unknown>>(): Promise<D1Result<Row>> {
    const result = this.database.prepare(this.sql).run(...sqliteValues(this.values));
    return d1Result([], Number(result.changes));
  }
}

class SqliteD1 implements D1Database {
  constructor(readonly sqlite: DatabaseSync) {}

  prepare(sql: string): SqliteStatement {
    return new SqliteStatement(this.sqlite, sql);
  }

  async batch<Row = unknown>(statements: D1PreparedStatement[]): Promise<D1Result<Row>[]> {
    this.sqlite.exec("BEGIN");
    try {
      const results: D1Result<Row>[] = [];
      for (const statement of statements) {
        if (!(statement instanceof SqliteStatement)) throw new TypeError("The SQLite retention test adapter only batches its own statements.");
        results.push(await statement.run<Row>());
      }
      this.sqlite.exec("COMMIT");
      return results;
    } catch (error) {
      this.sqlite.exec("ROLLBACK");
      throw error;
    }
  }

  async dump(): Promise<ArrayBuffer> {
    return new ArrayBuffer(0);
  }

  async exec(query: string): Promise<D1ExecResult> {
    this.sqlite.exec(query);
    return { count: 0, duration: 0 };
  }

  withSession(): D1DatabaseSession {
    return {
      batch: <Row = unknown>(statements: D1PreparedStatement[]) => this.batch<Row>(statements),
      getBookmark: () => null,
      prepare: (query: string) => this.prepare(query),
    };
  }
}

function createDatabase(): SqliteD1 {
  const sqlite = new DatabaseSync(":memory:");
  databases.push(sqlite);
  for (const file of readdirSync(resolve(controlPlaneDirectory, "db/migrations")).sort()) {
    if (/^\d{4}_.+\.sql$/.test(file)) sqlite.exec(readFileSync(resolve(controlPlaneDirectory, "db/migrations", file), "utf8"));
  }
  return new SqliteD1(sqlite);
}

function createExpiredFixture(database: SqliteD1, options: { id: string; rightsRevokedAtUtc?: string | null; status: string }): void {
  const projectId = `${options.id}-project`;
  const sourceId = `${options.id}-source`;
  database.sqlite.exec(`
    INSERT INTO projects (id, name, status, created_at, updated_at)
    VALUES ('${projectId}', 'Retention fixture', 'ACTIVE', '2026-07-20T00:00:00.000Z', '2026-07-20T00:00:00.000Z');
    INSERT INTO source_assets (
      id, project_id, source_provider, external_id, metadata_json, content_hash, byte_count,
      rights_metadata_json, rights_expires_at, rights_revoked_at, created_at
    ) VALUES (
      '${sourceId}', '${projectId}', 'OCI_INBOX_FILE', '${options.id}-asset', '{}', 'source-hash', 1,
      '{}', NULL, ${options.rightsRevokedAtUtc ? `'${options.rightsRevokedAtUtc}'` : "NULL"}, '2026-07-20T00:00:00.000Z'
    );
    INSERT INTO jobs (
      id, project_id, source_asset_id, job_type, status, step, priority, available_at,
      attempt_count, retention_deadline, created_at, updated_at
    ) VALUES (
      '${options.id}', '${projectId}', '${sourceId}', 'LOCALIZE', '${options.status}', '${options.status}', 0,
      '2026-07-20T00:00:00.000Z', 0, '2026-08-04T00:00:00.000Z', '2026-07-20T00:00:00.000Z', '2026-07-20T00:00:00.000Z'
    );
    INSERT INTO script_versions (id, job_id, version, content_hash, created_at)
    VALUES ('${options.id}-script', '${options.id}', 1, 'script-hash', '2026-07-20T00:00:00.000Z');
    INSERT INTO destination_publications (
      id, job_id, destination, status, metadata_hash, created_at, updated_at
    ) VALUES (
      '${options.id}-destination', '${options.id}', 'YOUTUBE', 'READY_TO_UPLOAD', 'metadata-hash',
      '2026-07-20T00:00:00.000Z', '2026-07-20T00:00:00.000Z'
    );
  `);
}

afterEach(() => {
  for (const database of databases.splice(0)) database.close();
});

describe("retention service", () => {
  it("expires seven-day preview media by queuing agent deletion work while retaining terminal metadata", async () => {
    const database = createDatabase();
    createExpiredFixture(database, { id: "job-expired", status: "READY_FOR_PREVIEW" });
    const service = new RetentionService(database, () => "cleanup-job-1");

    await service.run({ nowUtc, requestId: "request-retention-1" });

    expect(
      database.sqlite.prepare("SELECT status, retention_deadline FROM jobs WHERE id = 'job-expired'").get(),
    ).toEqual({ retention_deadline: "2026-08-04T00:00:00.000Z", status: "EXPIRED" });
    expect(
      database.sqlite.prepare("SELECT job_type, status, source_asset_id FROM jobs WHERE id = 'cleanup-job-1'").get(),
    ).toEqual({ job_type: "DELETE_EXPIRED", source_asset_id: "job-expired-source", status: "QUEUED" });
    expect(
      database.sqlite.prepare("SELECT status FROM destination_publications WHERE id = 'job-expired-destination'").get(),
    ).toEqual({ status: "MANUAL_ACTION_REQUIRED" });
    expect(database.sqlite.prepare("SELECT content_hash FROM script_versions WHERE id = 'job-expired-script'").get()).toEqual({ content_hash: "script-hash" });
    expect(database.sqlite.prepare("SELECT event_type FROM job_events WHERE job_id = 'job-expired'").get()).toEqual({ event_type: "RETENTION_EXPIRED" });
    expect(database.sqlite.prepare("SELECT operation FROM audit_events WHERE target_id = 'job-expired'").get()).toEqual({ operation: "RETENTION_EXPIRY_REQUESTED" });

    await service.run({ nowUtc, requestId: "request-retention-2" });
    expect(database.sqlite.prepare("SELECT COUNT(*) AS count FROM jobs WHERE job_type = 'DELETE_EXPIRED'").get()).toEqual({ count: 1 });
  });

  it("blocks revoked rights and queues cleanup instead of erasing metadata", async () => {
    const database = createDatabase();
    createExpiredFixture(database, {
      id: "job-revoked",
      rightsRevokedAtUtc: "2026-08-04T00:00:00.000Z",
      status: "QUEUED",
    });
    const service = new RetentionService(database, () => "cleanup-job-2");

    await service.run({ nowUtc, requestId: "request-rights-1" });

    expect(database.sqlite.prepare("SELECT status FROM jobs WHERE id = 'job-revoked'").get()).toEqual({ status: "RIGHTS_BLOCKED" });
    expect(
      database.sqlite.prepare("SELECT job_type, status FROM jobs WHERE id = 'cleanup-job-2'").get(),
    ).toEqual({ job_type: "DELETE_EXPIRED", status: "QUEUED" });
    expect(database.sqlite.prepare("SELECT operation FROM audit_events WHERE target_id = 'job-revoked'").get()).toEqual({ operation: "RIGHTS_REVOKED" });
    expect(database.sqlite.prepare("SELECT id FROM source_assets WHERE id = 'job-revoked-source'").get()).toEqual({ id: "job-revoked-source" });
  });

  it("emits an expiry log with request, job, and project correlation", async () => {
    const database = createDatabase();
    createExpiredFixture(database, { id: "job-logged", status: "READY_FOR_PREVIEW" });
    const events: Array<Record<string, unknown>> = [];
    const service = new RetentionService(database, () => "cleanup-job-logged", {
      info: (event) => events.push(event),
    });

    await service.run({ nowUtc, requestId: "request-retention-logged" });

    expect(events).toEqual([
      expect.objectContaining({
        durationMs: expect.any(Number),
        jobId: "job-logged",
        projectId: "job-logged-project",
        requestId: "request-retention-logged",
        stage: "RETENTION_EXPIRY",
      }),
    ]);
  });

  it("runs the daily retention sweep from the Worker scheduled handler", async () => {
    const database = createDatabase();
    createExpiredFixture(database, { id: "job-scheduled", status: "READY_FOR_PREVIEW" });

    await worker.scheduled!(
      { cron: "0 0 * * *", scheduledTime: Date.parse(nowUtc) } as ScheduledController,
      createTestEnv({ CONTROL_PLANE_DB: database }),
    );

    expect(database.sqlite.prepare("SELECT status FROM jobs WHERE id = 'job-scheduled'").get()).toEqual({ status: "EXPIRED" });
  });
});
