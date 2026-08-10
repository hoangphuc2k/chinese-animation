from run import upload_boundary
def test_private_upload_is_not_claimed_without_credentials(): assert upload_boundary().startswith('BLOCKED')
