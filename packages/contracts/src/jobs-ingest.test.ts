import { describe, expect, it } from "vitest";

import { CompleteStepRequest } from "./jobs.js";

const ingestResult = {
  normalizedByteCount: 10,
  normalizedHash: "a".repeat(64),
  sourceByteCount: 12,
  sourceHash: "b".repeat(64),
  technicalMetadata: {
    durationSeconds: 12.5,
    formatName: "mov,mp4",
    hasAudio: true,
    hasVideo: true,
  },
};

describe("ingest completion contract", () => {
  it("accepts sanitized ingest results only for the ingestion transition", () => {
    expect(
      CompleteStepRequest.parse({
        expectedState: "INGESTING",
        ingestResult,
        jobVersion: 2,
        leaseToken: "t".repeat(43),
        state: "PROBING",
        step: "ingested",
      }),
    ).toMatchObject({ ingestResult });
  });

  it("rejects ingest metadata at any other workflow transition", () => {
    expect(() =>
      CompleteStepRequest.parse({
        expectedState: "QUEUED",
        ingestResult,
        jobVersion: 2,
        leaseToken: "t".repeat(43),
        state: "INGESTING",
        step: "ingest_started",
      }),
    ).toThrow();
  });
});
