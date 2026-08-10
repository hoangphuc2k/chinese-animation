import { describe, expect, it } from "vitest";
import { DestinationState, ProcessingState } from "./states.js";

describe("workflow states", () => {
  it("accepts terminal and review states", () => {
    expect(ProcessingState.parse("TIMING_REVIEW_REQUIRED")).toBe("TIMING_REVIEW_REQUIRED");
    expect(ProcessingState.parse("EXPIRED")).toBe("EXPIRED");
    expect(DestinationState.parse("STAGED_HIDDEN")).toBe("STAGED_HIDDEN");
  });

  it("rejects uncontracted states", () => {
    expect(() => ProcessingState.parse("DONE")).toThrow();
  });
});
