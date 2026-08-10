"""Offline validation for the two guaranteed ingest boundaries."""
from __future__ import annotations
import hashlib, ipaddress, urllib.parse
from pathlib import Path

def validate_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname:
        raise ValueError("only credential-free HTTPS URLs are accepted")
    address = ipaddress.ip_address(parsed.hostname) if parsed.hostname.replace(".", "").isdigit() else None
    if address and (address.is_private or address.is_loopback or address.is_link_local):
        raise ValueError("private or loopback address")

def stable_source_id(path: Path) -> str:
    if path.is_symlink() or path.name.endswith(".partial"):
        raise ValueError("unstable inbox entry")
    return "src_" + hashlib.sha256(path.read_bytes()).hexdigest()[:24]

if __name__ == "__main__":
    print("manual-ingest offline checks available; network and ffprobe are runtime gates")
