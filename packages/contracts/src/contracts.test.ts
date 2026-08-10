import { describe, expect, it } from "vitest";
import {
  ApprovePublishRequest,
  ApproveScriptRequest,
  ClaimJobRequest,
  ClaimJobResponse,
  CompleteStepRequest,
  CreateImportRequest,
  HeartbeatRequest,
} from "./index.js";

const hash = "a".repeat(64);
const jobId = "00000000-0000-4000-8000-000000000001";

describe("wire contracts", () => {
  it("accepts HTTPS and OCI imports", () => {
    expect(CreateImportRequest.parse({ locator: { kind: "CREATOR_HTTPS_URL", url: "https://example.com/a.mp4" }, rightsEvidence: "license-1" })).toBeTruthy();
    expect(CreateImportRequest.parse({ locator: { kind: "OCI_INBOX_FILE", inboxFileId: "inbox-01" }, rightsEvidence: "license-1" })).toBeTruthy();
  });

  it("requires version-bound script approval hashes", () => {
    expect(ApproveScriptRequest.parse({ jobId, scriptVersion: 4, contentHash: hash })).toBeTruthy();
    expect(() => ApproveScriptRequest.parse({ jobId, scriptVersion: 4, contentHash: "abc" })).toThrow();
  });

  it("accepts publish metadata with nullable UTC schedule", () => {
    expect(ApprovePublishRequest.parse({
      jobId,
      renderId: "00000000-0000-4000-8000-000000000002",
      renderVersion: 2,
      metadataHash: hash,
      destinations: ["YOUTUBE"],
      scheduleAtUtc: null,
      metadata: [{ destination: "YOUTUBE", title: "A title", description: "Description", tags: ["animation"] }],
    })).toBeTruthy();
  });

  it("requires the claimed job version on every agent lease mutation", () => {
    const leaseToken = "l".repeat(32);
    expect(() =>
      ClaimJobResponse.parse({
        jobId,
        jobType: "LOCALIZE",
        leaseExpiresAtUtc: "2026-08-04T00:00:00Z",
        leaseToken,
        requestId: "00000000-0000-4000-8000-000000000003",
        state: "QUEUED",
      }),
    ).toThrow();
    expect(
      ClaimJobResponse.parse({
        jobId,
        jobType: "LOCALIZE",
        jobVersion: 1,
        leaseExpiresAtUtc: "2026-08-04T00:00:00Z",
        leaseToken,
        requestId: "00000000-0000-4000-8000-000000000003",
        state: "QUEUED",
      }),
    ).toBeTruthy();
    expect(() => HeartbeatRequest.parse({ leaseToken })).toThrow();
    expect(() => CompleteStepRequest.parse({ jobVersion: 1, leaseToken, state: "INGESTING", step: "ingest" })).toThrow();
  });

  it("binds an import claim and completion to sanitized ingest metadata", () => {
    const leaseToken = "l".repeat(32);
    expect(ClaimJobRequest.parse({ supportedJobTypes: ["IMPORT"], supportedStates: ["QUEUED", "INGESTING"] })).toEqual({
      supportedJobTypes: ["IMPORT"],
      supportedStates: ["QUEUED", "INGESTING"],
    });
    expect(
      ClaimJobResponse.parse({
        importInput: {
          locator: { kind: "OCI_INBOX_FILE", inboxFileId: "inbox-01" },
          rightsEvidenceHash: hash,
          sourceAssetId: "00000000-0000-4000-8000-000000000004",
        },
        jobId,
        jobType: "IMPORT",
        jobVersion: 1,
        leaseExpiresAtUtc: "2026-08-04T00:00:00Z",
        leaseToken,
        requestId: "00000000-0000-4000-8000-000000000003",
        state: "QUEUED",
      }),
    ).toBeTruthy();
    expect(() => ClaimJobResponse.parse({ jobId, jobType: "IMPORT", jobVersion: 1, leaseExpiresAtUtc: "2026-08-04T00:00:00Z", leaseToken, requestId: "00000000-0000-4000-8000-000000000003", state: "QUEUED" })).toThrow();
    expect(
      CompleteStepRequest.parse({
        expectedState: "INGESTING",
        ingestResult: {
          normalizedByteCount: 10,
          normalizedHash: hash,
          sourceByteCount: 12,
          sourceHash: "b".repeat(64),
          technicalMetadata: { durationSeconds: 12.5, formatName: "mov,mp4,m4a,3gp,3g2,mj2", hasAudio: true, hasVideo: true },
        },
        jobVersion: 2,
        leaseToken,
        state: "PROBING",
        step: "ingested",
      }),
    ).toBeTruthy();
  });
});
