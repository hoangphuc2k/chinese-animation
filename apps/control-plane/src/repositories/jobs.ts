import type { SqlDatabase } from "./sql";

export interface JobRow extends Record<string, unknown> {
  available_at: string;
  attempt_count: number;
  created_at: string;
  id: string;
  job_type: string;
  lease_until: string | null;
  project_id: string;
  retention_deadline: string | null;
  status: string;
  updated_at: string;
  version: number;
}

export interface CreateJobInput {
  availableAtUtc: string;
  createdAtUtc: string;
  id: string;
  jobType: string;
  priority: number;
  projectId: string;
  sourceAssetId: string;
  status: string;
  step: string;
  updatedAtUtc: string;
}

export interface ListRunnableJobsInput {
  availableBeforeUtc: string;
  limit: number;
}

export interface ListExpiringLeasesInput {
  leaseExpiresBeforeUtc: string;
  limit: number;
}

export interface ListProjectActivityInput {
  limit: number;
  projectId: string;
}

export interface ListRetentionDueInput {
  limit: number;
  retentionDueBeforeUtc: string;
}

export class JobsRepository {
  constructor(private readonly database: SqlDatabase) {}

  async create(input: CreateJobInput): Promise<void> {
    await this.database
      .prepare(
        "INSERT INTO jobs (id, project_id, source_asset_id, job_type, status, step, priority, available_at, attempt_count, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      )
      .bind(
        input.id,
        input.projectId,
        input.sourceAssetId,
        input.jobType,
        input.status,
        input.step,
        input.priority,
        input.availableAtUtc,
        0,
        input.createdAtUtc,
        input.updatedAtUtc,
      )
      .run();
  }

  async get(id: string): Promise<JobRow | null> {
    return this.database.prepare("SELECT * FROM jobs WHERE id = ?").bind(id).first<JobRow>();
  }

  async listRunnable(input: ListRunnableJobsInput): Promise<JobRow[]> {
    const result = await this.database
      .prepare(
        "SELECT * FROM jobs WHERE status = ? AND available_at <= ? AND (lease_until IS NULL OR lease_until <= ?) ORDER BY priority DESC, created_at ASC LIMIT ?",
      )
      .bind("QUEUED", input.availableBeforeUtc, input.availableBeforeUtc, input.limit)
      .all<JobRow>();

    return result.results;
  }

  async listExpiringLeases(input: ListExpiringLeasesInput): Promise<JobRow[]> {
    const result = await this.database
      .prepare(
        "SELECT * FROM jobs WHERE lease_until IS NOT NULL AND lease_until <= ? ORDER BY lease_until ASC LIMIT ?",
      )
      .bind(input.leaseExpiresBeforeUtc, input.limit)
      .all<JobRow>();

    return result.results;
  }

  async listProjectActivity(input: ListProjectActivityInput): Promise<JobRow[]> {
    const result = await this.database
      .prepare("SELECT * FROM jobs WHERE project_id = ? ORDER BY updated_at DESC LIMIT ?")
      .bind(input.projectId, input.limit)
      .all<JobRow>();

    return result.results;
  }

  async listRetentionDue(input: ListRetentionDueInput): Promise<JobRow[]> {
    const result = await this.database
      .prepare(
        "SELECT * FROM jobs WHERE retention_deadline IS NOT NULL AND retention_deadline <= ? ORDER BY retention_deadline ASC LIMIT ?",
      )
      .bind(input.retentionDueBeforeUtc, input.limit)
      .all<JobRow>();

    return result.results;
  }
}
