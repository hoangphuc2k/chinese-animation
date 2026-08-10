ALTER TABLE source_assets ADD COLUMN rights_revoked_at TEXT;

CREATE UNIQUE INDEX idx_jobs_one_delete_expired_command
  ON jobs(source_asset_id)
  WHERE job_type = 'DELETE_EXPIRED';

CREATE INDEX idx_source_assets_rights_revoked_at
  ON source_assets(rights_revoked_at)
  WHERE rights_revoked_at IS NOT NULL;

CREATE TABLE quota_usage (
  provider TEXT NOT NULL,
  quota_key TEXT NOT NULL,
  period_start TEXT NOT NULL,
  used_units INTEGER NOT NULL CHECK (used_units >= 0),
  limit_units INTEGER NOT NULL CHECK (limit_units > 0),
  retry_after_at TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (provider, quota_key, period_start)
);

CREATE INDEX idx_quota_usage_retry_after
  ON quota_usage(retry_after_at)
  WHERE retry_after_at IS NOT NULL;
