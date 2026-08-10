import type { SqlDatabase } from "./sql";

export interface AppendJobEventInput {
  createdAtUtc: string;
  detailsJson: string;
  eventType: string;
  id: string;
  jobId: string;
  nextState: string | null;
  previousState: string | null;
}

export interface JobEventRow extends Record<string, unknown> {
  created_at: string;
  details_json: string;
  event_type: string;
  id: string;
  job_id: string;
  next_state: string | null;
  previous_state: string | null;
}

export interface ListJobEventsInput {
  cursor: { createdAtUtc: string; id: string } | undefined;
  jobId: string;
  limit: number;
}

export class JobEventsRepository {
  constructor(private readonly database: SqlDatabase) {}

  async append(input: AppendJobEventInput): Promise<void> {
    await this.database
      .prepare(
        "INSERT INTO job_events (id, job_id, event_type, previous_state, next_state, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
      )
      .bind(
        input.id,
        input.jobId,
        input.eventType,
        input.previousState,
        input.nextState,
        input.detailsJson,
        input.createdAtUtc,
      )
      .run();
  }

  async list(input: ListJobEventsInput): Promise<JobEventRow[]> {
    if (!input.cursor) {
      const result = await this.database
        .prepare("SELECT * FROM job_events WHERE job_id = ? ORDER BY created_at ASC, id ASC LIMIT ?")
        .bind(input.jobId, input.limit)
        .all<JobEventRow>();
      return result.results;
    }

    const result = await this.database
      .prepare(
        "SELECT * FROM job_events WHERE job_id = ? AND (created_at > ? OR (created_at = ? AND id > ?)) ORDER BY created_at ASC, id ASC LIMIT ?",
      )
      .bind(input.jobId, input.cursor.createdAtUtc, input.cursor.createdAtUtc, input.cursor.id, input.limit)
      .all<JobEventRow>();
    return result.results;
  }
}
