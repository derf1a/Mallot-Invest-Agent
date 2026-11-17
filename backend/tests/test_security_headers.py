"""보안 머리 — iframe 막기 · https 로만 · MIME 추측 금지, 그리고 쓰기 요청의 출처는 Host 로만 본다 (X-Forwarded-Host 는 손님이 보낼 수 있다)."""


def test_security_headers(client):
    h = client.get("/api/health").headers
    assert h["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["strict-transport-security"].startswith("max-age=") and h["x-content-type-options"] == "nosniff"


def test_forwarded_host_cannot_fake_same_origin():
    from starlette.requests import Request

    from app.auth.guard import _same_origin

    def req(**h):
        return Request({"type": "http", "headers": [(k.replace("_", "-").encode(), v.encode()) for k, v in h.items()]})

    assert not _same_origin(req(host="example.com", origin="https://evil.example", x_forwarded_host="evil.example"))
    assert _same_origin(req(host="example.com", origin="https://example.com"))
