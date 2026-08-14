import { describe, expect, it } from "vitest";

import { ClaimJobRequest, ClaimJobResponse, CompleteStepRequest } from "./jobs.js";

const leaseToken = "t".repeat(43);
const sha256 = "a".repeat(64);

describe("pipeline artifact completion contracts", () => {
  it("requires agents to declare the processing states they implement", () => {
    expect(ClaimJobRequest.safeParse({ supportedJobTypes: ["IMPORT"] }).success).toBe(false);
    expect(
      ClaimJobRequest.safeParse({ supportedJobTypes: ["IMPORT"], supportedStates: ["INGESTING", "TRANSCRIBING"] }).success,
    ).toBe(true);
  });
  it("requires sanitized normalized-media input for a transcription lease", () => {
    const result = ClaimJobResponse.safeParse({
      importInput: {
        locator: { inboxFileId: "creator-submission-42", kind: "OCI_INBOX_FILE" },
        rightsEvidenceHash: sha256,
        sourceAssetId: "00000000-0000-4000-8000-000000000002",
      },
      jobId: "00000000-0000-4000-8000-000000000001",
      jobType: "IMPORT",
      jobVersion: 3,
      leaseExpiresAtUtc: "2026-08-07T00:01:00Z",
      leaseToken,
      requestId: "00000000-0000-4000-8000-000000000099",
      state: "TRANSCRIBING",
    });

    expect(result.success).toBe(false);
  });

  it("accepts only opaque normalized-media identity for a transcription lease", () => {
    const result = ClaimJobResponse.safeParse({
      jobId: "00000000-0000-4000-8000-000000000001",
      jobType: "IMPORT",
      jobVersion: 3,
      leaseExpiresAtUtc: "2026-08-07T00:01:00Z",
      leaseToken,
      pipelineInput: {
        normalizedMedia: {
          artifactRef: "normalized-media",
          byteCount: 1024,
          kind: "NORMALIZED_MEDIA",
          sha256,
          toolVersion: "ffmpeg-normalize-v1",
        },
        state: "TRANSCRIBING",
      },
      requestId: "00000000-0000-4000-8000-000000000099",
      state: "TRANSCRIBING",
    });

    expect(result.success).toBe(true);
    if (result.success && result.data.pipelineInput?.state === "TRANSCRIBING") {
      expect(result.data.pipelineInput.normalizedMedia.sha256).toBe(sha256);
      expect(JSON.stringify(result.data)).not.toContain("creator-submission");
    }
  });

  it("requires a transcript and explicit Google permission for a translation lease", () => {
    const base = {
      jobId: "00000000-0000-4000-8000-000000000001",
      jobType: "LOCALIZE",
      jobVersion: 4,
      leaseExpiresAtUtc: "2026-08-07T00:01:00Z",
      leaseToken,
      requestId: "00000000-0000-4000-8000-000000000099",
      state: "TRANSLATING",
    };
    const transcript = {
      artifactRef: "transcript-01.json",
      byteCount: 512,
      kind: "TRANSCRIPT",
      sha256,
      toolVersion: "whisper.cpp-1.7.4",
    };

    expect(ClaimJobResponse.safeParse(base).success).toBe(false);
    expect(
      ClaimJobResponse.safeParse({
        ...base,
        pipelineInput: { googleProcessingAllowed: true, state: "TRANSLATING", transcript },
      }).success,
    ).toBe(true);
    expect(
      ClaimJobResponse.safeParse({
        ...base,
        pipelineInput: { googleProcessingAllowed: false, state: "TRANSLATING", transcript },
      }).success,
    ).toBe(false);
  });

  it("requires exact script identity for a voice-generation lease", () => {
    const result = ClaimJobResponse.safeParse({
      jobId: "00000000-0000-4000-8000-000000000001",
      jobType: "LOCALIZE",
      jobVersion: 5,
      leaseExpiresAtUtc: "2026-08-07T00:01:00Z",
      leaseToken,
      pipelineInput: {
        script: {
          artifactRef: "script-01.json",
          byteCount: 768,
          kind: "SCRIPT",
          sha256,
          toolVersion: "gemini-2.5-flash",
        },
        scriptSha256: sha256,
        scriptVersion: 2,
        state: "GENERATING_VOICE",
      },
      requestId: "00000000-0000-4000-8000-000000000099",
      state: "GENERATING_VOICE",
    });

    expect(result.success).toBe(true);
  });

  it("accepts a transcript artifact when transcription advances to translation", () => {
    const result = CompleteStepRequest.safeParse({
      expectedState: "TRANSCRIBING",
      jobVersion: 2,
      leaseToken,
      pipelineArtifacts: [
        {
          artifactRef: "artifact-01",
          byteCount: 512,
          kind: "TRANSCRIPT",
          sha256,
          toolVersion: "whisper.cpp-1.7.4",
        },
      ],
      state: "TRANSLATING",
      step: "transcribed",
    });

    expect(result.success).toBe(true);
  });

  it("rejects a transcript artifact for an ingest completion", () => {
    const result = CompleteStepRequest.safeParse({
      expectedState: "INGESTING",
      jobVersion: 2,
      leaseToken,
      pipelineArtifacts: [
        {
          artifactRef: "artifact-01",
          byteCount: 512,
          kind: "TRANSCRIPT",
          sha256,
          toolVersion: "whisper.cpp-1.7.4",
        },
      ],
      state: "PROBING",
      step: "ingested",
    });

    expect(result.success).toBe(false);
  });
});
