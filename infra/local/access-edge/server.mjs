// Local stand-in for the Cloudflare Access edge. It mints the same JWT claim
// shapes Access issues and forwards requests to the Worker with
// Cf-Access-Jwt-Assertion, so the Worker's real verification code runs.
//
// Identity rules (local only):
// - CF-Access-Client-Id/Secret must match the configured service token, and
//   produce a service-token JWT (empty `sub`, Client ID in `common_name`).
// - Agent routes without a valid service token are refused, like an Access
//   application with a Service Auth policy.
// - Every other request is treated as the logged-in administrator.
import { createSign, generateKeyPairSync, randomUUID } from "node:crypto";
import { createServer as createHttpServer, request as upstreamRequest } from "node:http";
import { createServer as createHttpsServer } from "node:https";
import { readFileSync } from "node:fs";

const env = process.env;
const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const kid = randomUUID();
const jwks = { keys: [{ ...publicKey.export({ format: "jwk" }), alg: "RS256", kid, use: "sig" }] };
const upstream = new URL(env.UPSTREAM);

function mint(claims) {
  const now = Math.floor(Date.now() / 1000);
  const header = Buffer.from(JSON.stringify({ alg: "RS256", kid, typ: "JWT" })).toString("base64url");
  const payload = Buffer.from(
    JSON.stringify({ exp: now + 300, iat: now, iss: env.ACCESS_ISSUER, nbf: now, type: "app", ...claims }),
  ).toString("base64url");
  const signature = createSign("RSA-SHA256").update(`${header}.${payload}`).sign(privateKey).toString("base64url");
  return `${header}.${payload}.${signature}`;
}

function deny(response, status) {
  response.writeHead(status, { "content-type": "text/plain" });
  response.end(status === 403 ? "Forbidden by local Access edge\n" : "Error\n");
}

function identityFor(request) {
  const clientId = request.headers["cf-access-client-id"];
  const clientSecret = request.headers["cf-access-client-secret"];
  if (clientId !== undefined || clientSecret !== undefined) {
    if (clientId !== env.AGENT_CLIENT_ID || clientSecret !== env.AGENT_CLIENT_SECRET) return undefined;
    return { aud: [env.AGENT_AUDIENCE], common_name: clientId, sub: "" };
  }
  if (request.url.startsWith("/api/v1/agent/")) return undefined;
  return { aud: [env.ADMIN_AUDIENCE], email: env.ADMIN_EMAIL, sub: "local-admin" };
}

const edge = createHttpsServer(
  { cert: readFileSync("/certs/server.pem"), key: readFileSync("/certs/server-key.pem") },
  (request, response) => {
    const claims = identityFor(request);
    if (!claims) return deny(response, 403);
    const headers = { ...request.headers };
    for (const name of ["cf-access-client-id", "cf-access-client-secret", "cf-access-jwt-assertion"]) delete headers[name];
    headers["cf-access-jwt-assertion"] = mint(claims);
    const forwarded = upstreamRequest(
      { headers, hostname: upstream.hostname, method: request.method, path: request.url, port: upstream.port },
      (upstreamResponse) => {
        response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
        upstreamResponse.pipe(response);
      },
    );
    forwarded.on("error", () => deny(response, 502));
    request.pipe(forwarded);
  },
);

const keys = createHttpServer((request, response) => {
  if (request.method === "GET" && request.url === "/cdn-cgi/access/certs") {
    response.writeHead(200, { "cache-control": "max-age=60", "content-type": "application/json" });
    return response.end(JSON.stringify(jwks));
  }
  deny(response, 404);
});

edge.listen(443, () => console.log("local Access edge listening on :443"));
keys.listen(9000, () => console.log("local Access JWKS listening on :9000"));
