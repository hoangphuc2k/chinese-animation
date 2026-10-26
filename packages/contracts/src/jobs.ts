import { z } from "zod";
import { IngestLocator } from "./ingest.js";
import { JobId, RequestId, SegmentId, Sha256 } from "./ids.js";
import { JobType, ProcessingState, SourceState } from "./states.js";

const IsoUtc = z
  .string()
  .datetime({ offset: false })
  .refine((v) => v.endsWith("Z"), "UTC required");
export const ClaimJobRequest = z
  .object({
    supportedJobTypes: z.array(JobType).min(1).max(6),
    supportedStates: z.array(ProcessingState).min(1).max(21),
  })
  .strict()
  .superRefine((value, context) => {
    if (new Set(value.supportedJobTypes).size !== value.supportedJobTypes.length) {
      context.addIssue({ code: "custom", message: "supported job types must be unique" });
    }
    if (new Set(value.supportedStates).size !== value.supportedStates.length) {
      context.addIssue({ code: "custom", message: "supported processing states must be unique" });
    }
  });
export const ImportInput = z
  .object({
    locator: IngestLocator,
    rightsEvidenceHash: Sha256,
    sourceAssetId: JobId,
  })
  .strict();
export type ImportInput = z.infer<typeof ImportInput>;
const TechnicalMetadata = z
  .object({
    durationSeconds: z.number().nonnegative().finite(),
    formatName: z.string().min(1).max(80),
    hasAudio: z.boolean(),
    hasVideo: z.boolean(),
  })
  .strict();
export const IngestResult = z
  .object({
    normalizedByteCount: z.number().int().nonnegative().safe(),
    normalizedHash: Sha256,
    sourceByteCount: z.number().int().nonnegative().safe(),
    sourceHash: Sha256,
    technicalMetadata: TechnicalMetadata,
  })
  .strict();
export type IngestResult = z.infer<typeof IngestResult>;
export const PipelineArtifactKind = z.enum([
  "EXTRACTED_AUDIO",
  "MIXED_AUDIO",
  "NORMALIZED_MEDIA",
  "OCR_TRACK",
  "RENDER",
  "SCRIPT",
  "SUBTITLES",
  "TRANSCRIPT",
  "VOICE",
]);
export type PipelineArtifactKind = z.infer<typeof PipelineArtifactKind>;
export const PipelineArtifact = z
  .object({
    artifactRef: z
      .string()
      .min(1)
      .max(160)
      .regex(/^[A-Za-z0-9._:-]+$/),
    byteCount: z.number().int().nonnegative().safe(),
    kind: PipelineArtifactKind,
    sha256: Sha256,
    toolVersion: z.string().min(1).max(160),
  })
  .strict();
export type PipelineArtifact = z.infer<typeof PipelineArtifact>;
function artifactOfKind(kind: PipelineArtifactKind) {
  return PipelineArtifact.refine((artifact) => artifact.kind === kind, {
    message: `pipeline input artifact must have kind ${kind}`,
  });
}

export const PipelineClaimInput = z.discriminatedUnion("state", [
  z
    .object({
      normalizedMedia: artifactOfKind("NORMALIZED_MEDIA"),
      state: z.literal("EXTRACTING_AUDIO"),
    })
    .strict(),
  z
    .object({
      extractedAudio: artifactOfKind("EXTRACTED_AUDIO"),
      state: z.literal("TRANSCRIBING"),
    })
    .strict(),
  z
    .object({
      googleProcessingAllowed: z.literal(true),
      state: z.literal("TRANSLATING"),
      transcript: artifactOfKind("TRANSCRIPT"),
    })
    .strict(),
  z
    .object({
      script: artifactOfKind("SCRIPT"),
      scriptSha256: Sha256,
      scriptVersion: z.number().int().positive(),
      state: z.literal("GENERATING_VOICE"),
    })
    .strict(),
  z
    .object({
      script: artifactOfKind("SCRIPT"),
      state: z.literal("MIXING"),
      voice: artifactOfKind("VOICE"),
    })
    .strict(),
  z
    .object({
      mixedAudio: artifactOfKind("MIXED_AUDIO"),
      normalizedMedia: artifactOfKind("NORMALIZED_MEDIA"),
      scriptSha256: Sha256,
      scriptVersion: z.number().int().positive(),
      state: z.literal("RENDERING"),
      subtitles: artifactOfKind("SUBTITLES"),
    })
    .strict(),
  z
    .object({
      render: artifactOfKind("RENDER"),
      state: z.literal("READY_FOR_PREVIEW"),
      subtitles: artifactOfKind("SUBTITLES"),
    })
    .strict(),
]);
export type PipelineClaimInput = z.infer<typeof PipelineClaimInput>;
const outputKindsByTransition = {
  "EXTRACTING_AUDIO->TRANSCRIBING": ["EXTRACTED_AUDIO"],
  "GENERATING_VOICE->MIXING": ["VOICE"],
  "MIXING->RENDERING": ["MIXED_AUDIO", "SUBTITLES"],
  "OCR_OPTIONAL->TRANSLATING": ["OCR_TRACK"],
  "RENDERING->READY_FOR_PREVIEW": ["RENDER", "SUBTITLES"],
  "TRANSCRIBING->OCR_OPTIONAL": ["TRANSCRIPT"],
  "TRANSCRIBING->TRANSLATING": ["TRANSCRIPT"],
  "TRANSLATING->WAITING_SCRIPT_REVIEW": ["SCRIPT"],
} as const satisfies Record<string, readonly z.infer<typeof PipelineArtifactKind>[]>;
export const CleanupInput = z.object({ targetJobId: JobId }).strict();
export type CleanupInput = z.infer<typeof CleanupInput>;
export const FailureCode = z.enum([
  "RETRYABLE_NETWORK",
  "RATE_LIMITED",
  "AUTH_REQUIRED",
  "RIGHTS_BLOCKED",
  "QUOTA_EXHAUSTED",
  "INVALID_AI_RESPONSE",
  "MEDIA_INVALID",
  "DISK_PRESSURE",
  "MANUAL_REVIEW_REQUIRED",
]);
export type FailureCode = z.infer<typeof FailureCode>;
export const ClaimJobResponse = z
  .object({
    cleanupInput: CleanupInput.optional(),
    importInput: ImportInput.optional(),
    ingestResult: IngestResult.optional(),
    jobId: JobId,
    jobType: JobType,
    jobVersion: z.number().int().positive(),
    state: ProcessingState,
    leaseToken: z.string().min(32),
    leaseExpiresAtUtc: IsoUtc,
    requestId: RequestId,
    pipelineInput: PipelineClaimInput.optional(),
  })
  .strict()
  .superRefine((value, context) => {
    const needsImportInput =
      value.jobType === "IMPORT" && (value.state === "QUEUED" || value.state === "INGESTING");
    if (needsImportInput && !value.importInput) {
      context.addIssue({ code: "custom", message: "import claims require import input" });
    }
    if (!needsImportInput && value.importInput) {
      context.addIssue({
        code: "custom",
        message: "only queued or ingesting import claims may include import input",
      });
    }
    if ((value.jobType === "DELETE_EXPIRED") !== (value.cleanupInput !== undefined)) {
      context.addIssue({ code: "custom", message: "only cleanup claims carry cleanup input" });
    }
    const needsIngestResult = value.jobType === "IMPORT" && value.state === "PROBING";
    if (needsIngestResult !== (value.ingestResult !== undefined)) {
      context.addIssue({
        code: "custom",
        message: "only import probing claims require sanitized ingest results",
      });
    }
    const pipelineStates = new Set([
      "EXTRACTING_AUDIO",
      "TRANSCRIBING",
      "TRANSLATING",
      "GENERATING_VOICE",
      "MIXING",
      "RENDERING",
      "READY_FOR_PREVIEW",
    ]);
    if (pipelineStates.has(value.state) && !value.pipelineInput) {
      context.addIssue({
        code: "custom",
        message: "pipeline claims require state-specific input",
      });
    }
    if (value.pipelineInput && value.pipelineInput.state !== value.state) {
      context.addIssue({
        code: "custom",
        message: "pipeline input state must match the claimed job state",
      });
    }
    if (!pipelineStates.has(value.state) && value.pipelineInput) {
      context.addIssue({
        code: "custom",
        message: "pipeline input is valid only for executable pipeline claims",
      });
    }
  });
export const HeartbeatRequest = z
  .object({ jobVersion: z.number().int().positive(), leaseToken: z.string().min(32) })
  .strict();
export const PreviewDescriptor = z
  .object({
    expiresAtUtc: IsoUtc,
    renderSha256: Sha256,
    token: z
      .string()
      .min(32)
      .max(128)
      .regex(/^[A-Za-z0-9_-]+$/),
  })
  .strict();
export type PreviewDescriptor = z.infer<typeof PreviewDescriptor>;
export const CompleteStepRequest = z
  .object({
    leaseToken: z.string().min(32),
    jobVersion: z.number().int().positive(),
    expectedState: ProcessingState,
    failureCode: FailureCode.optional(),
    state: ProcessingState,
    step: z.string().min(1).max(80),
    outputHash: Sha256.optional(),
    bytesWritten: z.number().int().nonnegative().safe().optional(),
    ingestResult: IngestResult.optional(),
    pipelineArtifacts: z.array(PipelineArtifact).min(1).max(100).optional(),
    preview: PreviewDescriptor.optional(),
    segmentIds: z.array(SegmentId).max(10000).optional(),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.ingestResult && (value.expectedState !== "INGESTING" || value.state !== "PROBING")) {
      context.addIssue({
        code: "custom",
        message: "ingest results require an INGESTING to PROBING transition",
      });
    }
    if (value.pipelineArtifacts) {
      const transition = `${value.expectedState}->${value.state}`;
      const allowedKinds: readonly PipelineArtifactKind[] | undefined =
        outputKindsByTransition[transition as keyof typeof outputKindsByTransition];
      if (
        !allowedKinds ||
        value.pipelineArtifacts.some((artifact) => !allowedKinds.includes(artifact.kind))
      ) {
        context.addIssue({
          code: "custom",
          message: "pipeline artifacts are not valid for this processing transition",
        });
      }
    }
    if (value.failureCode && value.state !== "FAILED" && value.state !== "MANUAL_REVIEW_REQUIRED") {
      context.addIssue({
        code: "custom",
        message: "failure codes require a FAILED or MANUAL_REVIEW_REQUIRED completion",
      });
    }
    const isPreviewCompletion =
      value.expectedState === "READY_FOR_PREVIEW" && value.state === "WAITING_PUBLISH_APPROVAL";
    if (isPreviewCompletion !== (value.preview !== undefined)) {
      context.addIssue({
        code: "custom",
        message: "preview descriptors require the canonical preview completion transition",
      });
    }
  });
export const JobDetailResponse = z
  .object({
    jobId: JobId,
    jobType: JobType,
    lastEventType: z.string().min(1).nullable(),
    latestScript: z
      .object({ contentHash: Sha256, version: z.number().int().positive() })
      .strict()
      .nullable(),
    retentionDeadlineUtc: IsoUtc.nullable(),
    state: ProcessingState,
    step: z.string().min(1),
    version: z.number().int().positive(),
  })
  .strict();
export type JobDetailResponse = z.infer<typeof JobDetailResponse>;
export const DashboardInboxResponse = z
  .object({
    items: z.array(
      z
        .object({
          sourceVideoId: z.string().uuid(),
          title: z.string().min(1),
          sourceState: SourceState,
          durationSeconds: z.number().nonnegative().finite(),
          discoveredAtUtc: IsoUtc,
        })
        .strict(),
    ),
    nextCursor: z.string().min(1).nullable(),
  })
  .strict();
