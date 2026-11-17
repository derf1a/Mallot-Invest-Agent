"""경로 접두사 MIA_BASE_PATH — 떼기 · base 로 오면 redirect · 접두사 없는 요청은 그대로."""

import asyncio

from app.auth import security
from app.core.base_path import BasePathMiddleware, base_path


def test_base_path_is_normalized(monkeypatch):
    for raw, want in (("", ""), ("/", ""), ("invest", "/invest"), ("/invest/", "/invest"), (" /invest ", "/invest")):
        monkeypatch.setenv("MIA_BASE_PATH", raw)
        assert base_path() == want, raw


def test_without_base_path_nothing_changes(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/invest", follow_redirects=False).status_code != 307


def test_prefix_is_stripped_and_unprefixed_requests_still_work(client, monkeypatch):
    monkeypatch.setenv("MIA_BASE_PATH", "/invest")
    assert client.get("/invest/api/health").json()["ok"] is True
    assert client.get("/api/health").json()["ok"] is True  # 127.0.0.1:포트로 직접 · 홈의 /api/feed/* 호출
    assert client.get("/invest/api/invest/config").status_code == 200


def test_guard_sees_the_stripped_path(client, monkeypatch):
    monkeypatch.setenv("MIA_BASE_PATH", "/invest")
    monkeypatch.setenv("MIA_ADMIN_PASSWORD_HASH", security.hash_password("correct horse battery"))
    assert client.get("/invest/api/invest/config").status_code == 401
    assert client.get("/invest/api/health").status_code == 200


def test_bare_base_redirects_to_trailing_slash_with_query(client, monkeypatch):
    monkeypatch.setenv("MIA_BASE_PATH", "/invest")
    r = client.get("/invest", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/invest/"
    r = client.get("/invest?a=1&b=2", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/invest/?a=1&b=2"


def test_similar_prefix_is_not_stripped(monkeypatch):
    monkeypatch.setenv("MIA_BASE_PATH", "/invest")
    seen = _call({"type": "http", "path": "/investor/api/x", "raw_path": b"/investor/api/x"})
    assert seen["path"] == "/investor/api/x" and seen["raw_path"] == b"/investor/api/x"


def test_http_and_websocket_paths_and_raw_path_are_stripped(monkeypatch):
    monkeypatch.setenv("MIA_BASE_PATH", "/invest")
    for kind in ("http", "websocket"):
        seen = _call({"type": kind, "path": "/invest/api/a b", "raw_path": b"/invest/api/a%20b"})
        assert seen["path"] == "/api/a b" and seen["raw_path"] == b"/api/a%20b", kind
    seen = _call({"type": "http", "path": "/invest/", "raw_path": None})
    assert seen["path"] == "/" and seen["raw_path"] == b"/"
    assert _call({"type": "lifespan"})["type"] == "lifespan"


def _call(scope: dict) -> dict:
    seen: dict = {}

    async def inner(s, _receive, _send):
        seen.update(s)

    async def noop(*_a):
        return None

    asyncio.run(BasePathMiddleware(inner)(scope, noop, noop))
    return seen
