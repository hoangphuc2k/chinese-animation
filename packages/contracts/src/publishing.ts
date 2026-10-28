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
    hashtags: z.array(z.string().regex(/^#[\p{L}\p{M}\p{N}_]+$/u)).max(100),
  }).strict(),
]);
export type DestinationMetadata = z.infer<typeof DestinationMetadata>;

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const object = value as Record<string, unknown>;
    return `{${Object.keys(object)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(object[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

/**
 * SHA-256 of the destination metadata the administrator approves. It must stay byte-identical to the
 * processing agent's `metadata_sha256`: entries sorted by destination, keys sorted, compact, UTF-8.
 */
export async function publishMetadataHash(metadata: readonly DestinationMetadata[]): Promise<string> {
  const entries = [...metadata].sort((left, right) => left.destination.localeCompare(right.destination));
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonicalJson(entries)));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

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
