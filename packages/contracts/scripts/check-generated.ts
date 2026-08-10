import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const checks = [
  ["generated/openapi.json", '"version": "0.1.0"'],
  ["generated/typescript/api.d.ts", "DO NOT EDIT"],
  ["../../apps/processing-agent/src/localizer_agent/generated/models.py", "DO NOT EDIT"],
  ["generated/schema-manifest.json", '"contractVersion": "0.1.0"'],
] as const;
for (const [file, expected] of checks) {
  const value = await readFile(resolve(root, file), "utf8");
  if (!value.includes(expected)) throw new Error(`Generated contract check failed: ${file}`);
}
const openapi = JSON.parse(await readFile(resolve(root, "generated/openapi.json"), "utf8")) as {
  components?: { schemas?: Record<string, unknown> };
};
const required = ["ApiError", "CreateImportRequest", "ApproveScriptRequest", "ApprovePublishRequest", "ClaimJobRequest", "ClaimJobResponse", "CompleteStepRequest", "ImportInput", "IngestResult", "PipelineArtifact", "PipelineClaimInput", "TechnicalMetadata", "DashboardInboxResponse", "SegmentReviewResponse", "RenderReviewResponse", "SourceState", "ProcessingState", "DestinationState"];
for (const name of required) {
  if (!openapi.components?.schemas?.[name]) throw new Error(`Missing generated schema: ${name}`);
}
const requiredJobFields: Record<string, string[]> = {
  ClaimJobResponse: ["jobVersion"],
  CompleteStepRequest: ["jobVersion", "expectedState"],
  HeartbeatRequest: ["jobVersion"],
};
for (const [schemaName, fields] of Object.entries(requiredJobFields)) {
  const schema = openapi.components?.schemas?.[schemaName] as { required?: unknown } | undefined;
  const requiredFields = Array.isArray(schema?.required) ? schema.required : [];
  for (const field of fields) {
    if (!requiredFields.includes(field)) throw new Error(`Generated contract check failed: ${schemaName} is missing ${field}`);
  }
}
console.log("Generated contracts are current");
