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
/** Third-party AI processors the rights evidence explicitly allows (spec section 6). */
export const AllowedProcessor = z.enum(["GOOGLE_GEMINI"]);
export const CreateImportRequest = z
  .object({
    allowedProcessors: z.array(AllowedProcessor).max(1).default([]),
    locator: IngestLocator,
    rightsEvidence: z.string().min(1).max(2000),
  })
  .strict();
export const CreateImportResponse = z
  .object({ jobId: z.string().uuid(), sourceVideoId: z.string().uuid(), state: z.literal("QUEUED") })
  .strict();
