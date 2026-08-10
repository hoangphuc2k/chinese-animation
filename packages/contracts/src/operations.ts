import { z } from "zod";

export const ListQuery = z
  .object({ cursor: z.string().min(1).optional(), limit: z.number().int().min(1).max(100).default(25) })
  .strict();
export const OperationAcceptedResponse = z
  .object({ requestId: z.string().uuid(), accepted: z.literal(true) })
  .strict();
