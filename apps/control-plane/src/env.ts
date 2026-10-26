export interface AgentWorkerBindings {
  AGENT_ACCESS_AUDIENCE: string;
  AGENT_SERVICE_CLIENT_ID: string;
}

export interface BrowserWorkerBindings {
  ADMIN_EMAIL: string;
  ADMIN_ORIGIN: string;
  CSRF_SIGNING_SECRET: string;
}

export type WorkerEnv = Env & AgentWorkerBindings & BrowserWorkerBindings;
