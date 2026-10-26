import { z } from "zod";
import { IngestKind } from "./states.js";

const HttpsLocator = z.object({
  kind: z.literal(IngestKind.enum.CREATOR_HTTPS_URL),
  url: z.string().url().refine((value) => value.startsWith("https://"), "HTTPS is required"),
}).strict();
const OciLocator = z.object({
  kind: z.literal(IngestKind.enum.OCI_INBOX_FILE),
  inboxFileId: z.string().min(1).max(128).regex(/^[A-Za-z0-9._-]+$/),
}).strict();
export const IngestLocator = z.discriminatedUnion("kind", [HttpsLocator, OciLocator]);
export const AiProcessor = z.enum(["GOOGLE_GEMINI", "AZURE_SPEECH"]);
export const CreateImportRequest = z
  .object({
    allowedProcessors: z
      .array(AiProcessor)
      .max(2)
      .refine((value) => new Set(value).size === value.length, "allowed processors must be unique")
      .default([]),
    locator: IngestLocator,
    rightsEvidence: z.string().min(1).max(2000),
  })
  .strict();
export const CreateImportResponse = z
  .object({ jobId: z.string().uuid(), sourceVideoId: z.string().uuid(), state: z.literal("QUEUED") })
  .strict();
