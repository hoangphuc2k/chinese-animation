import { ApprovalsRepository } from "./approvals";
import { AuditEventsRepository } from "./audit-events";
import { DestinationPublicationsRepository } from "./destination-publications";
import { IdempotencyRepository } from "./idempotency";
import { JobEventsRepository } from "./job-events";
import { JobsRepository } from "./jobs";
import { ProjectsRepository } from "./projects";
import { RenderVersionsRepository } from "./render-versions";
import { ScriptVersionsRepository } from "./script-versions";
import type { SqlDatabase } from "./sql";
import { SourceAssetsRepository } from "./source-assets";

export interface Repositories {
  approvals: ApprovalsRepository;
  auditEvents: AuditEventsRepository;
  destinationPublications: DestinationPublicationsRepository;
  idempotency: IdempotencyRepository;
  jobEvents: JobEventsRepository;
  jobs: JobsRepository;
  projects: ProjectsRepository;
  renderVersions: RenderVersionsRepository;
  scriptVersions: ScriptVersionsRepository;
  sourceAssets: SourceAssetsRepository;
}

export function createRepositories(database: SqlDatabase): Repositories {
  return {
    approvals: new ApprovalsRepository(database),
    auditEvents: new AuditEventsRepository(database),
    destinationPublications: new DestinationPublicationsRepository(database),
    idempotency: new IdempotencyRepository(database),
    jobEvents: new JobEventsRepository(database),
    jobs: new JobsRepository(database),
    projects: new ProjectsRepository(database),
    renderVersions: new RenderVersionsRepository(database),
    scriptVersions: new ScriptVersionsRepository(database),
    sourceAssets: new SourceAssetsRepository(database),
  };
}
