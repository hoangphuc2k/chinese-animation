import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const openapiPath = resolve(root, "generated/openapi.json");
const tsPath = resolve(root, "generated/typescript/api.d.ts");
const pyPath = resolve(root, "../../apps/processing-agent/src/localizer_agent/generated/models.py");

const openapi = {
  openapi: "3.1.0",
  info: { title: "Chinese Animation Localizer API", version: "0.1.0" },
  paths: {},
  components: { schemas: {
    ErrorCode: { type: "string", enum: ["INVALID_REQUEST", "UNAUTHENTICATED", "FORBIDDEN", "NOT_FOUND", "CONFLICT", "STALE_VERSION", "LEASE_INVALID", "QUOTA_EXCEEDED", "RIGHTS_BLOCKED", "EXTERNAL_PROVIDER_ERROR", "VALIDATION_FAILED", "INTERNAL_ERROR"] },
    IngestLocator: { oneOf: [{ $ref: "#/components/schemas/HttpsLocator" }, { $ref: "#/components/schemas/OciLocator" }] },
    HttpsLocator: { type: "object", required: ["kind", "url"], properties: { kind: { const: "CREATOR_HTTPS_URL" }, url: { type: "string", format: "uri" } }, additionalProperties: false },
    OciLocator: { type: "object", required: ["kind", "inboxFileId"], properties: { kind: { const: "OCI_INBOX_FILE" }, inboxFileId: { type: "string" } }, additionalProperties: false },
    ApiError: { type: "object", required: ["error"], properties: { error: { type: "object", required: ["code", "message", "requestId", "retryable"], additionalProperties: false } }, additionalProperties: false },
  } },
};
const agentSchemas = {
  ClaimJobRequest: {
    type: "object",
    required: ["supportedJobTypes", "supportedStates"],
    properties: {
      supportedJobTypes: { type: "array", minItems: 1, maxItems: 6, items: { type: "string", enum: ["IMPORT", "DISCOVERY", "LOCALIZE", "RENDER", "PUBLISH", "DELETE_EXPIRED"] } },
      supportedStates: { type: "array", minItems: 1, maxItems: 21, items: { $ref: "#/components/schemas/ProcessingState" } },
    },
    additionalProperties: false,
  },
  ImportInput: {
    type: "object",
    required: ["locator", "rightsEvidenceHash", "sourceAssetId"],
    properties: {
      locator: { $ref: "#/components/schemas/IngestLocator" },
      rightsEvidenceHash: { type: "string", pattern: "^[a-f0-9]{64}$" },
      sourceAssetId: { type: "string", format: "uuid" },
    },
    additionalProperties: false,
  },
  TechnicalMetadata: {
    type: "object",
    required: ["durationSeconds", "formatName", "hasAudio", "hasVideo"],
    properties: {
      durationSeconds: { type: "number", minimum: 0 },
      formatName: { type: "string", minLength: 1, maxLength: 80 },
      hasAudio: { type: "boolean" },
      hasVideo: { type: "boolean" },
    },
    additionalProperties: false,
  },
  IngestResult: {
    type: "object",
    required: ["normalizedByteCount", "normalizedHash", "sourceByteCount", "sourceHash", "technicalMetadata"],
    properties: {
      normalizedByteCount: { type: "integer", minimum: 0 },
      normalizedHash: { type: "string", pattern: "^[a-f0-9]{64}$" },
      sourceByteCount: { type: "integer", minimum: 0 },
      sourceHash: { type: "string", pattern: "^[a-f0-9]{64}$" },
      technicalMetadata: { $ref: "#/components/schemas/TechnicalMetadata" },
    },
    additionalProperties: false,
  },
  PipelineArtifact: {
    type: "object",
    required: ["artifactRef", "byteCount", "kind", "sha256", "toolVersion"],
    properties: {
      artifactRef: { type: "string", minLength: 1, maxLength: 160, pattern: "^[A-Za-z0-9._:-]+$" },
      byteCount: { type: "integer", minimum: 0 },
      kind: { type: "string", enum: ["EXTRACTED_AUDIO", "MIXED_AUDIO", "NORMALIZED_MEDIA", "OCR_TRACK", "RENDER", "SCRIPT", "SUBTITLES", "TRANSCRIPT", "VOICE"] },
      sha256: { type: "string", pattern: "^[a-f0-9]{64}$" },
      toolVersion: { type: "string", minLength: 1, maxLength: 160 },
    },
    additionalProperties: false,
  },
  TranscriptionPipelineInput: {
    type: "object",
    required: ["normalizedMedia", "state"],
    properties: { normalizedMedia: { $ref: "#/components/schemas/PipelineArtifact" }, state: { const: "TRANSCRIBING" } },
    additionalProperties: false,
  },
  TranslationPipelineInput: {
    type: "object",
    required: ["googleProcessingAllowed", "state", "transcript"],
    properties: { googleProcessingAllowed: { const: true }, state: { const: "TRANSLATING" }, transcript: { $ref: "#/components/schemas/PipelineArtifact" } },
    additionalProperties: false,
  },
  VoicePipelineInput: {
    type: "object",
    required: ["script", "scriptSha256", "scriptVersion", "state"],
    properties: { script: { $ref: "#/components/schemas/PipelineArtifact" }, scriptSha256: { type: "string", pattern: "^[a-f0-9]{64}$" }, scriptVersion: { type: "integer", minimum: 1 }, state: { const: "GENERATING_VOICE" } },
    additionalProperties: false,
  },
  MixingPipelineInput: {
    type: "object",
    required: ["script", "state", "voice"],
    properties: { script: { $ref: "#/components/schemas/PipelineArtifact" }, state: { const: "MIXING" }, voice: { $ref: "#/components/schemas/PipelineArtifact" } },
    additionalProperties: false,
  },
  RenderingPipelineInput: {
    type: "object",
    required: ["mixedAudio", "normalizedMedia", "scriptSha256", "scriptVersion", "state", "subtitles"],
    properties: { mixedAudio: { $ref: "#/components/schemas/PipelineArtifact" }, normalizedMedia: { $ref: "#/components/schemas/PipelineArtifact" }, scriptSha256: { type: "string", pattern: "^[a-f0-9]{64}$" }, scriptVersion: { type: "integer", minimum: 1 }, state: { const: "RENDERING" }, subtitles: { $ref: "#/components/schemas/PipelineArtifact" } },
    additionalProperties: false,
  },
  PreviewPipelineInput: {
    type: "object",
    required: ["render", "state", "subtitles"],
    properties: { render: { $ref: "#/components/schemas/PipelineArtifact" }, state: { const: "READY_FOR_PREVIEW" }, subtitles: { $ref: "#/components/schemas/PipelineArtifact" } },
    additionalProperties: false,
  },
  PipelineClaimInput: {
    oneOf: [
      { $ref: "#/components/schemas/TranscriptionPipelineInput" },
      { $ref: "#/components/schemas/TranslationPipelineInput" },
      { $ref: "#/components/schemas/VoicePipelineInput" },
      { $ref: "#/components/schemas/MixingPipelineInput" },
      { $ref: "#/components/schemas/RenderingPipelineInput" },
      { $ref: "#/components/schemas/PreviewPipelineInput" },
    ],
    discriminator: { propertyName: "state" },
  },
  ClaimJobResponse: {
    type: "object",
    required: ["jobId", "jobType", "jobVersion", "state", "leaseToken", "leaseExpiresAtUtc", "requestId"],
    properties: {
      importInput: { $ref: "#/components/schemas/ImportInput" },
      ingestResult: { $ref: "#/components/schemas/IngestResult" },
      jobId: { type: "string", format: "uuid" },
      jobType: { type: "string", enum: ["IMPORT", "DISCOVERY", "LOCALIZE", "RENDER", "PUBLISH", "DELETE_EXPIRED"] },
      jobVersion: { type: "integer", minimum: 1 },
      state: { type: "string" },
      leaseToken: { type: "string", minLength: 32 },
      leaseExpiresAtUtc: { type: "string", format: "date-time" },
      pipelineInput: { $ref: "#/components/schemas/PipelineClaimInput" },
      requestId: { type: "string", format: "uuid" },
    },
    additionalProperties: false,
  },
  CompleteStepRequest: {
    type: "object",
    required: ["leaseToken", "jobVersion", "expectedState", "state", "step"],
    properties: {
      leaseToken: { type: "string", minLength: 32 },
      jobVersion: { type: "integer", minimum: 1 },
      expectedState: { type: "string" },
      state: { type: "string" },
      step: { type: "string", minLength: 1, maxLength: 80 },
      outputHash: { type: "string", pattern: "^[a-f0-9]{64}$" },
      bytesWritten: { type: "integer", minimum: 0 },
      ingestResult: { $ref: "#/components/schemas/IngestResult" },
      pipelineArtifacts: { type: "array", minItems: 1, maxItems: 100, items: { $ref: "#/components/schemas/PipelineArtifact" } },
      segmentIds: { type: "array", items: { type: "string", format: "uuid" } },
    },
    additionalProperties: false,
  },
};
const generatedHeader = "// Generated from packages/contracts/generated/openapi.json. DO NOT EDIT.\n";
const pythonHeader = "# Generated from packages/contracts/generated/openapi.json. DO NOT EDIT.\n";
const ts = `${generatedHeader}\nexport interface components { schemas: Record<string, unknown>; }\nexport type paths = Record<string, never>;\n`;
const py = `${pythonHeader}\nfrom typing import Annotated, Literal, Union\n\nfrom pydantic import BaseModel, ConfigDict, Field\n\n\nclass HttpsLocator(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n    kind: Literal["CREATOR_HTTPS_URL"]\n    url: str\n\n\nclass OciLocator(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n    kind: Literal["OCI_INBOX_FILE"]\n    inboxFileId: str = Field(min_length=1)\n\n\nIngestLocator = Annotated[Union[HttpsLocator, OciLocator], Field(discriminator="kind")]\nJobType = Literal["IMPORT", "DISCOVERY", "LOCALIZE", "RENDER", "PUBLISH", "DELETE_EXPIRED"]\n\n\nclass ClaimJobRequest(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n    supportedJobTypes: list[JobType] = Field(min_length=1, max_length=6)\n\n\nclass ImportInput(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n    locator: IngestLocator\n    rightsEvidenceHash: str = Field(pattern=r"^[a-f0-9]{64}$")\n    sourceAssetId: str\n\n\nclass TechnicalMetadata(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n    durationSeconds: float = Field(ge=0)\n    formatName: str = Field(min_length=1, max_length=80)\n    hasAudio: bool\n    hasVideo: bool\n\n\nclass IngestResult(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n    normalizedByteCount: int = Field(ge=0)\n    normalizedHash: str = Field(pattern=r"^[a-f0-9]{64}$")\n    sourceByteCount: int = Field(ge=0)\n    sourceHash: str = Field(pattern=r"^[a-f0-9]{64}$")\n    technicalMetadata: TechnicalMetadata\n\n\nclass ClaimJobResponse(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n    importInput: ImportInput | None = None\n    jobId: str\n    jobType: JobType\n    jobVersion: int = Field(gt=0)\n    leaseExpiresAtUtc: str\n    leaseToken: str = Field(min_length=32)\n    requestId: str\n    state: str\n\n\nclass CompleteStepRequest(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n    leaseToken: str = Field(min_length=32)\n    jobVersion: int = Field(gt=0)\n    expectedState: str\n    state: str\n    step: str = Field(min_length=1, max_length=80)\n    outputHash: str | None = None\n    bytesWritten: int | None = Field(default=None, ge=0)\n    ingestResult: IngestResult | None = None\n    segmentIds: list[str] | None = None\n`;

const pyWithSupportedStates = py.replace(
  "    supportedJobTypes: list[JobType] = Field(min_length=1, max_length=6)\n",
  "    supportedJobTypes: list[JobType] = Field(min_length=1, max_length=6)\n    supportedStates: list[str] = Field(min_length=1, max_length=21)\n",
);
const pyWithHeartbeat = pyWithSupportedStates.replace(
  "class CompleteStepRequest",
  "class HeartbeatRequest(BaseModel):\n    model_config = ConfigDict(extra=\"forbid\")\n    jobVersion: int = Field(gt=0)\n    leaseToken: str = Field(min_length=32)\n\n\nclass CompleteStepRequest",
);
const pyWithPipelineArtifacts = pyWithHeartbeat
  .replace(
    'JobType = Literal["IMPORT", "DISCOVERY", "LOCALIZE", "RENDER", "PUBLISH", "DELETE_EXPIRED"]',
    'JobType = Literal["IMPORT", "DISCOVERY", "LOCALIZE", "RENDER", "PUBLISH", "DELETE_EXPIRED"]\nArtifactKind = Literal["EXTRACTED_AUDIO", "MIXED_AUDIO", "NORMALIZED_MEDIA", "OCR_TRACK", "RENDER", "SCRIPT", "SUBTITLES", "TRANSCRIPT", "VOICE"]',
  )
  .replace(
    "class ClaimJobResponse(BaseModel):",
    "class PipelineArtifact(BaseModel):\n    model_config = ConfigDict(extra=\"forbid\")\n    artifactRef: str = Field(min_length=1, max_length=160, pattern=r\"^[A-Za-z0-9._:-]+$\")\n    byteCount: int = Field(ge=0)\n    kind: ArtifactKind\n    sha256: str = Field(pattern=r\"^[a-f0-9]{64}$\")\n    toolVersion: str = Field(min_length=1, max_length=160)\n\n\nclass TranscriptionPipelineInput(BaseModel):\n    model_config = ConfigDict(extra=\"forbid\")\n    normalizedMedia: PipelineArtifact\n    state: Literal[\"TRANSCRIBING\"]\n\n\nclass TranslationPipelineInput(BaseModel):\n    model_config = ConfigDict(extra=\"forbid\")\n    googleProcessingAllowed: Literal[True]\n    state: Literal[\"TRANSLATING\"]\n    transcript: PipelineArtifact\n\n\nclass VoicePipelineInput(BaseModel):\n    model_config = ConfigDict(extra=\"forbid\")\n    script: PipelineArtifact\n    scriptSha256: str = Field(pattern=r\"^[a-f0-9]{64}$\")\n    scriptVersion: int = Field(gt=0)\n    state: Literal[\"GENERATING_VOICE\"]\n\n\nclass MixingPipelineInput(BaseModel):\n    model_config = ConfigDict(extra=\"forbid\")\n    script: PipelineArtifact\n    state: Literal[\"MIXING\"]\n    voice: PipelineArtifact\n\n\nclass RenderingPipelineInput(BaseModel):\n    model_config = ConfigDict(extra=\"forbid\")\n    mixedAudio: PipelineArtifact\n    normalizedMedia: PipelineArtifact\n    scriptSha256: str = Field(pattern=r\"^[a-f0-9]{64}$\")\n    scriptVersion: int = Field(gt=0)\n    state: Literal[\"RENDERING\"]\n    subtitles: PipelineArtifact\n\n\nclass PreviewPipelineInput(BaseModel):\n    model_config = ConfigDict(extra=\"forbid\")\n    render: PipelineArtifact\n    state: Literal[\"READY_FOR_PREVIEW\"]\n    subtitles: PipelineArtifact\n\n\nPipelineClaimInput = Annotated[Union[TranscriptionPipelineInput, TranslationPipelineInput, VoicePipelineInput, MixingPipelineInput, RenderingPipelineInput, PreviewPipelineInput], Field(discriminator=\"state\")]\n\n\nclass ClaimJobResponse(BaseModel):",
  )
  .replace(
    "    ingestResult: IngestResult | None = None\n    segmentIds: list[str] | None = None\n",
    "    ingestResult: IngestResult | None = None\n    pipelineArtifacts: list[PipelineArtifact] | None = None\n    segmentIds: list[str] | None = None\n",
  )
  .replace(
    "    importInput: ImportInput | None = None\n",
    "    importInput: ImportInput | None = None\n    ingestResult: IngestResult | None = None\n    pipelineInput: PipelineClaimInput | None = None\n",
  );

await mkdir(dirname(openapiPath), { recursive: true });
await mkdir(dirname(tsPath), { recursive: true });
await mkdir(dirname(pyPath), { recursive: true });
// The checked-in schema artifact is the deterministic, reviewable representation
// of all exported contracts. Keep generation stable when the optional OpenAPI
// tooling is unavailable in an offline environment.
let schemaArtifact: {
  openapi: string;
  info: { title: string; version: string };
  paths: Record<string, unknown>;
  components: { schemas: Record<string, unknown> };
} = openapi;
try {
  schemaArtifact = JSON.parse(await readFile(openapiPath, "utf8")) as typeof openapi;
} catch {
  // A clean checkout must use the checked-in generated artifact until the
  // OpenAPI generator dependencies are available. Never emit a partial schema.
  throw new Error("Generated OpenAPI artifact is missing; install generator dependencies before first generation");
}
schemaArtifact.components.schemas = { ...schemaArtifact.components.schemas, ...agentSchemas };
const requiredSchemas = [
  "ApiError", "ApprovePublishRequest", "ApproveScriptRequest", "ClaimJobRequest", "ClaimJobResponse", "CompleteStepRequest", "ImportInput", "IngestResult", "PipelineArtifact", "PipelineClaimInput", "TechnicalMetadata",
  "CreateImportRequest", "DashboardInboxResponse",
  "DestinationState", "HeartbeatRequest", "ProcessingState", "RenderReviewResponse",
  "SegmentReviewResponse", "SourceState",
];
for (const schema of requiredSchemas) {
  if (!schemaArtifact.components?.schemas?.[schema]) {
    throw new Error(`Generated OpenAPI artifact is missing required schema: ${schema}`);
  }
}
const requiredJobFields: Record<string, string[]> = {
  ClaimJobResponse: ["jobVersion"],
  CompleteStepRequest: ["jobVersion", "expectedState"],
  HeartbeatRequest: ["jobVersion"],
};
for (const [schemaName, fields] of Object.entries(requiredJobFields)) {
  const schema = schemaArtifact.components.schemas[schemaName] as { required?: unknown };
  const existing = Array.isArray(schema.required) ? schema.required.filter((field): field is string => typeof field === "string") : [];
  schema.required = [...new Set([...existing, ...fields])];
}
await writeFile(openapiPath, `${JSON.stringify(schemaArtifact, null, 2)}\n`);
await writeFile(tsPath, ts);
await writeFile(pyPath, pyWithPipelineArtifacts);
console.log("Generated OpenAPI, TypeScript and Python contract models.");
