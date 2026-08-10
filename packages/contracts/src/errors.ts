import { z } from "zod";
import { ErrorCode } from "./states.js";

export const ApiError = z
  .object({
    error: z
      .object({
        code: ErrorCode,
        message: z.string().min(1),
        requestId: z.string().uuid(),
        retryable: z.boolean(),
        details: z.record(z.string(), z.unknown()).optional(),
      })
      .strict(),
  })
  .strict();

export type ApiError = z.infer<typeof ApiError>;
