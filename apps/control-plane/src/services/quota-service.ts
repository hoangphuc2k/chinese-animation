interface QuotaStatement {
  bind(...values: unknown[]): QuotaStatement;
  first<Row extends Record<string, unknown>>(): Promise<Row | null>;
  run(): Promise<{ meta?: { changes?: number } }>;
}

export interface QuotaDatabase {
  prepare(sql: string): QuotaStatement;
}

export interface QuotaReservationInput {
  limitUnits: number;
  nowUtc: string;
  periodStartUtc: string;
  provider: string;
  quotaKey: string;
  requestedUnits: number;
}

export interface QuotaReservation {
  decision: "ALLOWED" | "WARNING" | "BLOCKED";
  usedUnits: number;
}

export interface RetryScheduleInput {
  jobId: string;
  nowUtc: string;
  retryAfter: string;
}

interface UsageRow extends Record<string, unknown> {
  used_units: number;
}

function assertPositiveInteger(value: number, name: string): void {
  if (!Number.isInteger(value) || value <= 0) throw new TypeError(`${name} must be a positive integer`);
}

function retryAtUtc(retryAfter: string, nowUtc: string): string {
  if (/^\d+$/.test(retryAfter)) {
    return new Date(Date.parse(nowUtc) + Number(retryAfter) * 1000).toISOString();
  }

  const retryAt = Date.parse(retryAfter);
  if (Number.isNaN(retryAt)) throw new TypeError("retryAfter must be seconds or an HTTP date");
  return new Date(retryAt).toISOString();
}

export class QuotaService {
  constructor(private readonly database: QuotaDatabase) {}

  async reserve(input: QuotaReservationInput): Promise<QuotaReservation> {
    assertPositiveInteger(input.limitUnits, "limitUnits");
    assertPositiveInteger(input.requestedUnits, "requestedUnits");

    const existing = await this.database
      .prepare("SELECT used_units FROM quota_usage WHERE provider = ? AND quota_key = ? AND period_start = ?")
      .bind(input.provider, input.quotaKey, input.periodStartUtc)
      .first<UsageRow>();
    const usedUnits = existing?.used_units ?? 0;
    const nextUsedUnits = usedUnits + input.requestedUnits;

    if (nextUsedUnits >= input.limitUnits * 0.95) {
      return { decision: "BLOCKED", usedUnits };
    }

    await this.database
      .prepare(
        "INSERT INTO quota_usage (provider, quota_key, period_start, used_units, limit_units, retry_after_at, updated_at) VALUES (?, ?, ?, ?, ?, NULL, ?) ON CONFLICT(provider, quota_key, period_start) DO UPDATE SET used_units = excluded.used_units, limit_units = excluded.limit_units, updated_at = excluded.updated_at",
      )
      .bind(
        input.provider,
        input.quotaKey,
        input.periodStartUtc,
        nextUsedUnits,
        input.limitUnits,
        input.nowUtc,
      )
      .run();

    return {
      decision: nextUsedUnits >= input.limitUnits * 0.8 ? "WARNING" : "ALLOWED",
      usedUnits: nextUsedUnits,
    };
  }

  async scheduleRetry(input: RetryScheduleInput): Promise<string> {
    const availableAtUtc = retryAtUtc(input.retryAfter, input.nowUtc);
    await this.database
      .prepare("UPDATE jobs SET available_at = ?, updated_at = ? WHERE id = ? AND status = ?")
      .bind(availableAtUtc, input.nowUtc, input.jobId, "QUEUED")
      .run();
    return availableAtUtc;
  }
}
