import type { SqlDatabase } from "./sql";

export interface CreateSourceAssetInput {
  byteCount: number;
  contentHash: string;
  createdAtUtc: string;
  externalId: string;
  id: string;
  metadataJson: string;
  projectId: string;
  rightsExpiresAtUtc: string | null;
  rightsMetadataJson: string;
  sourceProvider: string;
}

export class SourceAssetsRepository {
  constructor(private readonly database: SqlDatabase) {}

  async create(input: CreateSourceAssetInput): Promise<void> {
    await this.database
      .prepare(
        "INSERT INTO source_assets (id, project_id, source_provider, external_id, metadata_json, content_hash, byte_count, rights_metadata_json, rights_expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      )
      .bind(
        input.id,
        input.projectId,
        input.sourceProvider,
        input.externalId,
        input.metadataJson,
        input.contentHash,
        input.byteCount,
        input.rightsMetadataJson,
        input.rightsExpiresAtUtc,
        input.createdAtUtc,
      )
      .run();
  }
}
