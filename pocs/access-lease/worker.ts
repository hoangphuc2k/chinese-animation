export type Claim = { jobId: string; leaseTokenHash: string; expiresAt: number };
export function claim(current: Claim | undefined, now: number, next: Claim): Claim | undefined {
  if (current && current.expiresAt >= now) return undefined;
  return next;
}
export function idempotent<T>(seen: Map<string, T>, key: string, value: T): T { if (seen.has(key)) return seen.get(key)!; seen.set(key, value); return value; }
