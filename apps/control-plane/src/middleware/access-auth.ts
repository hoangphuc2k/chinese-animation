import type { MiddlewareHandler } from "hono";

export type AccessRole = "operator" | "reviewer" | "admin";

export interface BrowserIdentity {
  actorId: string;
  email: string;
  kind: "browser";
  role: AccessRole;
}

export interface AgentIdentity {
  actorId: string;
  kind: "agent";
  role: "agent";
}

export type VerifiedIdentity = BrowserIdentity | AgentIdentity;

export interface AuthContext {
  auth: VerifiedIdentity;
}

interface AccessJwtClaims {
  aud?: unknown;
  email?: unknown;
  exp?: unknown;
  iss?: unknown;
  roles?: unknown;
  sub?: unknown;
}

interface JwksResponse {
  keys?: unknown;
}

interface CachedJwks {
  expiresAtSeconds: number;
  keys: AccessJwk[];
}

type AccessJwk = JsonWebKey & { kid?: string };

interface CommonAuthOptions {
  audience: string;
  fetch?: typeof globalThis.fetch;
  issuer: string;
  jwksUrl?: string;
  now?: () => number;
}

export interface AccessAuthOptions extends CommonAuthOptions {
  allowedEmail: string;
}

export interface AgentAuthOptions extends CommonAuthOptions {
  serviceSubject: string;
}

interface ParsedJwt {
  claims: AccessJwtClaims;
  header: { alg?: unknown; kid?: unknown };
  signature: Uint8Array;
  signingInput: string;
}

class AuthenticationError extends Error {}

function unauthorizedResponse(): Response {
  return new Response(JSON.stringify({ error: { code: "UNAUTHENTICATED", message: "Authentication is required." } }), {
    headers: { "content-type": "application/problem+json" },
    status: 401,
  });
}

function base64UrlDecode(value: string): Uint8Array {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) throw new AuthenticationError("JWT segment is malformed");
  const padded = value.replaceAll("-", "+").replaceAll("_", "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function decodeJson(value: string): Record<string, unknown> {
  try {
    const decoded = new TextDecoder().decode(base64UrlDecode(value));
    const parsed: unknown = JSON.parse(decoded);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new AuthenticationError("JWT object is malformed");
    return parsed as Record<string, unknown>;
  } catch (error) {
    if (error instanceof AuthenticationError) throw error;
    throw new AuthenticationError("JWT JSON is malformed");
  }
}

function parseJwt(token: string): ParsedJwt {
  const segments = token.split(".");
  if (segments.length !== 3) throw new AuthenticationError("JWT must contain three segments");
  const [encodedHeader, encodedClaims, encodedSignature] = segments;
  if (!encodedHeader || !encodedClaims || !encodedSignature) throw new AuthenticationError("JWT contains an empty segment");
  const header = decodeJson(encodedHeader);
  if (header.alg !== "RS256" || typeof header.kid !== "string" || !header.kid) {
    throw new AuthenticationError("JWT algorithm or key ID is invalid");
  }
  return {
    claims: decodeJson(encodedClaims),
    header,
    signature: base64UrlDecode(encodedSignature),
    signingInput: `${encodedHeader}.${encodedClaims}`,
  };
}

function cacheMaxAgeSeconds(value: string | null): number {
  const match = value?.match(/(?:^|,)\s*max-age=(\d+)\s*(?:,|$)/i);
  return match ? Number.parseInt(match[1]!, 10) : 0;
}

function audienceMatches(value: unknown, expected: string): boolean {
  return value === expected || (Array.isArray(value) && value.every((item) => typeof item === "string") && value.includes(expected));
}

function requiredString(value: unknown, name: string): string {
  if (typeof value !== "string" || !value) throw new AuthenticationError(`${name} claim is missing`);
  return value;
}

function roleFromClaims(value: unknown): AccessRole {
  if (!Array.isArray(value) || !value.every((role) => typeof role === "string")) {
    throw new AuthenticationError("roles claim is missing");
  }
  if (value.includes("admin")) return "admin";
  if (value.includes("reviewer")) return "reviewer";
  if (value.includes("operator")) return "operator";
  throw new AuthenticationError("roles claim has no authorized role");
}

function defaultJwksUrl(issuer: string): string {
  return new URL("/cdn-cgi/access/certs", issuer).toString();
}

class JwksVerifier {
  private cached: CachedJwks | undefined;

  constructor(private readonly options: CommonAuthOptions) {}

  async verify(token: string): Promise<AccessJwtClaims> {
    const parsed = parseJwt(token);
    const claims = parsed.claims;
    const now = this.now();
    if (claims.iss !== this.options.issuer || !audienceMatches(claims.aud, this.options.audience)) {
      throw new AuthenticationError("JWT issuer or audience does not match");
    }
    if (typeof claims.exp !== "number" || !Number.isFinite(claims.exp) || claims.exp <= now) {
      throw new AuthenticationError("JWT is expired");
    }
    const key = (await this.jwks()).find((candidate) => candidate.kid === parsed.header.kid && candidate.kty === "RSA");
    if (!key) throw new AuthenticationError("JWT key ID is unknown");
    if (key.alg && key.alg !== "RS256") throw new AuthenticationError("JWKS key algorithm is invalid");
    if (key.use && key.use !== "sig") throw new AuthenticationError("JWKS key use is invalid");
    const publicKey = await crypto.subtle.importKey(
      "jwk",
      key,
      { hash: "SHA-256", name: "RSASSA-PKCS1-v1_5" },
      false,
      ["verify"],
    );
    const valid = await crypto.subtle.verify(
      { name: "RSASSA-PKCS1-v1_5" },
      publicKey,
      parsed.signature,
      new TextEncoder().encode(parsed.signingInput),
    );
    if (!valid) throw new AuthenticationError("JWT signature is invalid");
    return claims;
  }

  private async jwks(): Promise<AccessJwk[]> {
    const now = this.now();
    if (this.cached && now < this.cached.expiresAtSeconds) return this.cached.keys;
    const response = await (this.options.fetch ?? globalThis.fetch)(this.options.jwksUrl ?? defaultJwksUrl(this.options.issuer));
    if (!response.ok) throw new AuthenticationError("JWKS endpoint was unavailable");
    let body: JwksResponse;
    try {
      body = (await response.json()) as JwksResponse;
    } catch {
      throw new AuthenticationError("JWKS response was invalid");
    }
    if (!Array.isArray(body.keys) || !body.keys.every((key) => key && typeof key === "object")) {
      throw new AuthenticationError("JWKS response has no keys");
    }
    const keys = body.keys as AccessJwk[];
    this.cached = { expiresAtSeconds: now + cacheMaxAgeSeconds(response.headers.get("cache-control")), keys };
    return keys;
  }

  private now(): number {
    return this.options.now?.() ?? Math.floor(Date.now() / 1000);
  }
}

function tokenFromRequest(request: Request): string {
  const token = request.headers.get("cf-access-jwt-assertion");
  if (!token) throw new AuthenticationError("Access JWT is missing");
  return token;
}

export function createAccessAuthenticator(options: AccessAuthOptions): MiddlewareHandler<{ Variables: AuthContext }> {
  const verifier = new JwksVerifier(options);
  return async (context, next) => {
    try {
      const claims = await verifier.verify(tokenFromRequest(context.req.raw));
      const email = requiredString(claims.email, "email");
      if (email !== options.allowedEmail) throw new AuthenticationError("email is not allowed");
      context.set("auth", {
        actorId: requiredString(claims.sub, "sub"),
        email,
        kind: "browser",
        role: roleFromClaims(claims.roles),
      });
      await next();
    } catch {
      return unauthorizedResponse();
    }
  };
}

export function createAgentAuthenticator(options: AgentAuthOptions): MiddlewareHandler<{ Variables: AuthContext }> {
  const verifier = new JwksVerifier(options);
  return async (context, next) => {
    try {
      const claims = await verifier.verify(tokenFromRequest(context.req.raw));
      const actorId = requiredString(claims.sub, "sub");
      if (actorId !== options.serviceSubject) throw new AuthenticationError("service subject does not match");
      context.set("auth", { actorId, kind: "agent", role: "agent" });
      await next();
    } catch {
      return unauthorizedResponse();
    }
  };
}
