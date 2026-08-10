# Generated from packages/contracts/generated/openapi.json. DO NOT EDIT.

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class HttpsLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["CREATOR_HTTPS_URL"]
    url: str


class OciLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["OCI_INBOX_FILE"]
    inboxFileId: str = Field(min_length=1)


IngestLocator = Annotated[Union[HttpsLocator, OciLocator], Field(discriminator="kind")]
JobType = Literal["IMPORT", "DISCOVERY", "LOCALIZE", "RENDER", "PUBLISH", "DELETE_EXPIRED"]
ArtifactKind = Literal["EXTRACTED_AUDIO", "MIXED_AUDIO", "NORMALIZED_MEDIA", "OCR_TRACK", "RENDER", "SCRIPT", "SUBTITLES", "TRANSCRIPT", "VOICE"]


class ClaimJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supportedJobTypes: list[JobType] = Field(min_length=1, max_length=6)
    supportedStates: list[str] = Field(min_length=1, max_length=21)


class ImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locator: IngestLocator
    rightsEvidenceHash: str = Field(pattern=r"^[a-f0-9]{64}$")
    sourceAssetId: str


class TechnicalMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    durationSeconds: float = Field(ge=0)
    formatName: str = Field(min_length=1, max_length=80)
    hasAudio: bool
    hasVideo: bool


class IngestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    normalizedByteCount: int = Field(ge=0)
    normalizedHash: str = Field(pattern=r"^[a-f0-9]{64}$")
    sourceByteCount: int = Field(ge=0)
    sourceHash: str = Field(pattern=r"^[a-f0-9]{64}$")
    technicalMetadata: TechnicalMetadata


class PipelineArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifactRef: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")
    byteCount: int = Field(ge=0)
    kind: ArtifactKind
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    toolVersion: str = Field(min_length=1, max_length=160)


class PipelineClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    normalizedMedia: PipelineArtifact


class ClaimJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    importInput: ImportInput | None = None
    ingestResult: IngestResult | None = None
    pipelineInput: PipelineClaimInput | None = None
    jobId: str
    jobType: JobType
    jobVersion: int = Field(gt=0)
    leaseExpiresAtUtc: str
    leaseToken: str = Field(min_length=32)
    requestId: str
    state: str


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jobVersion: int = Field(gt=0)
    leaseToken: str = Field(min_length=32)


class CompleteStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    leaseToken: str = Field(min_length=32)
    jobVersion: int = Field(gt=0)
    expectedState: str
    state: str
    step: str = Field(min_length=1, max_length=80)
    outputHash: str | None = None
    bytesWritten: int | None = Field(default=None, ge=0)
    ingestResult: IngestResult | None = None
    pipelineArtifacts: list[PipelineArtifact] | None = None
    segmentIds: list[str] | None = None
