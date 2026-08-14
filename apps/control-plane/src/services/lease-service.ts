import { ImportInput, IngestResult, PipelineArtifact, PipelineClaimInput } from "../../../../packages/contracts/src/jobs";
import type { JobType, ProcessingState } from "../../../../packages/contracts/src/states";

import { assertJobTransition } from "../domain/job-state-machine";

export interface LeaseStatement {
  bind(...values: unknown[]): LeaseStatement;
  first<Row extends Record<string, unknown>>(): Promise<Row | null>;
  run(): Promise<{ meta?: { changes?: number }; results: Record<string, unknown>[] }>;
}

export interface LeaseDatabase {
  batch(statements: LeaseStatement[]): Promise<Array<{ meta?: { changes?: number }; results: Record<string, unknown>[] }>>;
  prepare(sql: string): LeaseStatement;
}

interface ClaimedJobRow extends Record<string, unknown> {
  id: string;
  ingest_result_json: string | null;
  job_type: JobType;
  lease_until: string;
  metadata_json: string | null;
  pipeline_lineage_json: string | null;
  rights_metadata_json: string | null;
  script_hash: string | null;
  script_version: number | null;
  source_asset_id: string | null;
  status: ProcessingState;
  version: number;
}

export interface ClaimLeaseInput {
  agentId: string;
  leaseExpiresAtUtc: string;
  nowUtc: string;
  supportedJobTypes: readonly JobType[];
  supportedStates: readonly ProcessingState[];
}

export interface ClaimedLease {
  importInput?: ImportInput;
  ingestResult?: IngestResult;
  jobId: string;
  jobType: JobType;
  jobVersion: number;
  leaseExpiresAtUtc: string;
  leaseToken: string;
  pipelineInput?: PipelineClaimInput;
  state: ProcessingState;
}

export interface HeartbeatLeaseInput {
  agentId: string;
  jobId: string;
  jobVersion: number;
  leaseExpiresAtUtc: string;
  leaseToken: string;
  nowUtc: string;
}

export interface ScriptApprovalLookupInput {
  contentHash: string;
  jobId: string;
  scriptVersion: number;
}

export interface ScriptApprovalSnapshot extends ScriptApprovalLookupInput {
  approved: true;
}

export interface CompleteLeaseInput {
  agentId: string;
  expectedState: ProcessingState;
  jobId: string;
  jobVersion: number;
  leaseToken: string;
  nextState: ProcessingState;
  nowUtc: string;
  step: string;
  ingestResult?: IngestResult;
  pipelineArtifacts?: readonly PipelineArtifact[];
}

export class LeaseInvalidError extends Error {
  constructor() {
    super("The lease token, job version, or lease expiry is invalid.");
  }
}

function createLeaseToken(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

async function tokenHash(token: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function importInputFromClaim(row: ClaimedJobRow): ImportInput {
  if (!row.source_asset_id || !row.metadata_json || !row.rights_metadata_json) {
    throw new Error("Import job is missing its source asset metadata.");
  }
  const sourceMetadata: unknown = JSON.parse(row.metadata_json);
  const rightsMetadata: unknown = JSON.parse(row.rights_metadata_json);
  const locator = typeof sourceMetadata === "object" && sourceMetadata !== null ? (sourceMetadata as { locator?: unknown }).locator : undefined;
  const rightsEvidenceHash =
    typeof rightsMetadata === "object" && rightsMetadata !== null ? (rightsMetadata as { evidenceHash?: unknown }).evidenceHash : undefined;

  return ImportInput.parse({ locator, rightsEvidenceHash, sourceAssetId: row.source_asset_id });
}

function ingestResultFromClaim(row: ClaimedJobRow): IngestResult | undefined {
  if (!row.ingest_result_json) return undefined;
  return IngestResult.parse(JSON.parse(row.ingest_result_json));
}

function transcriptionInputFromClaim(row: ClaimedJobRow): PipelineClaimInput {
  const ingestResult = ingestResultFromClaim(row);
  if (!ingestResult) throw new Error("Transcription job is missing normalized-media lineage.");
  return PipelineClaimInput.parse({
    normalizedMedia: {
      artifactRef: "normalized-media",
      byteCount: ingestResult.normalizedByteCount,
      kind: "NORMALIZED_MEDIA",
      sha256: ingestResult.normalizedHash,
      toolVersion: "ffmpeg-normalize-v1",
    },
    state: "TRANSCRIBING",
  });
}

function pipelineArtifactsFromClaim(row: ClaimedJobRow): PipelineArtifact[] {
  if (!row.pipeline_lineage_json) return [];
  const stored: unknown = JSON.parse(row.pipeline_lineage_json);
  if (!Array.isArray(stored)) throw new Error("Pipeline lineage must be an array.");
  return stored.flatMap((entry) => {
    const artifacts = Array.isArray(entry) ? entry : [entry];
    return artifacts.map((artifact) => PipelineArtifact.parse(artifact));
  });
}

function latestArtifact(artifacts: readonly PipelineArtifact[], kind: PipelineArtifact["kind"]): PipelineArtifact {
  for (let index = artifacts.length - 1; index >= 0; index -= 1) {
    const artifact = artifacts[index];
    if (artifact?.kind === kind) return artifact;
  }
  throw new Error(`Pipeline claim is missing ${kind} lineage.`);
}

function googleProcessingAllowed(row: ClaimedJobRow): true {
  if (!row.rights_metadata_json) throw new Error("Translation claim is missing its rights policy.");
  const metadata: unknown = JSON.parse(row.rights_metadata_json);
  const processors =
    typeof metadata === "object" && metadata !== null
      ? (metadata as { allowedProcessors?: unknown }).allowedProcessors
      : undefined;
  if (!Array.isArray(processors) || !processors.includes("GOOGLE_GEMINI")) {
    throw new Error("Translation rights do not permit Google Gemini processing.");
  }
  return true;
}

function exactScript(row: ClaimedJobRow, artifacts: readonly PipelineArtifact[]): {
  script: PipelineArtifact;
  scriptSha256: string;
  scriptVersion: number;
} {
  const script = latestArtifact(artifacts, "SCRIPT");
  if (!row.script_hash || !row.script_version || script.sha256 !== row.script_hash) {
    throw new Error("Pipeline claim has inconsistent script identity.");
  }
  return { script, scriptSha256: row.script_hash, scriptVersion: row.script_version };
}

function pipelineInputFromClaim(row: ClaimedJobRow): PipelineClaimInput {
  if (row.status === "TRANSCRIBING") return transcriptionInputFromClaim(row);
  const artifacts = pipelineArtifactsFromClaim(row);
  if (row.status === "TRANSLATING") {
    return PipelineClaimInput.parse({
      googleProcessingAllowed: googleProcessingAllowed(row),
      state: row.status,
      transcript: latestArtifact(artifacts, "TRANSCRIPT"),
    });
  }
  if (row.status === "GENERATING_VOICE") {
    return PipelineClaimInput.parse({ ...exactScript(row, artifacts), state: row.status });
  }
  if (row.status === "MIXING") {
    return PipelineClaimInput.parse({
      script: latestArtifact(artifacts, "SCRIPT"),
      state: row.status,
      voice: latestArtifact(artifacts, "VOICE"),
    });
  }
  if (row.status === "RENDERING") {
    const ingestResult = ingestResultFromClaim(row);
    if (!ingestResult) throw new Error("Render claim is missing normalized-media lineage.");
    const normalizedMedia = PipelineArtifact.parse({
      artifactRef: "normalized-media",
      byteCount: ingestResult.normalizedByteCount,
      kind: "NORMALIZED_MEDIA",
      sha256: ingestResult.normalizedHash,
      toolVersion: "ffmpeg-normalize-v1",
    });
    const { scriptSha256, scriptVersion } = exactScript(row, artifacts);
    return PipelineClaimInput.parse({
      mixedAudio: latestArtifact(artifacts, "MIXED_AUDIO"),
      normalizedMedia,
      scriptSha256,
      scriptVersion,
      state: row.status,
      subtitles: latestArtifact(artifacts, "SUBTITLES"),
    });
  }
  if (row.status === "READY_FOR_PREVIEW") {
    return PipelineClaimInput.parse({
      render: latestArtifact(artifacts, "RENDER"),
      state: row.status,
      subtitles: latestArtifact(artifacts, "SUBTITLES"),
    });
  }
  throw new Error("Unsupported pipeline claim state.");
}

export class LeaseService {
  constructor(
    private readonly database: LeaseDatabase,
    private readonly newLeaseToken: () => string = createLeaseToken,
    private readonly newId: () => string = () => crypto.randomUUID(),
  ) {}

  async claim(input: ClaimLeaseInput): Promise<ClaimedLease | null> {
    if (input.supportedJobTypes.length === 0 || input.supportedStates.length === 0) return null;
    const leaseToken = this.newLeaseToken();
    const leaseTokenHash = await tokenHash(leaseToken);
    const jobTypePlaceholders = input.supportedJobTypes.map(() => "?").join(", ");
    const statePlaceholders = input.supportedStates.map(() => "?").join(", ");
    const claimed = await this.database
      .prepare(
        `WITH candidate AS (SELECT jobs.id, jobs.version FROM jobs LEFT JOIN source_assets AS source_asset ON source_asset.id = jobs.source_asset_id WHERE jobs.status IN (${statePlaceholders}) AND jobs.available_at <= ? AND (jobs.lease_until IS NULL OR jobs.lease_until <= ?) AND jobs.job_type IN (${jobTypePlaceholders}) AND (jobs.job_type <> 'IMPORT' OR (source_asset.id IS NOT NULL AND source_asset.rights_revoked_at IS NULL)) ORDER BY jobs.priority DESC, jobs.created_at ASC LIMIT 1) UPDATE jobs SET lease_owner = ?, lease_token_hash = ?, lease_until = ?, attempt_count = attempt_count + 1, version = version + 1, updated_at = ? WHERE id = (SELECT id FROM candidate) AND version = (SELECT version FROM candidate) AND (lease_until IS NULL OR lease_until <= ?) RETURNING id, job_type, status, version, lease_until, source_asset_id, ingest_result_json, pipeline_lineage_json, (SELECT metadata_json FROM source_assets WHERE id = source_asset_id) AS metadata_json, (SELECT rights_metadata_json FROM source_assets WHERE id = source_asset_id) AS rights_metadata_json, (SELECT content_hash FROM script_versions WHERE job_id = jobs.id ORDER BY version DESC LIMIT 1) AS script_hash, (SELECT version FROM script_versions WHERE job_id = jobs.id ORDER BY version DESC LIMIT 1) AS script_version`,
      )
      .bind(
        ...input.supportedStates,
        input.nowUtc,
        input.nowUtc,
        ...input.supportedJobTypes,
        input.agentId,
        leaseTokenHash,
        input.leaseExpiresAtUtc,
        input.nowUtc,
        input.nowUtc,
      )
      .first<ClaimedJobRow>();
    if (!claimed) return null;
    const lease = {
      jobId: claimed.id,
      jobType: claimed.job_type,
      jobVersion: claimed.version,
      leaseExpiresAtUtc: claimed.lease_until,
      leaseToken,
      state: claimed.status,
    };
    if (["TRANSCRIBING", "TRANSLATING", "GENERATING_VOICE", "MIXING", "RENDERING", "READY_FOR_PREVIEW"].includes(claimed.status)) {
      return { ...lease, pipelineInput: pipelineInputFromClaim(claimed) };
    }
    if (claimed.job_type !== "IMPORT") return lease;
    if (claimed.status === "QUEUED" || claimed.status === "INGESTING") {
      return { ...lease, importInput: importInputFromClaim(claimed) };
    }
    const ingestResult = ingestResultFromClaim(claimed);
    return ingestResult ? { ...lease, ingestResult } : lease;
  }

  async getScriptApproval(input: ScriptApprovalLookupInput): Promise<ScriptApprovalSnapshot | null> {
    const approval = await this.database
      .prepare(
        "SELECT 1 AS approved FROM approvals WHERE job_id = ? AND approval_type = 'SCRIPT_APPROVED' AND version = ? AND content_hash = ? AND invalidated_at IS NULL LIMIT 1",
      )
      .bind(input.jobId, input.scriptVersion, input.contentHash)
      .first<{ approved: number }>();
    return approval?.approved === 1 ? { approved: true, ...input } : null;
  }

  async heartbeat(input: HeartbeatLeaseInput): Promise<void> {
    const result = await this.database
      .prepare(
        "UPDATE jobs SET lease_until = ?, updated_at = ? WHERE id = ? AND lease_owner = ? AND lease_token_hash = ? AND lease_until > ? AND version = ?",
      )
      .bind(
        input.leaseExpiresAtUtc,
        input.nowUtc,
        input.jobId,
        input.agentId,
        await tokenHash(input.leaseToken),
        input.nowUtc,
        input.jobVersion,
      )
      .run();
    if (result.meta?.changes !== 1) throw new LeaseInvalidError();
  }

  async complete(input: CompleteLeaseInput): Promise<void> {
    assertJobTransition(input.expectedState, input.nextState);
    if (input.ingestResult && (input.expectedState !== "INGESTING" || input.nextState !== "PROBING")) {
      throw new Error("Ingest metadata may only be recorded when an import advances from INGESTING to PROBING.");
    }
    const ingestResultJson = input.ingestResult ? JSON.stringify(input.ingestResult) : null;
    const pipelineArtifactsJson = input.pipelineArtifacts ? JSON.stringify(input.pipelineArtifacts) : null;
    const detailsJson = JSON.stringify({
      ...(input.ingestResult ? { normalizedHash: input.ingestResult.normalizedHash } : {}),
      ...(input.pipelineArtifacts
        ? {
            artifacts: input.pipelineArtifacts.map(({ byteCount, kind, sha256, toolVersion }) => ({ byteCount, kind, sha256, toolVersion })),
          }
        : {}),
    });
    const results = await this.database.batch([
      this.database
        .prepare(
          "UPDATE jobs SET status = ?, step = ?, ingest_result_json = COALESCE(?, ingest_result_json), pipeline_lineage_json = CASE WHEN ? IS NULL THEN pipeline_lineage_json ELSE json_insert(COALESCE(pipeline_lineage_json, '[]'), '$[#]', json(?)) END, lease_owner = NULL, lease_token_hash = NULL, lease_until = NULL, version = version + 1, updated_at = ? WHERE id = ? AND lease_owner = ? AND lease_token_hash = ? AND lease_until > ? AND status = ? AND version = ? AND (? IS NULL OR job_type = 'IMPORT')",
        )
        .bind(
          input.nextState,
          input.step,
          ingestResultJson,
          pipelineArtifactsJson,
          pipelineArtifactsJson,
          input.nowUtc,
          input.jobId,
          input.agentId,
          await tokenHash(input.leaseToken),
          input.nowUtc,
          input.expectedState,
          input.jobVersion,
          ingestResultJson,
        ),
      this.database
        .prepare(
          "INSERT INTO job_events (id, job_id, event_type, previous_state, next_state, details_json, created_at) SELECT ?, ?, ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM jobs WHERE id = ? AND status = ? AND version = ? AND lease_owner IS NULL)",
        )
        .bind(
          this.newId(),
          input.jobId,
          "STEP_COMPLETED",
          input.expectedState,
          input.nextState,
          detailsJson,
          input.nowUtc,
          input.jobId,
          input.nextState,
          input.jobVersion + 1,
        ),
      this.database
        .prepare(
          "INSERT INTO audit_events (id, actor_id, actor_type, operation, target_type, target_id, target_version, target_hash, request_id, previous_state, next_state, details_json, created_at) SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM jobs WHERE id = ? AND status = ? AND version = ? AND lease_owner IS NULL)",
        )
        .bind(
          this.newId(),
          input.agentId,
          "AGENT",
          "STEP_COMPLETED",
          "JOB",
          input.jobId,
          input.jobVersion + 1,
          null,
          this.newId(),
          input.expectedState,
          input.nextState,
          detailsJson,
          input.nowUtc,
          input.jobId,
          input.nextState,
          input.jobVersion + 1,
        ),
    ]);
    if (results[0]?.meta?.changes !== 1) throw new LeaseInvalidError();
  }
}
