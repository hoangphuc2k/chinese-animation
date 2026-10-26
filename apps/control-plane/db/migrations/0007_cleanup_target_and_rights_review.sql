-- Cleanup commands target one job. Keying them by source asset blocked cleanup
-- of a re-imported asset's later jobs.
ALTER TABLE jobs ADD COLUMN target_job_id TEXT REFERENCES jobs(id);

DROP INDEX idx_jobs_one_delete_expired_command;

CREATE UNIQUE INDEX idx_jobs_one_delete_expired_per_target
  ON jobs(target_job_id)
  WHERE job_type = 'DELETE_EXPIRED';

-- At most one in-flight import per source asset; re-import is allowed once the
-- previous job is terminal.
CREATE UNIQUE INDEX idx_jobs_one_active_import_per_asset
  ON jobs(source_asset_id)
  WHERE job_type = 'IMPORT'
    AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED', 'RIGHTS_BLOCKED');

-- Revocation never alters a published destination; it flags it for manual review.
ALTER TABLE destination_publications ADD COLUMN rights_review_required_at TEXT;
