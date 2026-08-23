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

  it("requires sanitized ingest metadata only for an import probing lease", () => {
    const base = {
      jobId: "00000000-0000-4000-8000-000000000001",
      jobType: "IMPORT",
      jobVersion: 3,
      leaseExpiresAtUtc: "2026-08-07T00:01:00Z",
      leaseToken,
      requestId: "00000000-0000-4000-8000-000000000099",
      state: "PROBING",
    };
    const ingestResult = {
      normalizedByteCount: 1024,
      normalizedHash: sha256,
      sourceByteCount: 2048,
      sourceHash: "b".repeat(64),
      technicalMetadata: { durationSeconds: 12.5, formatName: "mp4", hasAudio: true, hasVideo: true },
    };

    expect(ClaimJobResponse.safeParse(base).success).toBe(false);
    expect(ClaimJobResponse.safeParse({ ...base, ingestResult }).success).toBe(true);
    expect(ClaimJobResponse.safeParse({ ...base, ingestResult, state: "WAITING_SCRIPT_REVIEW" }).success).toBe(false);
  });

  it("accepts normalized-media identity for extraction and extracted audio for transcription", () => {
    const normalizedMedia = {
      artifactRef: "normalized-media",
      byteCount: 1024,
      kind: "NORMALIZED_MEDIA",
      sha256,
      toolVersion: "ffmpeg-normalize-v1",
    };
    expect(
      ClaimJobResponse.safeParse({
        jobId: "00000000-0000-4000-8000-000000000001",
        jobType: "IMPORT",
        jobVersion: 3,
        leaseExpiresAtUtc: "2026-08-07T00:01:00Z",
        leaseToken,
        pipelineInput: { normalizedMedia, state: "EXTRACTING_AUDIO" },
        requestId: "00000000-0000-4000-8000-000000000099",
        state: "EXTRACTING_AUDIO",
      }).success,
    ).toBe(true);
    const result = ClaimJobResponse.safeParse({
      jobId: "00000000-0000-4000-8000-000000000001",
      jobType: "IMPORT",
      jobVersion: 3,
      leaseExpiresAtUtc: "2026-08-07T00:01:00Z",
      leaseToken,
      pipelineInput: {
        extractedAudio: {
          artifactRef: "audio.wav",
          byteCount: 512,
          kind: "EXTRACTED_AUDIO",
          sha256,
          toolVersion: "ffmpeg-pcm16-v1",
        },
        state: "TRANSCRIBING",
      },
      requestId: "00000000-0000-4000-8000-000000000099",
      state: "TRANSCRIBING",
    });

    expect(result.success).toBe(true);
    if (result.success && result.data.pipelineInput?.state === "TRANSCRIBING") {
      expect(result.data.pipelineInput.extractedAudio.sha256).toBe(sha256);
      expect(JSON.stringify(result.data)).not.toContain("creator-submission");
    }
  });

  it("requires extracted-audio lineage when extraction advances to transcription", () => {
    const completion = {
      expectedState: "EXTRACTING_AUDIO",
      jobVersion: 3,
      leaseToken,
      pipelineArtifacts: [
        {
          artifactRef: "audio.wav",
          byteCount: 512,
          kind: "EXTRACTED_AUDIO",
          sha256,
          toolVersion: "ffmpeg-pcm16-v1",
        },
      ],
      state: "TRANSCRIBING",
      step: "audio_extracted",
    };

    expect(CompleteStepRequest.safeParse(completion).success).toBe(true);
    expect(
      CompleteStepRequest.safeParse({
        ...completion,
        pipelineArtifacts: [{ ...completion.pipelineArtifacts[0], kind: "TRANSCRIPT" }],
      }).success,
    ).toBe(false);
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

  it("accepts only an opaque render-bound preview descriptor for preview completion", () => {
    const completion = {
      expectedState: "READY_FOR_PREVIEW",
      jobVersion: 8,
      leaseToken,
      preview: {
        expiresAtUtc: "2026-08-23T08:05:00Z",
        renderSha256: sha256,
        token: "p".repeat(43),
      },
      state: "WAITING_PUBLISH_APPROVAL",
      step: "preview_registered",
    };

    expect(CompleteStepRequest.safeParse(completion).success).toBe(true);
    expect(
      CompleteStepRequest.safeParse({
        ...completion,
        preview: { ...completion.preview, relativePath: "renders/private.mp4" },
      }).success,
    ).toBe(false);
    expect(CompleteStepRequest.safeParse({ ...completion, expectedState: "RENDERING" }).success).toBe(false);
  });
});
