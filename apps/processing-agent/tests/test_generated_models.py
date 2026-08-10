from localizer_agent.generated.models import ClaimJobRequest, ClaimJobResponse, CompleteStepRequest, HttpsLocator, OciLocator


def test_generated_models_parse_contract_fixtures() -> None:
    assert HttpsLocator(kind="CREATOR_HTTPS_URL", url="https://example.com/a.mp4").kind == "CREATOR_HTTPS_URL"
    assert OciLocator(kind="OCI_INBOX_FILE", inboxFileId="inbox-01").inboxFileId == "inbox-01"


def test_generated_models_reject_unknown_fields() -> None:
    try:
        HttpsLocator(kind="CREATOR_HTTPS_URL", url="https://example.com/a.mp4", extra="bad")
    except Exception:
        return
    raise AssertionError("unknown fields must be rejected")


def test_generated_models_bind_import_claims_and_sanitized_ingest_results() -> None:
    claim = ClaimJobRequest(supportedJobTypes=["IMPORT"], supportedStates=["QUEUED", "INGESTING"])
    assert claim.supportedJobTypes == ["IMPORT"]

    response = ClaimJobResponse(
        importInput={
            "locator": {"inboxFileId": "creator-submission-42", "kind": "OCI_INBOX_FILE"},
            "rightsEvidenceHash": "a" * 64,
            "sourceAssetId": "00000000-0000-4000-8000-000000000002",
        },
        jobId="00000000-0000-4000-8000-000000000001",
        jobType="IMPORT",
        jobVersion=2,
        leaseExpiresAtUtc="2026-08-04T00:05:00Z",
        leaseToken="t" * 43,
        requestId="00000000-0000-4000-8000-000000000099",
        state="QUEUED",
    )
    assert response.importInput is not None
    assert response.importInput.rightsEvidenceHash == "a" * 64

    completion = CompleteStepRequest(
        expectedState="INGESTING",
        ingestResult={
            "normalizedByteCount": 10,
            "normalizedHash": "a" * 64,
            "sourceByteCount": 12,
            "sourceHash": "b" * 64,
            "technicalMetadata": {"durationSeconds": 12.5, "formatName": "mov,mp4", "hasAudio": True, "hasVideo": True},
        },
        jobVersion=2,
        leaseToken="t" * 43,
        state="PROBING",
        step="ingested",
    )
    assert completion.ingestResult is not None
    assert completion.ingestResult.normalizedHash == "a" * 64
