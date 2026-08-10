import { DestinationState as DestinationStateSchema, type DestinationState } from "../../../../packages/contracts/src/states";

export class DestinationTransitionError extends Error {
  constructor(from: DestinationState, to: DestinationState) {
    super(`Destination transition from ${from} to ${to} is not allowed.`);
  }
}

const terminalStates = new Set<DestinationState>(["PUBLISHED", "REJECTED", "CANCELLED"]);
const forwardTransitions: Partial<Record<DestinationState, readonly DestinationState[]>> = {
  BLOCKED_BY_GATE: ["READY_TO_UPLOAD", "MANUAL_ACTION_REQUIRED", "CANCELLED"],
  FAILED: ["UPLOAD_QUEUED", "MANUAL_ACTION_REQUIRED", "CANCELLED"],
  MANUAL_ACTION_REQUIRED: ["READY_TO_UPLOAD", "PUBLISHED", "CANCELLED"],
  NOT_REQUESTED: ["BLOCKED_BY_GATE", "READY_TO_UPLOAD", "CANCELLED"],
  PROCESSING: ["STAGED_HIDDEN", "SCHEDULED", "PUBLISHED", "FAILED", "MANUAL_ACTION_REQUIRED"],
  READY_TO_UPLOAD: ["UPLOAD_QUEUED", "MANUAL_ACTION_REQUIRED", "CANCELLED"],
  SCHEDULED: ["PUBLISHED", "FAILED", "MANUAL_ACTION_REQUIRED", "CANCELLED"],
  STAGED_HIDDEN: ["SCHEDULED", "PUBLISHED", "MANUAL_ACTION_REQUIRED", "CANCELLED"],
  UPLOADING: ["PROCESSING", "FAILED", "MANUAL_ACTION_REQUIRED"],
  UPLOAD_QUEUED: ["UPLOADING", "CANCELLED"],
};

export function isTerminalDestinationState(state: DestinationState): boolean {
  return terminalStates.has(state);
}

export function assertDestinationTransition(from: DestinationState, to: DestinationState): void {
  DestinationStateSchema.parse(from);
  DestinationStateSchema.parse(to);
  if (isTerminalDestinationState(from) || !forwardTransitions[from]?.includes(to)) {
    throw new DestinationTransitionError(from, to);
  }
}
