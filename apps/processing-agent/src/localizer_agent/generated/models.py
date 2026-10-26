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


class ExtractionPipelineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    normalizedMedia: PipelineArtifact
    state: Literal["EXTRACTING_AUDIO"]


class TranscriptionPipelineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extractedAudio: PipelineArtifact
    state: Literal["TRANSCRIBING"]


class TranslationPipelineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    googleProcessingAllowed: Literal[True]
    state: Literal["TRANSLATING"]
    transcript: PipelineArtifact


class VoicePipelineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script: PipelineArtifact
    scriptSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scriptVersion: int = Field(gt=0)
    state: Literal["GENERATING_VOICE"]


class MixingPipelineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script: PipelineArtifact
    state: Literal["MIXING"]
    voice: PipelineArtifact


class RenderingPipelineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mixedAudio: PipelineArtifact
    normalizedMedia: PipelineArtifact
    scriptSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scriptVersion: int = Field(gt=0)
    state: Literal["RENDERING"]
    subtitles: PipelineArtifact


class PreviewPipelineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    render: PipelineArtifact
    state: Literal["READY_FOR_PREVIEW"]
    subtitles: PipelineArtifact


PipelineClaimInput = Annotated[Union[ExtractionPipelineInput, TranscriptionPipelineInput, TranslationPipelineInput, VoicePipelineInput, MixingPipelineInput, RenderingPipelineInput, PreviewPipelineInput], Field(discriminator="state")]


class CleanupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    targetJobId: str


FailureCode = Literal["RETRYABLE_NETWORK", "RATE_LIMITED", "AUTH_REQUIRED", "RIGHTS_BLOCKED", "QUOTA_EXHAUSTED", "INVALID_AI_RESPONSE", "MEDIA_INVALID", "DISK_PRESSURE", "MANUAL_REVIEW_REQUIRED"]


class ClaimJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cleanupInput: CleanupInput | None = None
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


class PreviewDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expiresAtUtc: str
    renderSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    token: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class CompleteStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    leaseToken: str = Field(min_length=32)
    jobVersion: int = Field(gt=0)
    expectedState: str
    failureCode: FailureCode | None = None
    state: str
    step: str = Field(min_length=1, max_length=80)
    outputHash: str | None = None
    bytesWritten: int | None = Field(default=None, ge=0)
    ingestResult: IngestResult | None = None
    pipelineArtifacts: list[PipelineArtifact] | None = None
    preview: PreviewDescriptor | None = None
    segmentIds: list[str] | None = None
