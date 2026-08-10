import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
describe('gate result contract', () => {
  it('requires auditable fields and constrained decisions', () => {
    const schema = JSON.parse(readFileSync('pocs/schema/gate-result.schema.json', 'utf8'));
    expect(schema.required).toEqual(expect.arrayContaining(['gate_id', 'timestamp', 'environment', 'fixture_hash', 'metrics', 'redactions', 'result', 'reason', 'fallback']));
    expect(schema.properties.result.enum).toEqual(['PASS', 'FAIL_FALLBACK', 'BLOCKED']);
    expect(schema.properties.gate_id.enum).not.toContain('arbitrary');
  });
});
