export interface StructuredLogFields {
  durationMs?: number;
  errorCode?: string;
  jobId?: string;
  projectId?: string;
  requestId?: string;
  sourceTitleSensitive?: boolean;
  stage?: string;
  [key: string]: unknown;
}

export interface StructuredLogger {
  error(fields: StructuredLogFields): void;
  info(fields: StructuredLogFields): void;
  warn(fields: StructuredLogFields): void;
}

export interface StructuredLoggerOptions {
  nowUtc?: () => string;
  write?: (entry: string) => void;
}

const sensitiveKey = /(authorization|cookie|credential|header|password|secret|token|url)/i;
const redact = "[REDACTED]";

function sanitize(value: unknown, sourceTitleSensitive: boolean, key?: string): unknown {
  if (key === "sourceTitleSensitive") return undefined;
  if (key === "sourceTitle" && sourceTitleSensitive) return redact;
  if (key !== undefined && sensitiveKey.test(key)) return redact;
  if (Array.isArray(value)) return value.map((item) => sanitize(item, sourceTitleSensitive));
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).flatMap(([nestedKey, nestedValue]) => {
        const sanitized = sanitize(nestedValue, sourceTitleSensitive, nestedKey);
        return sanitized === undefined ? [] : [[nestedKey, sanitized]];
      }),
    );
  }
  return value;
}

export function createStructuredLogger(options: StructuredLoggerOptions = {}): StructuredLogger {
  const nowUtc = options.nowUtc ?? (() => new Date().toISOString());
  const write = options.write ?? ((entry: string) => console.log(entry));

  function log(level: "ERROR" | "INFO" | "WARN", fields: StructuredLogFields): void {
    const sanitized = sanitize(fields, fields.sourceTitleSensitive ?? false) as Record<string, unknown>;
    write(JSON.stringify({ level, atUtc: nowUtc(), ...sanitized }));
  }

  return {
    error: (fields) => log("ERROR", fields),
    info: (fields) => log("INFO", fields),
    warn: (fields) => log("WARN", fields),
  };
}
