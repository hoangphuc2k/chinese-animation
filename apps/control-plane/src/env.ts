export interface AgentWorkerBindings {
  AGENT_ACCESS_AUDIENCE: string;
  /** Access service-token Client ID accepted on agent routes. */
  AGENT_SERVICE_CLIENT_ID: string;
}

export interface BrowserWorkerBindings {
  ADMIN_EMAIL: string;
  /** Origin the administrator UI is served from; browser mutations must come from it. */
  ADMIN_ORIGIN: string;
  CSRF_SIGNING_SECRET: string;
}

export type WorkerEnv = Env & AgentWorkerBindings & BrowserWorkerBindings;
