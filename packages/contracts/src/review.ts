import { z } from "zod";
import { JobId, Sha256, SegmentId } from "./ids.js";

export const ApproveScriptRequest = z
  .object({
    jobId: JobId,
    scriptVersion: z.number().int().positive(),
    contentHash: Sha256,
  })
  .strict();
export const SegmentReviewResponse = z
  .object({
    jobId: JobId,
    scriptVersion: z.number().int().positive(),
    segments: z.array(
      z
        .object({
          segmentId: SegmentId,
          startMs: z.number().int().nonnegative(),
          endMs: z.number().int().positive(),
          sourceText: z.string(),
          translatedText: z.string(),
          approvedText: z.string().nullable(),
        })
        .strict()
        .refine((s) => s.endMs > s.startMs, "end must follow start"),
    ),
  })
  .strict();
