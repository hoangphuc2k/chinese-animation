import pytest
from pathlib import Path
from run import validate_url, stable_source_id

@pytest.mark.parametrize("url", ["http://example.com/a", "https://u:p@example.com/a", "https://127.0.0.1/a"])
def test_rejects_unsafe_urls(url):
    with pytest.raises(ValueError): validate_url(url)

def test_accepts_https(): validate_url("https://example.com/video.mp4")

def test_opaque_id_and_unstable_files(tmp_path: Path):
    p = tmp_path / "video.mp4"; p.write_bytes(b"fixture")
    assert stable_source_id(p).startswith("src_")
    partial = tmp_path / "video.partial"; partial.write_bytes(b"x")
    with pytest.raises(ValueError): stable_source_id(partial)
