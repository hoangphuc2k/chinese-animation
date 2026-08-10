from run import authorize
def test_expiry_and_job_binding(): assert not authorize(0, 'j1', 'j1'); assert not authorize(9999999999, 'j1', 'j2')
