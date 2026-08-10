import { z } from "zod";

export const SourceState = z.enum([
  "DISCOVERED",
  "TRANSLATING_METADATA",
  "INBOX_READY",
  "IGNORED",
  "IMPORT_SELECTED",
]);
export const DiscoveryState = SourceState;
export const ProcessingState = z.enum([
  "QUEUED",
  "INGESTING",
  "PROBING",
  "EXTRACTING_AUDIO",
  "TRANSCRIBING",
  "OCR_OPTIONAL",
  "TRANSLATING",
  "WAITING_SCRIPT_REVIEW",
  "GENERATING_VOICE",
  "MIXING",
  "RENDERING",
  "READY_FOR_PREVIEW",
  "WAITING_PUBLISH_APPROVAL",
  "TIMING_REVIEW_REQUIRED",
  "MANUAL_REVIEW_REQUIRED",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
  "EXPIRED",
  "RIGHTS_BLOCKED",
]);
export const DestinationState = z.enum([
  "NOT_REQUESTED",
  "BLOCKED_BY_GATE",
  "READY_TO_UPLOAD",
  "UPLOAD_QUEUED",
  "UPLOADING",
  "PROCESSING",
  "STAGED_HIDDEN",
  "SCHEDULED",
  "PUBLISHED",
  "FAILED",
  "REJECTED",
  "MANUAL_ACTION_REQUIRED",
  "CANCELLED",
]);
export const AdapterHealth = z.enum(["HEALTHY", "DEGRADED", "DISABLED", "UNKNOWN"]);
export const JobType = z.enum([
  "IMPORT",
  "DISCOVERY",
  "LOCALIZE",
  "RENDER",
  "PUBLISH",
  "DELETE_EXPIRED",
]);
export const ErrorCode = z.enum([
  "INVALID_REQUEST",
  "UNAUTHENTICATED",
  "FORBIDDEN",
  "NOT_FOUND",
  "CONFLICT",
  "STALE_VERSION",
  "LEASE_INVALID",
  "QUOTA_EXCEEDED",
  "RIGHTS_BLOCKED",
  "EXTERNAL_PROVIDER_ERROR",
  "VALIDATION_FAILED",
  "INTERNAL_ERROR",
]);
export const ApprovalType = z.enum(["SCRIPT_APPROVED", "PUBLISH_APPROVED"]);
export const IngestKind = z.enum(["CREATOR_HTTPS_URL", "OCI_INBOX_FILE"]);

export type ProcessingState = z.infer<typeof ProcessingState>;
export type DestinationState = z.infer<typeof DestinationState>;
export type JobType = z.infer<typeof JobType>;
