from run import decision
def test_no_hidden_stage_uses_approval_fallback(): assert decision(False) == 'FAIL_FALLBACK'
def test_hidden_stage_requires_explicit_probe(): assert decision(True) == 'PASS'
