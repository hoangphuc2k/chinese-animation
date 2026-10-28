-- One active cleanup per target job. A finished, failed or cancelled cleanup must not block a
-- later retention cycle (the job was retried, or the earlier cleanup failed and is rescheduled).
DROP INDEX idx_jobs_one_delete_expired_per_target;

CREATE UNIQUE INDEX idx_jobs_one_active_cleanup_per_target
  ON jobs(target_job_id)
  WHERE job_type = 'DELETE_EXPIRED'
    AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED');
