export interface SqlResult<Row extends Record<string, unknown>> {
  results: Row[];
}

export interface SqlStatement {
  bind(...values: unknown[]): SqlStatement;
  all<Row extends Record<string, unknown> = Record<string, unknown>>(): Promise<SqlResult<Row>>;
  first<Row extends Record<string, unknown> = Record<string, unknown>>(): Promise<Row | null>;
  run<Row extends Record<string, unknown> = Record<string, unknown>>(): Promise<SqlResult<Row>>;
}

export interface SqlDatabase {
  prepare(query: string): SqlStatement;
}
