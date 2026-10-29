import { describe, expect, it } from "vitest";

import { CreateImportRequest } from "./ingest.js";
import { ClaimJobResponse, CompleteStepRequest } from "./jobs.js";

const leaseToken = "t".repeat(43);
const locator = { kind: "CREATOR_HTTPS_URL", url: "https://creator.example/video.mp4" };

describe("AI-processor consent on import", () => {
  it("defaults to no consent so omitted processors never reach Gemini", () => {
    const parsed = CreateImportRequest.parse({ locator, rightsEvidence: "Signed licence 2026-10" });

    expect(parsed.allowedProcessors).toEqual([]);
  });

  it("accepts named processors and rejects duplicates or unknown providers", () => {
    expect(
      CreateImportRequest.safeParse({ allowedProcessors: ["GOOGLE_GEMINI"], locator, rightsEvidence: "licence" }).success,
    ).toBe(true);
    expect(
      CreateImportRequest.safeParse({
        allowedProcessors: ["GOOGLE_GEMINI", "GOOGLE_GEMINI"],
        locator,
        rightsEvidence: "licence",
      }).success,
    ).toBe(false);
    expect(
      CreateImportRequest.safeParse({ allowedProcessors: ["OPENAI"], locator, rightsEvidence: "licence" }).success,
    ).toBe(false);
  });
});

describe("cleanup claims", () => {
  const base = {
    jobId: "00000000-0000-4000-8000-000000000010",
    jobVersion: 2,
    leaseExpiresAtUtc: "2026-08-07T00:01:00Z",
    leaseToken,
    requestId: "00000000-0000-4000-8000-000000000099",
    state: "QUEUED",
  };

  it("requires the cleanup target only for DELETE_EXPIRED claims", () => {
    const cleanupInput = { targetJobId: "00000000-0000-4000-8000-000000000001" };

    expect(ClaimJobResponse.safeParse({ ...base, cleanupInput, jobType: "DELETE_EXPIRED" }).success).toBe(true);
    expect(ClaimJobResponse.safeParse({ ...base, jobType: "DELETE_EXPIRED" }).success).toBe(false);
    expect(
      ClaimJobResponse.safeParse({
        ...base,
        cleanupInput,
        importInput: {
          locator,
          rightsEvidenceHash: "a".repeat(64),
          sourceAssetId: "00000000-0000-4000-8000-000000000002",
        },
        jobType: "IMPORT",
      }).success,
    ).toBe(false);
  });
});

describe("failure codes", () => {
  const base = { expectedState: "MIXING", jobVersion: 4, leaseToken, step: "subtitle_rules_failed" };

  it("are accepted only on failure or manual-review completions", () => {
    expect(
      CompleteStepRequest.safeParse({ ...base, failureCode: "MEDIA_INVALID", state: "MANUAL_REVIEW_REQUIRED" }).success,
    ).toBe(true);
    expect(CompleteStepRequest.safeParse({ ...base, failureCode: "MEDIA_INVALID", state: "FAILED" }).success).toBe(true);
    expect(CompleteStepRequest.safeParse({ ...base, failureCode: "MEDIA_INVALID", state: "RENDERING" }).success).toBe(false);
  });
});
