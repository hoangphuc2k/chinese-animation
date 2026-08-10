import { ProcessingState as ProcessingStateSchema, type ProcessingState } from "../../../../packages/contracts/src/states";

export class JobTransitionError extends Error {
  constructor(from: ProcessingState, to: ProcessingState) {
    super(`Processing transition from ${from} to ${to} is not allowed.`);
  }
}

const terminalStates = new Set<ProcessingState>(["COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "RIGHTS_BLOCKED"]);
const failureStates: readonly ProcessingState[] = ["FAILED", "CANCELLED", "EXPIRED", "RIGHTS_BLOCKED"];

const forwardTransitions: Partial<Record<ProcessingState, readonly ProcessingState[]>> = {
  EXTRACTING_AUDIO: ["TRANSCRIBING"],
  GENERATING_VOICE: ["MIXING"],
  INGESTING: ["PROBING"],
  MANUAL_REVIEW_REQUIRED: ["WAITING_SCRIPT_REVIEW"],
  MIXING: ["RENDERING"],
  OCR_OPTIONAL: ["TRANSLATING"],
  PROBING: ["EXTRACTING_AUDIO"],
  QUEUED: ["INGESTING"],
  READY_FOR_PREVIEW: ["WAITING_PUBLISH_APPROVAL"],
  RENDERING: ["READY_FOR_PREVIEW", "TIMING_REVIEW_REQUIRED", "MANUAL_REVIEW_REQUIRED"],
  TIMING_REVIEW_REQUIRED: ["WAITING_SCRIPT_REVIEW"],
  TRANSCRIBING: ["OCR_OPTIONAL", "TRANSLATING"],
  TRANSLATING: ["WAITING_SCRIPT_REVIEW", "TIMING_REVIEW_REQUIRED", "MANUAL_REVIEW_REQUIRED"],
  WAITING_PUBLISH_APPROVAL: ["COMPLETED"],
  WAITING_SCRIPT_REVIEW: ["GENERATING_VOICE", "TRANSLATING"],
};

export function isTerminalJobState(state: ProcessingState): boolean {
  return terminalStates.has(state);
}

export function assertJobTransition(from: ProcessingState, to: ProcessingState): void {
  ProcessingStateSchema.parse(from);
  ProcessingStateSchema.parse(to);
  if (isTerminalJobState(from)) throw new JobTransitionError(from, to);
  if (failureStates.includes(to) || forwardTransitions[from]?.includes(to)) return;
  throw new JobTransitionError(from, to);
}
