# OCI media envelope

Run `benchmark.sh` on the OCI ARM64 test host. It must capture tool versions,
codec, elapsed time, peak RSS, peak disk, output hash and failure stage for
15/30/60-minute fixtures. A tier passes only at <=12 hours, <10 GB RSS and
>=20 GB free disk; otherwise production import remains disabled.
