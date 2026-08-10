import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import { QuotaService } from "../src/services/quota-service";
import { createStructuredLogger } from "../src/observability/logger";

const controlPlaneDirectory = fileURLToPath(new URL("../", import.meta.url));
const databases: DatabaseSync[] = [];
const nowUtc = "2026-08-05T00:00:00.000Z";

function sqliteValues(values: unknown[]): Array<bigint | null | number | string> {
  return values.map((value) => {
    if (value === null || typeof value === "bigint" || typeof value === "number" || typeof value === "string") return value;
    throw new TypeError("The SQLite quota test adapter only accepts scalar D1 bind values.");
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

  async first<Row extends Record<string, unknown>>(): Promise<Row | null> {
    return (this.database.prepare(this.sql).get(...sqliteValues(this.values)) as Row | undefined) ?? null;
  }

  async run<Row extends Record<string, unknown>>(): Promise<{ meta: { changes: number }; results: Row[] }> {
    const result = this.database.prepare(this.sql).run(...sqliteValues(this.values));
    return { meta: { changes: Number(result.changes) }, results: [] };
  }
}

class SqliteD1 {
  constructor(readonly sqlite: DatabaseSync) {}

  prepare(sql: string): SqliteStatement {
    return new SqliteStatement(this.sqlite, sql);
  }
}

function createDatabase(): SqliteD1 {
  const sqlite = new DatabaseSync(":memory:");
  databases.push(sqlite);
  for (const file of readdirSync(resolve(controlPlaneDirectory, "db/migrations")).sort()) {
    if (/^\d{4}_.+\.sql$/.test(file)) sqlite.exec(readFileSync(resolve(controlPlaneDirectory, "db/migrations", file), "utf8"));
  }
  sqlite.exec(`
    INSERT INTO projects (id, name, status, created_at, updated_at)
    VALUES ('quota-project', 'Quota fixture', 'ACTIVE', '${nowUtc}', '${nowUtc}');
    INSERT INTO source_assets (
      id, project_id, source_provider, external_id, metadata_json, content_hash, byte_count,
      rights_metadata_json, rights_expires_at, created_at
    ) VALUES ('quota-source', 'quota-project', 'OCI_INBOX_FILE', 'quota-asset', '{}', 'source-hash', 1, '{}', NULL, '${nowUtc}');
    INSERT INTO jobs (
      id, project_id, source_asset_id, job_type, status, step, priority, available_at,
      attempt_count, created_at, updated_at
    ) VALUES ('quota-job', 'quota-project', 'quota-source', 'LOCALIZE', 'QUEUED', 'TRANSLATING', 0, '${nowUtc}', 0, '${nowUtc}', '${nowUtc}');
  `);
  return new SqliteD1(sqlite);
}

afterEach(() => {
  for (const database of databases.splice(0)) database.close();
});

describe("quota service", () => {
  it("warns at 80 percent and blocks new provider work at the 95 percent quota guard", async () => {
    const database = createDatabase();
    const service = new QuotaService(database);
    const budget = { limitUnits: 1000, periodStartUtc: "2026-08-01T00:00:00.000Z", provider: "AZURE_TTS", quotaKey: "voice-month", nowUtc };

    await expect(service.reserve({ ...budget, requestedUnits: 790 })).resolves.toEqual({ decision: "ALLOWED", usedUnits: 790 });
    await expect(service.reserve({ ...budget, requestedUnits: 10 })).resolves.toEqual({ decision: "WARNING", usedUnits: 800 });
    await expect(service.reserve({ ...budget, requestedUnits: 149 })).resolves.toEqual({ decision: "WARNING", usedUnits: 949 });
    await expect(service.reserve({ ...budget, requestedUnits: 1 })).resolves.toEqual({ decision: "BLOCKED", usedUnits: 949 });

    expect(database.sqlite.prepare("SELECT used_units, limit_units FROM quota_usage").get()).toEqual({ limit_units: 1000, used_units: 949 });
  });

  it("honors Retry-After by scheduling the job's next available time", async () => {
    const database = createDatabase();
    const service = new QuotaService(database);

    await expect(service.scheduleRetry({ jobId: "quota-job", nowUtc, retryAfter: "120" })).resolves.toEqual(
      "2026-08-05T00:02:00.000Z",
    );
    expect(database.sqlite.prepare("SELECT available_at, status FROM jobs WHERE id = 'quota-job'").get()).toEqual({
      available_at: "2026-08-05T00:02:00.000Z",
      status: "QUEUED",
    });
  });

  it("accepts an HTTP-date Retry-After value from a provider", async () => {
    const database = createDatabase();
    const service = new QuotaService(database);

    await expect(
      service.scheduleRetry({ jobId: "quota-job", nowUtc, retryAfter: "Wed, 05 Aug 2026 00:03:00 GMT" }),
    ).resolves.toEqual("2026-08-05T00:03:00.000Z");
    expect(database.sqlite.prepare("SELECT available_at FROM jobs WHERE id = 'quota-job'").get()).toEqual({
      available_at: "2026-08-05T00:03:00.000Z",
    });
  });

  it("redacts sensitive fields from JSON logs while retaining job correlation", () => {
    const output: string[] = [];
    const logger = createStructuredLogger({
      nowUtc: () => nowUtc,
      write: (entry) => output.push(entry),
    });

    logger.info({
      accessToken: "provider-token",
      durationMs: 120,
      errorCode: "RATE_LIMITED",
      headers: { authorization: "Bearer credential", "x-request-id": "safe-id" },
      jobId: "quota-job",
      nested: { cookie: "session-cookie", provider: "azure" },
      projectId: "quota-project",
      requestId: "request-1",
      sourceTitle: "Unreleased creator episode",
      sourceTitleSensitive: true,
      stage: "SYNTHESIS",
      url: "https://creator.example/private.mp4",
    });

    expect(output.map((entry) => JSON.parse(entry))).toEqual([
      {
        accessToken: "[REDACTED]",
        atUtc: nowUtc,
        durationMs: 120,
        errorCode: "RATE_LIMITED",
        headers: "[REDACTED]",
        jobId: "quota-job",
        level: "INFO",
        nested: { cookie: "[REDACTED]", provider: "azure" },
        projectId: "quota-project",
        requestId: "request-1",
        sourceTitle: "[REDACTED]",
        stage: "SYNTHESIS",
        url: "[REDACTED]",
      },
    ]);
  });
});
