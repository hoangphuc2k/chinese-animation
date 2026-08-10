from collect import Benchmark, classify
def test_envelope_thresholds(): assert classify(Benchmark(900, 3600, 4096, 25, 'sha256:x', None)) == 'PASS'
def test_slow_tier_falls_back(): assert classify(Benchmark(900, 43201, 4096, 25, None, 'transcode')) == 'FAIL_FALLBACK'
