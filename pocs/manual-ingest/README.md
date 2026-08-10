# Manual ingest PoC

The runtime implementation must enforce HTTPS-only downloads, DNS and redirect
rechecks, byte/time limits, streaming SHA-256, and `ffprobe` validation. OCI
inbox mode scans only `/mnt/localizer/inbox`, ignores symlinks and `.partial`
files, waits for two identical size scans, then emits an opaque source ID.
Network, Bastion/SCP, and ffprobe checks require the OCI test host and are not
claimed by the offline tests.
