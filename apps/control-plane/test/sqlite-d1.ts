import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";

const migrationsDirectory = resolve(fileURLToPath(new URL("../", import.meta.url)), "db/migrations");

function sqliteInputValues(values: unknown[]): Array<bigint | null | number | string> {
  return values.map((value) => {
    if (value === null || typeof value === "bigint" || typeof value === "number" || typeof value === "string") return value;
    throw new TypeError("The SQLite test adapter only accepts scalar D1 bind values.");
  });
}

export class SqliteStatement {
  private values: unknown[] = [];

  constructor(
    private readonly database: DatabaseSync,
    private readonly sql: string,
  ) {}

  bind(...values: unknown[]): this {
    this.values = values;
    return this;
  }

  async all<Row extends Record<string, unknown>>(): Promise<{ results: Row[] }> {
    return { results: this.database.prepare(this.sql).all(...sqliteInputValues(this.values)) as Row[] };
  }

  async first<Row extends Record<string, unknown>>(): Promise<Row | null> {
    return (this.database.prepare(this.sql).get(...sqliteInputValues(this.values)) as Row | undefined) ?? null;
  }

  async run<Row extends Record<string, unknown> = Record<string, unknown>>(): Promise<{ meta: { changes: number }; results: Row[] }> {
    const statement = this.database.prepare(this.sql);
    if (/\bRETURNING\b/i.test(this.sql)) {
      const results = statement.all(...sqliteInputValues(this.values)) as Row[];
      return { meta: { changes: results.length }, results };
    }
    const result = statement.run(...sqliteInputValues(this.values));
    return { meta: { changes: Number(result.changes) }, results: [] };
  }

  runSync(): { meta: { changes: number }; results: Record<string, unknown>[] } {
    const result = this.database.prepare(this.sql).run(...sqliteInputValues(this.values));
    return { meta: { changes: Number(result.changes) }, results: [] };
  }
}

/** D1-compatible adapter: `batch` runs its statements sequentially in one transaction, like D1. */
export class SqliteD1 {
  constructor(readonly sqlite: DatabaseSync) {}

  prepare(sql: string): SqliteStatement {
    return new SqliteStatement(this.sqlite, sql);
  }

  async batch(statements: SqliteStatement[]): Promise<Array<{ meta: { changes: number }; results: Record<string, unknown>[] }>> {
    this.sqlite.exec("BEGIN");
    try {
      const results = statements.map((statement) => statement.runSync());
      this.sqlite.exec("COMMIT");
      return results;
    } catch (error) {
      this.sqlite.exec("ROLLBACK");
      throw error;
    }
  }

  close(): void {
    this.sqlite.close();
  }
}

export function createMigratedD1(): SqliteD1 {
  const database = new DatabaseSync(":memory:");
  for (const file of readdirSync(migrationsDirectory).filter((name) => /^\d{4}_.+\.sql$/.test(name)).sort()) {
    database.exec(readFileSync(resolve(migrationsDirectory, file), "utf8"));
  }
  return new SqliteD1(database);
}
