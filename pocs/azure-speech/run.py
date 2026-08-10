"""Credential-free Azure contract probe; live calls require OCI Vault."""
def classify(status: int) -> str:
    return 'retryable' if status in (429, 500, 502, 503, 504) else ('terminal' if status in (401, 403, 400) else 'unknown')
