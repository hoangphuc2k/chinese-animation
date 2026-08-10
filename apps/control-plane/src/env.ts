export interface AgentWorkerBindings {
  AGENT_ACCESS_AUDIENCE: string;
  AGENT_SERVICE_SUBJECT: string;
}

export interface BrowserWorkerBindings {
  ADMIN_EMAIL: string;
  CSRF_SIGNING_SECRET: string;
}

export type WorkerEnv = Env & AgentWorkerBindings & BrowserWorkerBindings;
