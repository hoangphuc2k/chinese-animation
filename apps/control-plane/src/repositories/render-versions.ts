import type { SqlDatabase } from "./sql";

export interface CreateRenderVersionInput {
  artifactRef: string;
  byteCount: number;
  createdAtUtc: string;
  id: string;
  jobId: string;
  renderHash: string;
  version: number;
}

export interface RenderVersionRow extends Record<string, unknown> {
  artifact_ref: string;
  byte_count: number;
  created_at: string;
  id: string;
  job_id: string;
  render_hash: string;
  version: number;
}

export interface ListRenderVersionsInput {
  cursor: number | undefined;
  jobId: string;
  limit: number;
}

export class RenderVersionsRepository {
  constructor(private readonly database: SqlDatabase) {}

  async create(input: CreateRenderVersionInput): Promise<void> {
    await this.database
      .prepare(
        "INSERT INTO render_versions (id, job_id, version, render_hash, byte_count, artifact_ref, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
      )
      .bind(
        input.id,
        input.jobId,
        input.version,
        input.renderHash,
        input.byteCount,
        input.artifactRef,
        input.createdAtUtc,
      )
      .run();
  }

  async list(input: ListRenderVersionsInput): Promise<RenderVersionRow[]> {
    const statement = input.cursor
      ? this.database
          .prepare("SELECT * FROM render_versions WHERE job_id = ? AND version > ? ORDER BY version ASC LIMIT ?")
          .bind(input.jobId, input.cursor, input.limit)
      : this.database
          .prepare("SELECT * FROM render_versions WHERE job_id = ? ORDER BY version ASC LIMIT ?")
          .bind(input.jobId, input.limit);
    const result = await statement.all<RenderVersionRow>();
    return result.results;
  }
}
