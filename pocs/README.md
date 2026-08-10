# Risk-gate proof-of-concepts

Each directory is an isolated PoC. Results are redacted JSON files under
`artifacts/pocs/` and use only `PASS`, `FAIL_FALLBACK`, or `BLOCKED`.
Credentials must come from OCI Vault/instance principal at runtime; they are
never accepted from command-line arguments or committed files.

Run the offline checks without network access:

```sh
node scripts/run-poc-gates.mjs artifacts/pocs
python3 -m pytest pocs --collect-only
```

Provider gates intentionally report `BLOCKED` when credentials/tools are not
present. `FAIL_FALLBACK` is valid only when the fallback is the one specified
in the design (for example Facebook post-approval/manual publication).

Synthetic fixture hashes are manifest placeholders until media is generated on
the benchmark host. Replace them with SHA-256 values in a local, uncommitted
manifest before running media benchmarks.
