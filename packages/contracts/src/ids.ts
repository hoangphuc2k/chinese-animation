import { z } from "zod";

const Uuid = z.string().uuid();
export const JobId = Uuid.brand<"JobId">();
export const RenderId = Uuid.brand<"RenderId">();
export const SourceVideoId = Uuid.brand<"SourceVideoId">();
export const RequestId = Uuid.brand<"RequestId">();
export const SegmentId = z.string().regex(/^SEG-[0-9]{6}$/).brand<"SegmentId">();
export const IdempotencyKey = z.string().min(8).max(255);
export const Sha256 = z.string().regex(/^[a-fA-F0-9]{64}$/);

export type JobId = z.infer<typeof JobId>;
export type RenderId = z.infer<typeof RenderId>;
export type SourceVideoId = z.infer<typeof SourceVideoId>;
export type SegmentId = z.infer<typeof SegmentId>;
