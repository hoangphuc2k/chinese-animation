import type { MiddlewareHandler } from "hono";

import type { AuthContext } from "./access-auth";

interface CsrfTokenPayload {
  actorId: string;
  exp: number;
}

export interface CsrfTokenService {
  issue(actorId: string): Promise<string>;
  verify(token: string, actorId: string): Promise<boolean>;
}

export interface CsrfTokenServiceOptions {
  expiresInSeconds?: number;
  now?: () => number;
  secret: string;
}

export interface CsrfProtectorOptions {
  origin: string;
  tokens: CsrfTokenService;
}

function base64UrlEncode(value: string | Uint8Array): string {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : value;
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function base64UrlDecode(value: string): Uint8Array | undefined {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) return undefined;
  try {
    const padded = value.replaceAll("-", "+").replaceAll("_", "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
    const binary = atob(padded);
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
  } catch {
    return undefined;
  }
}

function equalBytes(left: Uint8Array, right: Uint8Array): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) difference |= left[index]! ^ right[index]!;
  return difference === 0;
}

function parsePayload(value: string): CsrfTokenPayload | undefined {
  const bytes = base64UrlDecode(value);
  if (!bytes) return undefined;
  try {
    const parsed: unknown = JSON.parse(new TextDecoder().decode(bytes));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return undefined;
    const { actorId, exp } = parsed as Record<string, unknown>;
    return typeof actorId === "string" && actorId && typeof exp === "number" && Number.isFinite(exp)
      ? { actorId, exp }
      : undefined;
  } catch {
    return undefined;
  }
}

function forbiddenResponse(): Response {
  return new Response(JSON.stringify({ error: { code: "FORBIDDEN", message: "CSRF validation failed." } }), {
    headers: { "content-type": "application/problem+json" },
    status: 403,
  });
}

export function createCsrfTokenService(options: CsrfTokenServiceOptions): CsrfTokenService {
  const now = options.now ?? (() => Math.floor(Date.now() / 1000));
  const expiresInSeconds = options.expiresInSeconds ?? 600;
  const keyPromise = crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(options.secret),
    { hash: "SHA-256", name: "HMAC" },
    false,
    ["sign"],
  );

  async function signature(payload: string): Promise<Uint8Array> {
    const value = await crypto.subtle.sign("HMAC", await keyPromise, new TextEncoder().encode(payload));
    return new Uint8Array(value);
  }

  return {
    async issue(actorId: string): Promise<string> {
      const payload = base64UrlEncode(JSON.stringify({ actorId, exp: now() + expiresInSeconds }));
      return `${payload}.${base64UrlEncode(await signature(payload))}`;
    },
    async verify(token: string, actorId: string): Promise<boolean> {
      const [encodedPayload, encodedSignature, extra] = token.split(".");
      if (!encodedPayload || !encodedSignature || extra) return false;
      const suppliedSignature = base64UrlDecode(encodedSignature);
      const payload = parsePayload(encodedPayload);
      if (!suppliedSignature || !payload || payload.actorId !== actorId || payload.exp <= now()) return false;
      return equalBytes(suppliedSignature, await signature(encodedPayload));
    },
  };
}

export function createCsrfProtector(options: CsrfProtectorOptions): MiddlewareHandler<{ Variables: AuthContext }> {
  return async (context, next) => {
    if (["GET", "HEAD", "OPTIONS"].includes(context.req.method)) return next();
    const identity = context.get("auth");
    if (identity?.kind === "agent") return next();
    const token = context.req.header("x-csrf-token");
    if (!identity || context.req.header("origin") !== options.origin || !token || !(await options.tokens.verify(token, identity.actorId))) {
      return forbiddenResponse();
    }
    await next();
  };
}
