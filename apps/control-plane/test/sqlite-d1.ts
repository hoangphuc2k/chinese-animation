import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";

const migrationsDirectory = fileURLToPath(new URL("../db/migrations/", import.meta.url));

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

  async run<Row extends Record<string, unknown>>(): Promise<{ meta: { changes: number }; results: Row[] }> {
    const result = this.database.prepare(this.sql).run(...sqliteInputValues(this.values));
    return { meta: { changes: Number(result.changes) }, results: [] };
  }
}

export class SqliteD1 {
  constructor(readonly sqlite: DatabaseSync) {}

  prepare(sql: string): SqliteStatement {
    return new SqliteStatement(this.sqlite, sql);
  }

  async batch(statements: SqliteStatement[]): Promise<Array<{ meta: { changes: number }; results: Record<string, unknown>[] }>> {
    this.sqlite.exec("BEGIN");
    try {
      const results = await Promise.all(statements.map((statement) => statement.run()));
      this.sqlite.exec("COMMIT");
      return results;
    } catch (error) {
      this.sqlite.exec("ROLLBACK");
      throw error;
    }
  }
}

/** In-memory SQLite database with the given migrations (all of them by default) applied. */
export function createSqliteD1(migrations: readonly string[] = readdirSync(migrationsDirectory).filter((name) => name.endsWith(".sql")).sort()): SqliteD1 {
  const database = new DatabaseSync(":memory:");
  for (const migration of migrations) database.exec(readFileSync(resolve(migrationsDirectory, migration), "utf8"));
  return new SqliteD1(database);
}
