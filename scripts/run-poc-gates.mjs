#!/usr/bin/env node
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const allowed = new Set(['local-fixtures', 'access-lease', 'manual-ingest', 'oci-media', 'azure-speech', 'gemini', 'youtube', 'preview', 'facebook', 'source-adapters']);
const mandatory = new Set(['local-fixtures', 'access-lease', 'manual-ingest', 'oci-media', 'azure-speech', 'gemini', 'youtube', 'preview', 'facebook']);
const approvedFallbacks = new Map([
  ['facebook', 'manual publication after exact render and metadata approval'],
  ['source-adapters', 'manual ingest only'],
]);
const secret = /(token|secret|password|cookie|authorization|bearer|api[_-]?key)/i;
function assertResult(value, file) {
  for (const key of ['gate_id', 'timestamp', 'environment', 'fixture_hash', 'metrics', 'redactions', 'result', 'reason', 'fallback']) if (!(key in value)) throw new Error(`${file}: missing ${key}`);
  if (!allowed.has(value.gate_id)) throw new Error(`${file}: unknown gate_id`);
  if (!/^sha256:[a-f0-9]{64}$/.test(value.fixture_hash) || /^sha256:0{64}$/.test(value.fixture_hash)) throw new Error(`${file}: invalid or placeholder fixture_hash`);
  if (!['PASS', 'FAIL_FALLBACK', 'BLOCKED'].includes(value.result)) throw new Error(`${file}: invalid result`);
  const text = JSON.stringify(value);
  const forbiddenKey = Object.keys(value).find((key) => secret.test(key) && key !== 'redactions');
  if (forbiddenKey) throw new Error(`${file}: secret-like field ${forbiddenKey} is not permitted`);
  if (/(?:^|[" ])\/(?:Users|home|tmp|var)\//.test(text)) throw new Error(`${file}: absolute path found`);
  if (value.result === 'FAIL_FALLBACK' && !approvedFallbacks.has(value.gate_id)) throw new Error(`${file}: no approved fallback for ${value.gate_id}`);
  if (value.result === 'FAIL_FALLBACK' && !value.fallback.toLowerCase().includes(approvedFallbacks.get(value.gate_id))) throw new Error(`${file}: fallback is not design-approved`);
}
const dir = process.argv[2] ?? 'artifacts/pocs';
const files = existsSync(dir) ? readdirSync(dir).filter((f) => f.endsWith('.json')) : [];
const results = files.map((f) => { const value = JSON.parse(readFileSync(join(dir, f), 'utf8')); assertResult(value, f); if (f !== `${value.gate_id}.json`) throw new Error(`${f}: filename must be ${value.gate_id}.json`); return value; });
const ids = results.map((r) => r.gate_id);
if (new Set(ids).size !== ids.length) throw new Error('duplicate gate_id artifacts are not allowed');
const byId = new Map(results.map((r) => [r.gate_id, r]));
const missing = [...mandatory].filter((id) => !byId.has(id));
const failures = results.filter((r) => mandatory.has(r.gate_id) && r.result === 'BLOCKED');
console.log(JSON.stringify({ checked: results.length, missing, blocked: failures.map((r) => r.gate_id), status: missing.length || failures.length ? 'BLOCKED' : 'PASS' }, null, 2));
if (missing.length || failures.length) process.exitCode = 2;
