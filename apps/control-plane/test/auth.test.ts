import { Hono } from "hono";
import { describe, expect, it, vi } from "vitest";

import {
  createAccessAuthenticator,
  createAgentAuthenticator,
  type AuthContext,
  type VerifiedIdentity,
} from "../src/middleware/access-auth";
import { Action, authorize, roleAllows } from "../src/middleware/authorize";
import { createCsrfProtector, createCsrfTokenService } from "../src/middleware/csrf";

const encoder = new TextEncoder();

function base64Url(value: string | ArrayBuffer): string {
  const bytes = typeof value === "string" ? encoder.encode(value) : new Uint8Array(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

async function accessFixture() {
  const keyPair = await crypto.subtle.generateKey(
    { hash: "SHA-256", name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]) },
    true,
    ["sign", "verify"],
  );
  const jwk = await crypto.subtle.exportKey("jwk", keyPair.publicKey);
  const now = 1_785_600_000;

  return {
    async token(claims: Record<string, unknown> = {}, kid = "access-key-1"): Promise<string> {
      const header = base64Url(JSON.stringify({ alg: "RS256", kid, typ: "JWT" }));
      const payload = base64Url(
        JSON.stringify({
          aud: ["control-plane-audience"],
          email: "admin@example.test",
          exp: now + 300,
          iss: "https://access.example.test",
          roles: ["admin"],
          sub: "user-admin-1",
          ...claims,
        }),
      );
      const signingInput = `${header}.${payload}`;
      const signature = await crypto.subtle.sign(
        { name: "RSASSA-PKCS1-v1_5" },
        keyPair.privateKey,
        encoder.encode(signingInput),
      );
      return `${signingInput}.${base64Url(signature)}`;
    },
    jwks: { keys: [{ ...jwk, alg: "RS256", kid: "access-key-1", use: "sig" }] },
    now,
  };
}

function identity(role: "operator" | "reviewer" | "admin" = "admin"): VerifiedIdentity {
  return {
    actorId: "user-admin-1",
    email: "admin@example.test",
    kind: "browser",
    role,
  };
}

function authApp(authenticate: ReturnType<typeof createAccessAuthenticator>) {
  const app = new Hono<{ Variables: AuthContext }>();
  app.use("*", authenticate);
  app.get("/protected", (context) => context.json(context.get("auth")));
  return app;
}

describe("Cloudflare Access authentication", () => {
  it.each([
    ["operator", "operator"],
    ["reviewer", "reviewer"],
    ["admin", "admin"],
  ] as const)("accepts a signed %s identity", async (role, expectedRole) => {
    const fixture = await accessFixture();
    const fetchJwks = vi.fn(async () =>
      new Response(JSON.stringify(fixture.jwks), { headers: { "cache-control": "max-age=60" } }),
    );
    const app = authApp(
      createAccessAuthenticator({
        allowedEmail: "admin@example.test",
        audience: "control-plane-audience",
        fetch: fetchJwks,
        issuer: "https://access.example.test",
        now: () => fixture.now,
      }),
    );

    const response = await app.request("https://control-plane.test/protected", {
      headers: { "cf-access-jwt-assertion": await fixture.token({ roles: [role] }) },
    });

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      actorId: "user-admin-1",
      email: "admin@example.test",
      kind: "browser",
      role: expectedRole,
    });
  });

  it.each([
    ["missing token", undefined],
    ["forged signature", "forged"],
    ["expired token", "expired"],
    ["wrong audience", "audience"],
    ["wrong issuer", "issuer"],
    ["unapproved email", "email"],
  ] as const)("rejects a %s", async (_name, scenario) => {
    const fixture = await accessFixture();
    const app = authApp(
      createAccessAuthenticator({
        allowedEmail: "admin@example.test",
        audience: "control-plane-audience",
        fetch: async () => new Response(JSON.stringify(fixture.jwks)),
        issuer: "https://access.example.test",
        now: () => fixture.now,
      }),
    );
    let token: string | undefined;
    if (scenario === "forged") {
      const [header, payload, signature] = (await fixture.token()).split(".");
      const replacement = signature!.startsWith("A") ? "B" : "A";
      token = `${header}.${payload}.${replacement}${signature!.slice(1)}`;
    }
    if (scenario === "expired") token = await fixture.token({ exp: fixture.now - 1 });
    if (scenario === "audience") token = await fixture.token({ aud: ["other-audience"] });
    if (scenario === "issuer") token = await fixture.token({ iss: "https://other-access.example.test" });
    if (scenario === "email") token = await fixture.token({ email: "unapproved@example.test" });

    const response = await app.request("https://control-plane.test/protected", {
      headers: token ? { "cf-access-jwt-assertion": token } : {},
    });

    expect(response.status).toBe(401);
  });

  it("uses a cached JWKS only until its HTTP max-age expires and fails closed for an unknown key", async () => {
    const fixture = await accessFixture();
    let now = fixture.now;
    const fetchJwks = vi.fn(async () =>
      new Response(JSON.stringify(fixture.jwks), { headers: { "cache-control": "public, max-age=60" } }),
    );
    const app = authApp(
      createAccessAuthenticator({
        allowedEmail: "admin@example.test",
        audience: "control-plane-audience",
        fetch: fetchJwks,
        issuer: "https://access.example.test",
        now: () => now,
      }),
    );

    const validHeaders = { "cf-access-jwt-assertion": await fixture.token() };
    expect((await app.request("https://control-plane.test/protected", { headers: validHeaders })).status).toBe(200);
    expect((await app.request("https://control-plane.test/protected", { headers: validHeaders })).status).toBe(200);
    expect(fetchJwks).toHaveBeenCalledTimes(1);

    now += 61;
    expect((await app.request("https://control-plane.test/protected", { headers: validHeaders })).status).toBe(200);
    expect(fetchJwks).toHaveBeenCalledTimes(2);
    expect(
      (await app.request("https://control-plane.test/protected", {
        headers: { "cf-access-jwt-assertion": await fixture.token({}, "unknown-key") },
      })).status,
    ).toBe(401);
  });
});

describe("authorization", () => {
  it("assigns the explicit action policy to every control-plane role", () => {
    expect(roleAllows("operator", Action.CreateImport)).toBe(true);
    expect(roleAllows("operator", Action.ApproveScript)).toBe(false);
    expect(roleAllows("reviewer", Action.ApproveScript)).toBe(true);
    expect(roleAllows("reviewer", Action.Render)).toBe(false);
    expect(roleAllows("admin", Action.AdminConfiguration)).toBe(true);
    expect(roleAllows("admin", Action.ApprovePublish)).toBe(true);
  });

  it("rejects a verified identity that lacks the required action", async () => {
    const app = new Hono<{ Variables: AuthContext }>();
    app.use("*", async (context, next) => {
      context.set("auth", identity("operator"));
      await next();
    });
    app.post("/publish", authorize(Action.ApprovePublish), (context) => context.json({ status: "approved" }));

    expect((await app.request("https://control-plane.test/publish", { method: "POST" })).status).toBe(403);
  });
});

describe("processing-agent authentication", () => {
  it("accepts only the configured service subject on the distinct agent audience", async () => {
    const fixture = await accessFixture();
    const app = new Hono<{ Variables: AuthContext }>();
    app.use(
      "*",
      createAgentAuthenticator({
        audience: "processing-agent-audience",
        fetch: async () => new Response(JSON.stringify(fixture.jwks)),
        issuer: "https://access.example.test",
        now: () => fixture.now,
        serviceSubject: "service-agent-1",
      }),
    );
    app.post("/step", (context) => context.json(context.get("auth")));

    const accepted = await app.request("https://control-plane.test/step", {
      method: "POST",
      headers: {
        "cf-access-jwt-assertion": await fixture.token({
          aud: ["processing-agent-audience"],
          sub: "service-agent-1",
        }),
      },
    });
    expect(accepted.status).toBe(200);
    await expect(accepted.json()).resolves.toMatchObject({ actorId: "service-agent-1", kind: "agent", role: "agent" });

    const rejected = await app.request("https://control-plane.test/step", {
      method: "POST",
      headers: {
        "cf-access-jwt-assertion": await fixture.token({
          aud: ["processing-agent-audience"],
          sub: "different-service",
        }),
      },
    });
    expect(rejected.status).toBe(401);
  });
});

describe("browser mutation CSRF protection", () => {
  it("requires an origin-matched token bound to the verified browser identity", async () => {
    const tokens = createCsrfTokenService({ now: () => 1_785_600_000, secret: "test-csrf-secret" });
    const app = new Hono<{ Variables: AuthContext }>();
    app.use("*", async (context, next) => {
      context.set("auth", identity());
      await next();
    });
    app.post("/mutate", createCsrfProtector({ origin: "https://control-plane.test", tokens }), (context) =>
      context.json({ status: "mutated" }),
    );
    const token = await tokens.issue("user-admin-1");

    expect(
      (
        await app.request("https://control-plane.test/mutate", {
          method: "POST",
          headers: { origin: "https://control-plane.test", "x-csrf-token": token },
        })
      ).status,
    ).toBe(200);
    expect(
      (
        await app.request("https://control-plane.test/mutate", {
          method: "POST",
          headers: { origin: "https://attacker.example.test", "x-csrf-token": token },
        })
      ).status,
    ).toBe(403);
    expect(
      (
        await app.request("https://control-plane.test/mutate", {
          method: "POST",
          headers: { origin: "https://control-plane.test" },
        })
      ).status,
    ).toBe(403);
  });

  it("exempts a separately authenticated agent identity without weakening browser protection", async () => {
    const tokens = createCsrfTokenService({ now: () => 1_785_600_000, secret: "test-csrf-secret" });
    const app = new Hono<{ Variables: AuthContext }>();
    app.use("*", async (context, next) => {
      context.set("auth", { actorId: "agent-1", kind: "agent", role: "agent" });
      await next();
    });
    app.post("/agent-step", createCsrfProtector({ origin: "https://control-plane.test", tokens }), (context) =>
      context.json({ status: "accepted" }),
    );

    expect((await app.request("https://control-plane.test/agent-step", { method: "POST" })).status).toBe(200);
  });
});
