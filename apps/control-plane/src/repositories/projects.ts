import type { SqlDatabase } from "./sql";

export interface CreateProjectInput {
  createdAtUtc: string;
  id: string;
  name: string;
  status: string;
  updatedAtUtc: string;
}

export interface ProjectRow extends Record<string, unknown> {
  created_at: string;
  id: string;
  name: string;
  status: string;
  updated_at: string;
}

export interface ListProjectsInput {
  cursor: { id: string; updatedAtUtc: string } | undefined;
  limit: number;
}

export class ProjectsRepository {
  constructor(private readonly database: SqlDatabase) {}

  async create(input: CreateProjectInput): Promise<void> {
    await this.database
      .prepare(
        "INSERT INTO projects (id, name, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
      )
      .bind(input.id, input.name, input.status, input.createdAtUtc, input.updatedAtUtc)
      .run();
  }

  async get(id: string): Promise<ProjectRow | null> {
    return this.database.prepare("SELECT * FROM projects WHERE id = ?").bind(id).first<ProjectRow>();
  }

  async list(input: ListProjectsInput): Promise<ProjectRow[]> {
    if (!input.cursor) {
      const result = await this.database
        .prepare("SELECT * FROM projects ORDER BY updated_at DESC, id DESC LIMIT ?")
        .bind(input.limit)
        .all<ProjectRow>();
      return result.results;
    }

    const result = await this.database
      .prepare(
        "SELECT * FROM projects WHERE updated_at < ? OR (updated_at = ? AND id < ?) ORDER BY updated_at DESC, id DESC LIMIT ?",
      )
      .bind(input.cursor.updatedAtUtc, input.cursor.updatedAtUtc, input.cursor.id, input.limit)
      .all<ProjectRow>();
    return result.results;
  }
}
