import { describe, expect, it } from 'vitest';
import { claim, idempotent } from './worker';
describe('access and atomic lease semantics', () => {
  it('permits one claimant and reclaims expired lease', () => {
    const first = claim(undefined, 10, { jobId: 'j1', leaseTokenHash: 'h1', expiresAt: 20 });
    expect(first).toBeDefined();
    expect(claim(first, 10, { jobId: 'j1', leaseTokenHash: 'h2', expiresAt: 20 })).toBeUndefined();
    expect(claim(first, 21, { jobId: 'j1', leaseTokenHash: 'h2', expiresAt: 30 })).toBeDefined();
  });
  it('replays duplicate idempotency keys', () => { const m = new Map<string, string>(); expect(idempotent(m, 'k', 'a')).toBe('a'); expect(idempotent(m, 'k', 'b')).toBe('a'); });
});
