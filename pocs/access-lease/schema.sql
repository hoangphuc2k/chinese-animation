CREATE TABLE jobs (id TEXT PRIMARY KEY, lease_token_hash TEXT, lease_expires_at INTEGER);
-- Production claim must be one conditional UPDATE ... WHERE lease_expires_at < now RETURNING.
