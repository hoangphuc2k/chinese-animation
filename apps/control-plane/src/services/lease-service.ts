import {
  ImportInput,
  IngestResult,
  PipelineArtifact,
  PipelineClaimInput,
  CleanupInput,
  type FailureCode,
  type PreviewDescriptor,
} from "../../../../packages/contracts/src/jobs";
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
  target_job_id: string | null;
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
  cleanupInput?: CleanupInput;
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
  failureCode?: FailureCode;
  ingestResult?: IngestResult;
  pipelineArtifacts?: readonly PipelineArtifact[];
  preview?: PreviewDescriptor;
}

/** Automated claims allowed per step before the job needs an administrator (spec section 14). */
export const MAX_STEP_ATTEMPTS = 5;
const RETENTION_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;

function plusRetentionWindow(nowUtc: string): string {
  return new Date(new Date(nowUtc).getTime() + RETENTION_WINDOW_MS).toISOString();
}

function allowsGoogleProcessing(rightsMetadataJson: string | null | undefined): boolean {
  if (!rightsMetadataJson) return false;
  const metadata: unknown = JSON.parse(rightsMetadataJson);
  const processors =
    typeof metadata === "object" && metadata !== null ? (metadata as { allowedProcessors?: unknown }).allowedProcessors : undefined;
  return Array.isArray(processors) && processors.includes("GOOGLE_GEMINI");
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

function extractionInputFromClaim(row: ClaimedJobRow): PipelineClaimInput {
  const ingestResult = ingestResultFromClaim(row);
  if (!ingestResult) throw new Error("Audio extraction job is missing normalized-media lineage.");
  return PipelineClaimInput.parse({
    normalizedMedia: {
      artifactRef: "normalized-media",
      byteCount: ingestResult.normalizedByteCount,
      kind: "NORMALIZED_MEDIA",
      sha256: ingestResult.normalizedHash,
      toolVersion: "ffmpeg-normalize-v1",
    },
    state: "EXTRACTING_AUDIO",
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
  // The claim query already excludes TRANSLATING rows without consent; this is a defensive re-check.
  if (!allowsGoogleProcessing(row.rights_metadata_json)) {
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
  if (row.status === "EXTRACTING_AUDIO") return extractionInputFromClaim(row);
  const artifacts = pipelineArtifactsFromClaim(row);
  if (row.status === "TRANSCRIBING") {
    return PipelineClaimInput.parse({ extractedAudio: latestArtifact(artifacts, "EXTRACTED_AUDIO"), state: row.status });
  }
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
        `WITH candidate AS (SELECT jobs.id, jobs.version FROM jobs LEFT JOIN source_assets AS source_asset ON source_asset.id = jobs.source_asset_id WHERE jobs.status IN (${statePlaceholders}) AND jobs.available_at <= ? AND (jobs.lease_until IS NULL OR jobs.lease_until <= ?) AND jobs.job_type IN (${jobTypePlaceholders}) AND jobs.attempt_count < ${MAX_STEP_ATTEMPTS} AND (jobs.job_type = 'DELETE_EXPIRED' OR (source_asset.id IS NOT NULL AND source_asset.rights_revoked_at IS NULL)) AND (jobs.status <> 'TRANSLATING' OR EXISTS (SELECT 1 FROM json_each(source_asset.rights_metadata_json, '$.allowedProcessors') AS processor WHERE processor.value = 'GOOGLE_GEMINI')) ORDER BY jobs.priority DESC, jobs.created_at ASC LIMIT 1) UPDATE jobs SET lease_owner = ?, lease_token_hash = ?, lease_until = ?, attempt_count = attempt_count + 1, version = version + 1, updated_at = ? WHERE id = (SELECT id FROM candidate) AND version = (SELECT version FROM candidate) AND (lease_until IS NULL OR lease_until <= ?) RETURNING id, job_type, status, version, lease_until, source_asset_id, target_job_id, ingest_result_json, pipeline_lineage_json, (SELECT metadata_json FROM source_assets WHERE id = source_asset_id) AS metadata_json, (SELECT rights_metadata_json FROM source_assets WHERE id = source_asset_id) AS rights_metadata_json, (SELECT content_hash FROM script_versions WHERE job_id = jobs.id ORDER BY version DESC LIMIT 1) AS script_hash, (SELECT version FROM script_versions WHERE job_id = jobs.id ORDER BY version DESC LIMIT 1) AS script_version`,
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
    if (["EXTRACTING_AUDIO", "TRANSCRIBING", "TRANSLATING", "GENERATING_VOICE", "MIXING", "RENDERING", "READY_FOR_PREVIEW"].includes(claimed.status)) {
      return { ...lease, pipelineInput: pipelineInputFromClaim(claimed) };
    }
    if (claimed.job_type === "DELETE_EXPIRED") {
      if (!claimed.target_job_id) throw new Error("Cleanup job is missing its target job.");
      return { ...lease, cleanupInput: CleanupInput.parse({ targetJobId: claimed.target_job_id }) };
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
    // Cleanup commands are the only jobs that finish straight from QUEUED; the UPDATE enforces the job type.
    const isCleanupCompletion = input.expectedState === "QUEUED" && input.nextState === "COMPLETED";
    if (!isCleanupCompletion) assertJobTransition(input.expectedState, input.nextState);
    if (input.failureCode && input.nextState !== "FAILED" && input.nextState !== "MANUAL_REVIEW_REQUIRED") {
      throw new Error("Failure codes may only accompany a FAILED or MANUAL_REVIEW_REQUIRED completion.");
    }
    if (input.ingestResult && (input.expectedState !== "INGESTING" || input.nextState !== "PROBING")) {
      throw new Error("Ingest metadata may only be recorded when an import advances from INGESTING to PROBING.");
    }
    const ingestResultJson = input.ingestResult ? JSON.stringify(input.ingestResult) : null;
    const pipelineArtifactsJson = input.pipelineArtifacts ? JSON.stringify(input.pipelineArtifacts) : null;
    const previewJson = input.preview ? JSON.stringify(input.preview) : null;
    const isPreviewCompletion = input.expectedState === "READY_FOR_PREVIEW" && input.nextState === "WAITING_PUBLISH_APPROVAL";
    if (isPreviewCompletion !== (input.preview !== undefined)) {
      throw new Error("The canonical preview transition requires exactly one preview descriptor.");
    }
    if (input.preview) {
      const lifetimeMs = new Date(input.preview.expiresAtUtc).getTime() - new Date(input.nowUtc).getTime();
      if (!Number.isFinite(lifetimeMs) || lifetimeMs <= 0 || lifetimeMs > 300_000) {
        throw new Error("Preview descriptors require the canonical transition and a maximum five-minute lifetime.");
      }
    }
    let nextState = input.nextState;
    let step = input.step;
    if (input.expectedState === "TRANSCRIBING" && input.nextState === "TRANSLATING") {
      const rights = await this.database
        .prepare(
          "SELECT source_asset.rights_metadata_json FROM jobs JOIN source_assets AS source_asset ON source_asset.id = jobs.source_asset_id WHERE jobs.id = ?",
        )
        .bind(input.jobId)
        .first<{ rights_metadata_json: string | null }>();
      // Without recorded consent the job stops for manual translation (spec section 6); it never reaches Gemini.
      if (!allowsGoogleProcessing(rights?.rights_metadata_json)) {
        nextState = "MANUAL_REVIEW_REQUIRED";
        step = "TRANSLATION_CONSENT_MISSING";
      }
    }
    const scriptArtifact =
      input.expectedState === "TRANSLATING" && nextState === "WAITING_SCRIPT_REVIEW"
        ? input.pipelineArtifacts?.find((artifact) => artifact.kind === "SCRIPT")
        : undefined;
    if (input.expectedState === "TRANSLATING" && nextState === "WAITING_SCRIPT_REVIEW" && !scriptArtifact) {
      throw new Error("A translated script requires its SCRIPT artifact.");
    }
    const retentionDeadline = nextState === "READY_FOR_PREVIEW" || nextState === "FAILED" ? plusRetentionWindow(input.nowUtc) : null;
    const detailsJson = JSON.stringify({
      ...(input.failureCode ? { failureCode: input.failureCode } : {}),
      ...(input.ingestResult ? { normalizedHash: input.ingestResult.normalizedHash } : {}),
      ...(input.pipelineArtifacts
        ? {
            artifacts: input.pipelineArtifacts.map(({ byteCount, kind, sha256, toolVersion }) => ({ byteCount, kind, sha256, toolVersion })),
          }
        : {}),
      ...(input.preview ? { previewExpiresAtUtc: input.preview.expiresAtUtc, renderSha256: input.preview.renderSha256 } : {}),
    });
    const completedJobExists = "EXISTS (SELECT 1 FROM jobs WHERE id = ? AND status = ? AND version = ? AND lease_owner IS NULL)";
    const statements = [
      this.database
        .prepare(
          "UPDATE jobs SET status = ?, step = ?, ingest_result_json = COALESCE(?, ingest_result_json), pipeline_lineage_json = CASE WHEN ? IS NULL THEN pipeline_lineage_json ELSE json_insert(COALESCE(pipeline_lineage_json, '[]'), '$[#]', json(?)) END, preview_descriptor_json = COALESCE(?, preview_descriptor_json), retention_deadline = COALESCE(?, retention_deadline), attempt_count = 0, lease_owner = NULL, lease_token_hash = NULL, lease_until = NULL, version = version + 1, updated_at = ? WHERE id = ? AND lease_owner = ? AND lease_token_hash = ? AND lease_until > ? AND status = ? AND version = ? AND (job_type = 'DELETE_EXPIRED') = ? AND (? IS NULL OR job_type = 'IMPORT') AND (? IS NULL OR EXISTS (SELECT 1 FROM json_tree(jobs.pipeline_lineage_json) AS artifact WHERE artifact.type = 'object' AND json_extract(artifact.value, '$.kind') = 'RENDER' AND json_extract(artifact.value, '$.sha256') = ?))",
        )
        .bind(
          nextState,
          step,
          ingestResultJson,
          pipelineArtifactsJson,
          pipelineArtifactsJson,
          previewJson,
          retentionDeadline,
          input.nowUtc,
          input.jobId,
          input.agentId,
          await tokenHash(input.leaseToken),
          input.nowUtc,
          input.expectedState,
          input.jobVersion,
          isCleanupCompletion ? 1 : 0,
          ingestResultJson,
          input.preview?.renderSha256 ?? null,
          input.preview?.renderSha256 ?? null,
        ),
      this.database
        .prepare(
          `INSERT INTO job_events (id, job_id, event_type, previous_state, next_state, details_json, created_at) SELECT ?, ?, ?, ?, ?, ?, ? WHERE ${completedJobExists}`,
        )
        .bind(
          this.newId(),
          input.jobId,
          "STEP_COMPLETED",
          input.expectedState,
          nextState,
          detailsJson,
          input.nowUtc,
          input.jobId,
          nextState,
          input.jobVersion + 1,
        ),
      this.database
        .prepare(
          `INSERT INTO audit_events (id, actor_id, actor_type, operation, target_type, target_id, target_version, target_hash, request_id, previous_state, next_state, details_json, created_at) SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE ${completedJobExists}`,
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
          nextState,
          detailsJson,
          input.nowUtc,
          input.jobId,
          nextState,
          input.jobVersion + 1,
        ),
    ];
    if (scriptArtifact) {
      // The agent's translated script becomes the reviewable script version; its hash is computed by the agent
      // from the artifact bytes, never supplied by the browser.
      statements.push(
        this.database
          .prepare(
            `INSERT INTO script_versions (id, job_id, version, content_hash, transcript_hash, translation_hash, metadata_hash, voice_settings_hash, created_at) SELECT ?, ?, (SELECT COALESCE(MAX(version), 0) + 1 FROM script_versions WHERE job_id = ?), ?, (SELECT json_extract(artifact.value, '$.sha256') FROM jobs, json_tree(jobs.pipeline_lineage_json) AS artifact WHERE jobs.id = ? AND artifact.type = 'object' AND json_extract(artifact.value, '$.kind') = 'TRANSCRIPT' ORDER BY artifact.id DESC LIMIT 1), ?, NULL, NULL, ? WHERE ${completedJobExists}`,
          )
          .bind(
            this.newId(),
            input.jobId,
            input.jobId,
            scriptArtifact.sha256,
            input.jobId,
            scriptArtifact.sha256,
            input.nowUtc,
            input.jobId,
            nextState,
            input.jobVersion + 1,
          ),
      );
    }
    const results = await this.database.batch(statements);
    if (results[0]?.meta?.changes !== 1) throw new LeaseInvalidError();
  }
}
