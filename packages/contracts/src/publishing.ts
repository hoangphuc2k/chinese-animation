import { z } from "zod";
import { DestinationState } from "./states.js";
import { JobId, RenderId, Sha256 } from "./ids.js";

const Destination = z.enum(["YOUTUBE", "FACEBOOK"]);
export const DestinationMetadata = z.discriminatedUnion("destination", [
  z.object({
    destination: z.literal("YOUTUBE"),
    title: z.string().min(1).max(100),
    description: z.string().min(1),
    tags: z.array(z.string().min(1)).max(500),
  }).strict(),
  z.object({
    destination: z.literal("FACEBOOK"),
    caption: z.string().min(1),
    hashtags: z.array(z.string().regex(/^#[A-Za-z0-9_]+$/)).max(100),
  }).strict(),
]);
const Schedule = z
  .string()
  .datetime({ offset: false })
  .refine((v) => v.endsWith("Z"), "UTC required")
  .nullable();
export const ApprovePublishRequest = z
  .object({
    jobId: JobId,
    renderId: RenderId,
    renderVersion: z.number().int().positive(),
    metadataHash: Sha256,
    destinations: z.array(Destination).min(1).max(2),
    scheduleAtUtc: Schedule,
    metadata: z.array(DestinationMetadata).min(1).max(2),
  })
  .strict()
  .superRefine((value, ctx) => {
    if (new Set(value.destinations).size !== value.destinations.length) {
      ctx.addIssue({ code: "custom", path: ["destinations"], message: "duplicate destination" });
    }
    const metadataDestinations = value.metadata.map((entry) => entry.destination).sort().join(",");
    const selectedDestinations = [...value.destinations].sort().join(",");
    if (metadataDestinations !== selectedDestinations) {
      ctx.addIssue({ code: "custom", path: ["metadata"], message: "metadata must match destinations" });
    }
  });
export const RenderReviewResponse = z
  .object({
    jobId: JobId,
    renderId: RenderId,
    renderVersion: z.number().int().positive(),
    checksum: Sha256,
    destinations: z.record(Destination, DestinationState),
    previewUrl: z.string().url(),
  })
  .strict();
