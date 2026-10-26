import { readdirSync, readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

const controlPlaneDirectory = fileURLToPath(new URL("../", import.meta.url));
const migrationSql = readdirSync(resolve(controlPlaneDirectory, "db/migrations"))
  .filter((file) => /^\d{4}_.+\.sql$/.test(file))
  .sort()
  .map((file) => readFileSync(resolve(controlPlaneDirectory, "db/migrations", file), "utf8"));
const databases: DatabaseSync[] = [];

afterEach(() => {
  for (const database of databases.splice(0)) {
    database.close();
  }
});

function createEmptyD1(): DatabaseSync {
  const database = new DatabaseSync(":memory:");
  databases.push(database);
  return database;
}

function applyMigrations(database: DatabaseSync): void {
  for (const sql of migrationSql) database.exec(sql);
}

function queryRows<Row extends Record<string, unknown>>(
  database: DatabaseSync,
  query: string,
): Row[] {
  return database.prepare(query).all() as Row[];
}

describe("D1 migrations", () => {
  it("creates the persistence foundation from an empty D1-compatible SQLite database without sensitive storage columns", () => {
    const database = createEmptyD1();

    expect(() => applyMigrations(database)).not.toThrow();

    const tables = queryRows<{ name: string }>(
      database,
      "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name",
    ).map((table) => table.name);

    expect(tables).toEqual(
      expect.arrayContaining([
        "approvals",
        "audit_events",
        "destination_publications",
        "idempotency_records",
        "job_events",
        "jobs",
        "projects",
        "render_versions",
        "script_versions",
        "source_assets",
      ]),
    );

    const sourceAssetColumns = queryRows<{ name: string }>(
      database,
      "PRAGMA table_info(source_assets)",
    ).map((column) => column.name);

    expect(sourceAssetColumns).toEqual(
      expect.arrayContaining([
        "byte_count",
        "content_hash",
        "rights_expires_at",
        "rights_metadata_json",
      ]),
    );
    expect(sourceAssetColumns.join(" ")).not.toMatch(
      /body|preview.*token|oauth|filesystem|file.*path/i,
    );

    const jobColumns = queryRows<{ name: string }>(database, "PRAGMA table_info(jobs)").map((column) => column.name);
    expect(jobColumns).toContain("version");
    expect(jobColumns).toContain("preview_descriptor_json");
    const idempotencyColumns = queryRows<{ name: string }>(database, "PRAGMA table_info(idempotency_records)").map(
      (column) => column.name,
    );
    expect(idempotencyColumns).toContain("request_hash");

    const indexes = queryRows<{ name: string }>(
      database,
      "SELECT name FROM sqlite_master WHERE type = 'index' ORDER BY name",
    ).map((index) => index.name);

    expect(indexes).toEqual(
      expect.arrayContaining([
        "idx_jobs_expiring_leases",
        "idx_jobs_project_activity",
        "idx_jobs_retention_deadline",
        "idx_jobs_runnable",
      ]),
    );
  });

  it("fails when the committed SQL is executed twice against the same D1", () => {
    const database = createEmptyD1();

    applyMigrations(database);

    expect(() => applyMigrations(database)).toThrow(/already exists/i);
  });

  it("enforces external IDs, version numbers, operation-scoped idempotency keys, and provider asset identities across jobs", () => {
    const database = createEmptyD1();

    applyMigrations(database);
    database.exec(`
      INSERT INTO projects (id, name, status, created_at, updated_at)
      VALUES ('project-1', 'Fixture', 'ACTIVE', '2026-08-03T00:00:00Z', '2026-08-03T00:00:00Z');
      INSERT INTO source_assets (
        id,
        project_id,
        source_provider,
        external_id,
        metadata_json,
        content_hash,
        byte_count,
        rights_metadata_json,
        rights_expires_at,
        created_at
      ) VALUES (
        'source-1',
        'project-1',
        'BILIBILI',
        'external-1',
        '{}',
        'hash-source',
        42,
        '{}',
        '2026-08-10T00:00:00Z',
        '2026-08-03T00:00:00Z'
      );
      INSERT INTO jobs (
        id,
        project_id,
        source_asset_id,
        job_type,
        status,
        step,
        priority,
        available_at,
        attempt_count,
        created_at,
        updated_at
      ) VALUES (
        'job-1',
        'project-1',
        'source-1',
        'LOCALIZE',
        'QUEUED',
        'DISCOVERED',
        0,
        '2026-08-03T00:00:00Z',
        0,
        '2026-08-03T00:00:00Z',
        '2026-08-03T00:00:00Z'
      );
      INSERT INTO jobs (
        id,
        project_id,
        source_asset_id,
        job_type,
        status,
        step,
        priority,
        available_at,
        attempt_count,
        created_at,
        updated_at
      ) VALUES (
        'job-2',
        'project-1',
        'source-1',
        'LOCALIZE',
        'QUEUED',
        'DISCOVERED',
        0,
        '2026-08-03T00:00:00Z',
        0,
        '2026-08-03T00:00:00Z',
        '2026-08-03T00:00:00Z'
      );
    `);

    expect(() =>
      database.exec(`
        INSERT INTO source_assets (
          id,
          project_id,
          source_provider,
          external_id,
          metadata_json,
          content_hash,
          byte_count,
          rights_metadata_json,
          rights_expires_at,
          created_at
        ) VALUES (
          'source-2',
          'project-1',
          'BILIBILI',
          'external-1',
          '{}',
          'hash-source-2',
          43,
          '{}',
          '2026-08-10T00:00:00Z',
          '2026-08-03T00:00:00Z'
        );
      `),
    ).toThrow(/UNIQUE constraint failed/i);
    expect(() =>
      database.exec(`
        INSERT INTO script_versions (id, job_id, version, content_hash, created_at)
        VALUES ('script-1', 'job-1', 1, 'hash-script', '2026-08-03T00:00:00Z');
        INSERT INTO script_versions (id, job_id, version, content_hash, created_at)
        VALUES ('script-2', 'job-1', 1, 'hash-script-2', '2026-08-03T00:00:00Z');
      `),
    ).toThrow(/UNIQUE constraint failed/i);
    expect(() =>
      database.exec(`
        INSERT INTO render_versions (
          id,
          job_id,
          version,
          render_hash,
          byte_count,
          artifact_ref,
          created_at
        ) VALUES ('render-1', 'job-1', 1, 'hash-render', 84, 'render-opaque-1', '2026-08-03T00:00:00Z');
        INSERT INTO render_versions (
          id,
          job_id,
          version,
          render_hash,
          byte_count,
          artifact_ref,
          created_at
        ) VALUES ('render-2', 'job-1', 1, 'hash-render-2', 85, 'render-opaque-2', '2026-08-03T00:00:00Z');
      `),
    ).toThrow(/UNIQUE constraint failed/i);
    database.exec(`
        INSERT INTO idempotency_records (
          id,
          actor_id,
          operation,
          idempotency_key,
          response_status,
          response_json,
          created_at
        ) VALUES ('idem-1', 'actor-1', 'IMPORT', 'idem-key-1', 202, '{}', '2026-08-03T00:00:00Z');
      `);
    expect(() =>
      database.exec(`
        INSERT INTO idempotency_records (
          id,
          actor_id,
          operation,
          idempotency_key,
          response_status,
          response_json,
          created_at
        ) VALUES ('idem-2', 'actor-1', 'IMPORT', 'idem-key-1', 202, '{}', '2026-08-03T00:00:00Z');
      `),
    ).toThrow(/UNIQUE constraint failed/i);
    expect(() =>
      database.exec(`
        INSERT INTO idempotency_records (
          id,
          actor_id,
          operation,
          idempotency_key,
          response_status,
          response_json,
          created_at
        ) VALUES ('idem-3', 'actor-1', 'APPROVE', 'idem-key-1', 202, '{}', '2026-08-03T00:00:00Z');
      `),
    ).not.toThrow();
    expect(() =>
      database.exec(`
        INSERT INTO destination_publications (
          id,
          job_id,
          destination,
          provider_asset_id,
          status,
          metadata_hash,
          created_at,
          updated_at
        ) VALUES (
          'publication-1',
          'job-1',
          'YOUTUBE',
          'provider-asset-1',
          'UPLOADING',
          'hash-metadata',
          '2026-08-03T00:00:00Z',
          '2026-08-03T00:00:00Z'
        );
        INSERT INTO destination_publications (
          id,
          job_id,
          destination,
          provider_asset_id,
          status,
          metadata_hash,
          created_at,
          updated_at
        ) VALUES (
          'publication-2',
          'job-2',
          'YOUTUBE',
          'provider-asset-1',
          'UPLOADING',
          'hash-metadata-2',
          '2026-08-03T00:00:00Z',
          '2026-08-03T00:00:00Z'
        );
      `),
    ).toThrow(/UNIQUE constraint failed/i);
  });
  it("allows one in-flight import per asset, re-import after a terminal job, and one cleanup per target job", () => {
    const database = createEmptyD1();
    applyMigrations(database);
    database.exec(`
      INSERT INTO projects (id, name, status, created_at, updated_at) VALUES ('project-1', 'Series', 'ACTIVE', '2026-08-03T00:00:00Z', '2026-08-03T00:00:00Z');
      INSERT INTO source_assets (id, project_id, source_provider, external_id, metadata_json, content_hash, byte_count, rights_metadata_json, created_at)
        VALUES ('asset-1', 'project-1', 'CREATOR_HTTPS_URL', 'https://creator.example/a.mp4', '{}', 'hash', 0, '{}', '2026-08-03T00:00:00Z');
      INSERT INTO jobs (id, project_id, source_asset_id, job_type, status, step, available_at, created_at, updated_at)
        VALUES ('job-1', 'project-1', 'asset-1', 'IMPORT', 'QUEUED', 'QUEUED', '2026-08-03T00:00:00Z', '2026-08-03T00:00:00Z', '2026-08-03T00:00:00Z');
    `);
    const insertImport = (id: string) =>
      database.exec(
        `INSERT INTO jobs (id, project_id, source_asset_id, job_type, status, step, available_at, created_at, updated_at) VALUES ('${id}', 'project-1', 'asset-1', 'IMPORT', 'QUEUED', 'QUEUED', '2026-08-03T00:00:00Z', '2026-08-03T00:00:00Z', '2026-08-03T00:00:00Z')`,
      );
    const insertCleanup = (id: string, target: string) =>
      database.exec(
        `INSERT INTO jobs (id, project_id, source_asset_id, job_type, status, step, available_at, created_at, updated_at, target_job_id) VALUES ('${id}', 'project-1', 'asset-1', 'DELETE_EXPIRED', 'QUEUED', 'DELETE_EXPIRED', '2026-08-03T00:00:00Z', '2026-08-03T00:00:00Z', '2026-08-03T00:00:00Z', '${target}')`,
      );

    expect(() => insertImport("job-2")).toThrow(/UNIQUE constraint failed/i);
    database.exec("UPDATE jobs SET status = 'EXPIRED' WHERE id = 'job-1'");
    expect(() => insertImport("job-2")).not.toThrow();

    insertCleanup("cleanup-1", "job-1");
    expect(() => insertCleanup("cleanup-2", "job-1")).toThrow(/UNIQUE constraint failed/i);
    expect(() => insertCleanup("cleanup-3", "job-2")).not.toThrow();
  });
});
