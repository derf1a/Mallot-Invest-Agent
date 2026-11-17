"""홈 통합 로그인 (MIA_HOME_AUTH_URL) — 역할은 Mallot 홈이 정하고, 앱은 묻기만 한다. 홈 호출은 가짜로 바꿔 끼운다."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.auth import security

PROXY = {"Cf-Connecting-IP": "203.0.113.7"}


@pytest.fixture
def home(monkeypatch):
    """홈 모드 + 가짜 home_role: 쿠키 값이 곧 역할 ('admin' · 'viewer' · 'down' 이면 장애)."""
    monkeypatch.setenv("MIA_HOME_AUTH_URL", "http://127.0.0.1:9")
    asked: list = []

    def fake(session):
        asked.append(session)
        if session == "down":
            raise security.HomeUnavailable("연결 안 됨")
        return session if session in ("admin", "viewer") else None

    monkeypatch.setattr(security, "home_role", fake)
    return asked


def _as(client, value):
    client.cookies.clear()
    if value is not None:
        client.cookies.set(security.HOME_COOKIE, value)


def test_home_admin_opens_everything(client, home):
    _as(client, "admin")
    assert client.get("/api/invest/config", headers=PROXY).status_code == 200
    assert client.get("/api/auth/access", headers=PROXY).status_code == 200
    assert client.get("/api/auth/me", headers=PROXY).json() == {
        "auth": True, "public": False, "has_password": False, "role": "admin", "share_enabled": False,
        "home": True, "login_url": "/login", "logout_url": "/logout"}


def test_home_viewer_reads_only(client, home):
    _as(client, "viewer")
    assert client.get("/api/invest/config", headers=PROXY).status_code == 200
    assert "keys" not in client.get("/api/invest/config", headers=PROXY).json()  # 손님용으로 줄인 값 (state.role)
    assert client.put("/api/invest/config/trading", json={"halted": True}, headers=PROXY).status_code == 403
    assert client.get("/api/auth/access", headers=PROXY).status_code == 403
    assert client.get("/openapi.json", headers=PROXY).status_code == 403
    assert client.get("/api/auth/me", headers=PROXY).json()["role"] == "viewer"


def test_home_nobody_gets_401_with_login_url(client, home):
    _as(client, "stranger")
    r = client.get("/api/invest/config", headers=PROXY)
    assert r.status_code == 401 and r.json() == {"detail": "로그인해 주세요", "login_url": "/login"}
    me = client.get("/api/auth/me", headers=PROXY)
    assert me.status_code == 200 and me.json()["role"] is None and me.json()["home"] is True
    assert client.get("/api/health", headers=PROXY).status_code == 200


def test_home_without_cookie_does_not_ask_home(client, monkeypatch):
    monkeypatch.setenv("MIA_HOME_AUTH_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(security, "_ask_home", lambda _s: pytest.fail("쿠키가 없으면 홈에 묻지 않는다"))
    assert client.get("/api/invest/config", headers=PROXY).status_code == 401


def test_home_down_fails_closed_with_503(client, home):
    _as(client, "down")
    r = client.get("/api/invest/config", headers=PROXY)
    assert r.status_code == 503 and r.json() == {"detail": "Mallot 홈 로그인 서버에 연결하지 못했어요"}
    assert client.get("/api/auth/me", headers=PROXY).status_code == 503
    assert client.get("/api/health", headers=PROXY).status_code == 200


def test_home_unproxied_request_is_admin_without_asking(client, home):
    _as(client, None)
    assert client.get("/api/invest/config").status_code == 200
    assert client.get("/api/auth/me").json()["role"] == "admin"
    assert home == []


def test_home_mode_refuses_app_login_and_share(client, home):
    _as(client, "admin")
    for method, path, body in (("post", "/api/auth/login", {"password": "x"}), ("post", "/api/auth/logout", None),
                               ("post", "/api/auth/share/enter", {"key": "k"}), ("get", "/api/auth/share", None),
                               ("put", "/api/auth/share", {"enabled": True})):
        for headers in (PROXY, {}):
            r = client.request(method.upper(), path, json=body, headers=headers)
            assert r.status_code == 409 and r.json()["detail"] == "Mallot 홈에서 로그인 · 관리해요", (path, headers)


def test_home_keeps_origin_check_and_feed_token(client, home, monkeypatch):
    _as(client, "admin")
    r = client.put("/api/invest/config/trading", json={"halted": False}, headers={**PROXY, "Origin": "https://evil.example"})
    assert r.status_code == 403
    token = "f" * 40
    monkeypatch.setenv("MIA_FEED_TOKEN", token)
    _as(client, None)
    assert client.get("/api/feed/summary", headers={"X-Mallot-Feed": token}).status_code == 200
    assert home == []  # 열쇠 요청은 홈에 묻지 않는다


def test_home_role_caches_by_session_hash(monkeypatch):
    monkeypatch.setenv("MIA_HOME_AUTH_URL", "http://127.0.0.1:9")
    calls: list = []
    monkeypatch.setattr(security, "_ask_home", lambda s: calls.append(s) or "viewer")
    assert security.home_role("abc") == "viewer" and security.home_role("abc") == "viewer"
    assert calls == ["abc"]
    assert security.home_role(None) is None and security.home_role("") is None and calls == ["abc"]
    monkeypatch.setattr(security, "HOME_CACHE_MAX", 3)
    for i in range(5):
        security.home_role(f"s{i}")
    assert len(security._home_cache) == 3


class _Home(BaseHTTPRequestHandler):
    seen: list = []
    status = 200
    body = {"role": "admin"}

    def do_GET(self):  # noqa: N802
        type(self).seen.append((self.path, self.headers.get("X-Mallot-Feed"), self.headers.get("X-Mallot-Session")))
        raw = json.dumps(type(self).body).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_a):
        pass


@pytest.fixture
def fake_home(monkeypatch):
    """이 컴퓨터 안의 가짜 홈 서버 (urllib 이 실제로 보내는 요청을 확인)."""
    server = HTTPServer(("127.0.0.1", 0), _Home)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Home.seen, _Home.status, _Home.body = [], 200, {"role": "admin"}
    monkeypatch.setenv("MIA_HOME_AUTH_URL", f"http://127.0.0.1:{server.server_port}/")
    monkeypatch.setenv("MIA_FEED_TOKEN", "t" * 32)
    yield _Home
    server.shutdown()
    server.server_close()


def test_ask_home_sends_feed_token_and_session(fake_home):
    assert security._ask_home("sess-1") == "admin"
    assert fake_home.seen == [("/internal/auth/check?app=invest", "t" * 32, "sess-1")]
    fake_home.body = {"role": "root"}
    assert security._ask_home("sess-2") is None  # 모르는 역할은 None
    fake_home.status, fake_home.body = 401, {"detail": "열쇠"}
    with pytest.raises(security.HomeUnavailable):
        security._ask_home("sess-3")


def test_ask_home_unreachable_is_unavailable(monkeypatch):
    monkeypatch.setenv("MIA_HOME_AUTH_URL", "http://127.0.0.1:9")  # discard 포트 — 아무도 듣지 않는다
    with pytest.raises(security.HomeUnavailable):
        security._ask_home("x")
