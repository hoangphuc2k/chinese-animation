from time import time
def authorize(expiry: float, requested_job: str, token_job: str) -> bool: return expiry > time() and requested_job == token_job
