import type { SqlDatabase } from "./sql";

export interface CreateScriptVersionInput {
  contentHash: string;
  createdAtUtc: string;
  id: string;
  jobId: string;
  metadataHash: string | null;
  transcriptHash: string | null;
  translationHash: string | null;
  version: number;
  voiceSettingsHash: string | null;
}

export interface ScriptVersionRow extends Record<string, unknown> {
  content_hash: string;
  created_at: string;
  id: string;
  job_id: string;
  metadata_hash: string | null;
  transcript_hash: string | null;
  translation_hash: string | null;
  version: number;
  voice_settings_hash: string | null;
}

export interface ListScriptVersionsInput {
  cursor: number | undefined;
  jobId: string;
  limit: number;
}

export class ScriptVersionsRepository {
  constructor(private readonly database: SqlDatabase) {}

  async create(input: CreateScriptVersionInput): Promise<void> {
    await this.database
      .prepare(
        "INSERT INTO script_versions (id, job_id, version, content_hash, transcript_hash, translation_hash, metadata_hash, voice_settings_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
      )
      .bind(
        input.id,
        input.jobId,
        input.version,
        input.contentHash,
        input.transcriptHash,
        input.translationHash,
        input.metadataHash,
        input.voiceSettingsHash,
        input.createdAtUtc,
      )
      .run();
  }

  async list(input: ListScriptVersionsInput): Promise<ScriptVersionRow[]> {
    const statement = input.cursor
      ? this.database
          .prepare("SELECT * FROM script_versions WHERE job_id = ? AND version > ? ORDER BY version ASC LIMIT ?")
          .bind(input.jobId, input.cursor, input.limit)
      : this.database
          .prepare("SELECT * FROM script_versions WHERE job_id = ? ORDER BY version ASC LIMIT ?")
          .bind(input.jobId, input.limit);
    const result = await statement.all<ScriptVersionRow>();
    return result.results;
  }
}
