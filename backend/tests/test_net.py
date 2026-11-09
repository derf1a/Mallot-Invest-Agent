import pytest

from app.core.net import http_request, is_public_url


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("http://127.0.0.1:8000/api/health", False),
        ("http://192.168.0.1/admin", False),
        ("http://10.0.0.5/", False),
        ("http://169.254.169.254/latest/meta-data", False),
        ("http://[::1]/", False),
        ("ftp://8.8.8.8/file", False),
        ("javascript:alert(1)", False),
        ("https://8.8.8.8/", True),
    ],
)
def test_is_public_url(url, ok):
    assert is_public_url(url) is ok


def test_http_blocks_internal_addresses_without_connecting():
    assert http_request("GET", "http://127.0.0.1:8000/api/health") == (0, b"blocked: non-public address")
