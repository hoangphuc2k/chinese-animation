from run import classify
def test_retryable_provider_errors(): assert classify(429) == 'retryable'
def test_auth_is_terminal(): assert classify(401) == 'terminal'
