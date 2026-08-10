import type { MiddlewareHandler } from "hono";

import type { AccessRole, AuthContext } from "./access-auth";

export enum Action {
  CreateImport = "CREATE_IMPORT",
  EditScript = "EDIT_SCRIPT",
  ApproveScript = "APPROVE_SCRIPT",
  Render = "RENDER",
  ApprovePublish = "APPROVE_PUBLISH",
  RetryCancel = "RETRY_CANCEL",
  AdminConfiguration = "ADMIN_CONFIGURATION",
}

const allowedActions: Record<AccessRole, ReadonlySet<Action>> = {
  admin: new Set(Object.values(Action)),
  operator: new Set([Action.CreateImport, Action.EditScript, Action.Render, Action.RetryCancel]),
  reviewer: new Set([Action.ApproveScript, Action.ApprovePublish]),
};

function forbiddenResponse(): Response {
  return new Response(JSON.stringify({ error: { code: "FORBIDDEN", message: "The verified identity lacks this action." } }), {
    headers: { "content-type": "application/problem+json" },
    status: 403,
  });
}

export function roleAllows(role: AccessRole, action: Action): boolean {
  return allowedActions[role].has(action);
}

export function authorize(action: Action): MiddlewareHandler<{ Variables: AuthContext }> {
  return async (context, next) => {
    const identity = context.get("auth");
    if (!identity || identity.kind !== "browser" || !roleAllows(identity.role, action)) return forbiddenResponse();
    await next();
  };
}
