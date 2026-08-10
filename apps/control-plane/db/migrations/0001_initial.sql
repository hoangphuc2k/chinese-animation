PRAGMA foreign_keys = ON;

CREATE TABLE projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE source_assets (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  source_provider TEXT NOT NULL,
  external_id TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
  rights_metadata_json TEXT NOT NULL,
  rights_expires_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (source_provider, external_id)
);

CREATE TABLE jobs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  source_asset_id TEXT NOT NULL REFERENCES source_assets(id),
  job_type TEXT NOT NULL,
  status TEXT NOT NULL,
  step TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  available_at TEXT NOT NULL,
  lease_owner TEXT,
  lease_token_hash TEXT,
  lease_until TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  retention_deadline TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE job_events (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  event_type TEXT NOT NULL,
  previous_state TEXT,
  next_state TEXT,
  details_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE script_versions (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  version INTEGER NOT NULL CHECK (version > 0),
  content_hash TEXT NOT NULL,
  transcript_hash TEXT,
  translation_hash TEXT,
  metadata_hash TEXT,
  voice_settings_hash TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (job_id, version)
);

CREATE TABLE render_versions (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  version INTEGER NOT NULL CHECK (version > 0),
  render_hash TEXT NOT NULL,
  byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
  artifact_ref TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (job_id, version)
);

CREATE TABLE destination_publications (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  destination TEXT NOT NULL,
  provider_asset_id TEXT,
  provider_session_id TEXT,
  render_version_id TEXT REFERENCES render_versions(id),
  status TEXT NOT NULL,
  metadata_hash TEXT NOT NULL,
  scheduled_at_utc TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (job_id, destination),
  UNIQUE (destination, provider_asset_id)
);

CREATE TABLE approvals (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  approval_type TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version > 0),
  content_hash TEXT,
  metadata_hash TEXT,
  destinations_json TEXT,
  schedule_at_utc TEXT,
  actor_id TEXT NOT NULL,
  approved_at TEXT NOT NULL,
  invalidated_at TEXT,
  invalidation_reason TEXT
);

CREATE TABLE idempotency_records (
  id TEXT PRIMARY KEY,
  actor_id TEXT NOT NULL,
  operation TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  response_status INTEGER NOT NULL,
  response_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (actor_id, operation, idempotency_key)
);

CREATE TABLE audit_events (
  id TEXT PRIMARY KEY,
  actor_id TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  operation TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  target_version INTEGER,
  target_hash TEXT,
  request_id TEXT NOT NULL,
  previous_state TEXT,
  next_state TEXT,
  details_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX idx_source_assets_project_activity
  ON source_assets(project_id, created_at DESC);

CREATE INDEX idx_jobs_runnable
  ON jobs(status, available_at, lease_until, priority DESC, created_at);

CREATE INDEX idx_jobs_expiring_leases
  ON jobs(lease_until)
  WHERE lease_until IS NOT NULL;

CREATE INDEX idx_jobs_project_activity
  ON jobs(project_id, updated_at DESC);

CREATE INDEX idx_jobs_retention_deadline
  ON jobs(retention_deadline)
  WHERE retention_deadline IS NOT NULL;

CREATE INDEX idx_job_events_job_created_at
  ON job_events(job_id, created_at DESC);

CREATE INDEX idx_destination_publications_job
  ON destination_publications(job_id, updated_at DESC);

CREATE INDEX idx_approvals_job_type
  ON approvals(job_id, approval_type, invalidated_at);

CREATE UNIQUE INDEX idx_approvals_active_script
  ON approvals(job_id, version)
  WHERE approval_type = 'SCRIPT_APPROVED' AND invalidated_at IS NULL;

CREATE UNIQUE INDEX idx_approvals_active_publish
  ON approvals(job_id, version, metadata_hash)
  WHERE approval_type = 'PUBLISH_APPROVED' AND invalidated_at IS NULL;

CREATE INDEX idx_audit_events_target_created_at
  ON audit_events(target_type, target_id, created_at DESC);

CREATE INDEX idx_audit_events_created_at
  ON audit_events(created_at);
