def decision(hidden_stage_supported: bool) -> str:
    return 'PASS' if hidden_stage_supported else 'FAIL_FALLBACK'
