import type { SqlDatabase } from "./sql";

export interface CreateDestinationPublicationInput {
  createdAtUtc: string;
  destination: string;
  id: string;
  jobId: string;
  metadataHash: string;
  providerAssetId: string | null;
  providerSessionId: string | null;
  renderVersionId: string | null;
  scheduledAtUtc: string | null;
  status: string;
  updatedAtUtc: string;
}

export class DestinationPublicationsRepository {
  constructor(private readonly database: SqlDatabase) {}

  async create(input: CreateDestinationPublicationInput): Promise<void> {
    await this.database
      .prepare(
        "INSERT INTO destination_publications (id, job_id, destination, provider_asset_id, provider_session_id, render_version_id, status, metadata_hash, scheduled_at_utc, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      )
      .bind(
        input.id,
        input.jobId,
        input.destination,
        input.providerAssetId,
        input.providerSessionId,
        input.renderVersionId,
        input.status,
        input.metadataHash,
        input.scheduledAtUtc,
        input.createdAtUtc,
        input.updatedAtUtc,
      )
      .run();
  }
}
