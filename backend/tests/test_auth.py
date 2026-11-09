"""관리자 로그인 · 손님 공유 링크 · 요청마다 역할 검사."""

import pytest

from app.auth import security

PASSWORD = "correct horse battery"
PROXY = {"Cf-Connecting-IP": "203.0.113.7"}


@pytest.fixture
def admin_pw(monkeypatch):
    monkeypatch.setenv("MIA_ADMIN_PASSWORD_HASH", security.hash_password(PASSWORD))


def _login(client, pw=PASSWORD):
    return client.post("/api/auth/login", json={"password": pw})


def test_without_password_local_is_open_but_tunnelled_requests_are_refused(client):
    assert client.get("/api/invest/config").status_code == 200  # 이 컴퓨터에서 쓰는 지금 그대로
    assert client.get("/api/auth/me").json()["role"] == "admin"
    r = client.get("/api/invest/config", headers=PROXY)
    assert r.status_code == 403 and "비밀번호" in r.json()["detail"]
    assert client.get("/openapi.json", headers=PROXY).status_code == 403


def test_admin_api_needs_login(client, admin_pw):
    assert client.get("/api/invest/config").status_code == 401
    assert client.get("/api/runs").status_code == 401 and client.get("/api/traces").status_code == 401
    assert client.get("/docs").status_code == 401 and client.get("/openapi.json").status_code == 401
    assert client.get("/api/health").status_code == 200  # 살아 있는지는 누구나
    assert client.get("/api/auth/me").json() == {"auth": True, "public": False, "has_password": True, "role": None,
                                                 "share_enabled": False}
    assert _login(client, "wrong").status_code == 401
    r = _login(client)
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    assert client.get("/api/invest/config").status_code == 200 and client.get("/api/auth/me").json()["role"] == "admin"
    client.post("/api/auth/logout")
    assert client.get("/api/invest/config").status_code == 401


def test_login_locks_after_repeated_failures(client, admin_pw):
    for _ in range(security.MAX_FAILS):
        assert _login(client, "nope").status_code == 401
    r = _login(client)  # 맞는 비밀번호도 잠긴 동안은 안 된다
    assert r.status_code == 429 and "분 뒤" in r.json()["detail"]


def test_changing_the_password_ends_old_sessions(client, admin_pw, monkeypatch):
    _login(client)
    assert client.get("/api/invest/config").status_code == 200
    monkeypatch.setenv("MIA_ADMIN_PASSWORD_HASH", security.hash_password("another long password"))
    assert client.get("/api/invest/config").status_code == 401


def test_viewer_enters_with_the_share_key_and_reads_everything_but_writes_nothing(client, admin_pw):
    _login(client)
    share = client.get("/api/auth/share").json()
    assert share["enabled"] is False and share["path"] == f"/share?k={share['key']}"
    assert client.post("/api/auth/share/enter", json={"key": share["key"]}).status_code == 403  # 아직 꺼짐
    client.put("/api/auth/share", json={"enabled": True})
    client.post("/api/auth/logout")

    assert client.get("/api/share/status").status_code == 401
    assert client.post("/api/auth/share/enter", json={"key": "guess"}).status_code == 403
    assert client.post("/api/auth/share/enter", json={"key": share["key"]}).status_code == 200
    assert client.get("/api/auth/me").json()["role"] == "viewer"
    st = client.get("/api/share/status").json()
    assert {"autopilot", "desk", "latest"} <= set(st) and isinstance(st["autopilot"]["intents"], int)  # 지시 글은 개수만
    assert client.get("/api/share/sessions").status_code == 200 and client.get("/api/share/reports").json() == []
    # 관리자 화면을 읽기만 — 개발 화면(트레이스 · 실행 · 그래프)까지
    for path in ("/api/invest/assistant/messages", "/api/llm/routes", "/api/traces", "/api/runs", "/api/graph/agents",
                 "/api/invest/autopilot", "/api/invest/status"):
        assert client.get(path).status_code == 200, path
    # 키(가린 값도) · .env 위치 · vLLM 주소는 빼고 보낸다
    cfg = client.get("/api/invest/config").json()
    assert "keys" not in cfg and "env_file" not in cfg and "limits" in cfg
    assert client.get("/api/llm/vllm").json()["url"] is None
    # 공유 링크 · 키 · 문서는 읽기도 막는다
    for path in ("/api/auth/share", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 403, path
    # 쓰기는 모두 막는다 — 설정 · 주문 · 자동 운용 · 공유 · 비서
    for method, path, body in (("put", "/api/invest/config/keys", {"DART_API_KEY": "x"}), ("post", "/api/invest/orders", {}),
                               ("put", "/api/invest/autopilot", {"enabled": True}), ("put", "/api/auth/share", {"rotate": True}),
                               ("post", "/api/invest/assistant/messages", {"text": "사 줘"}), ("delete", "/api/runs/1", None)):
        r = client.request(method.upper(), path, json=body)
        assert r.status_code == 403 and "읽기 전용" in r.json()["detail"], (method, path)
    assert not security.enabled() or client.get("/api/auth/me").json()["role"] == "viewer"  # 로그아웃은 된다
    assert client.post("/api/auth/logout").status_code == 200


def test_admin_still_sees_keys(client, admin_pw):
    _login(client)
    assert "keys" in client.get("/api/invest/config").json() and client.get("/api/auth/share").status_code == 200


def test_rotating_the_key_or_turning_share_off_cuts_viewers(client, admin_pw):
    from fastapi.testclient import TestClient

    from app.main import app

    _login(client)
    key = client.put("/api/auth/share", json={"enabled": True}).json()["key"]
    with TestClient(app) as guest:
        assert guest.post("/api/auth/share/enter", json={"key": key}).status_code == 200
        assert guest.get("/api/share/status").status_code == 200
        new = client.put("/api/auth/share", json={"rotate": True}).json()["key"]
        assert new != key and guest.get("/api/share/status").status_code == 401
        assert guest.post("/api/auth/share/enter", json={"key": new}).status_code == 200
        client.put("/api/auth/share", json={"enabled": False})
        assert guest.get("/api/share/status").status_code == 401


def test_cross_site_writes_are_refused(client, admin_pw):
    _login(client)
    r = client.put("/api/auth/share", json={"enabled": True}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.put("/api/auth/share", json={"enabled": True}, headers={"Origin": "http://testserver"}).status_code == 200


def test_public_mode_without_password_fails_closed(client, monkeypatch):
    monkeypatch.setenv("MIA_PUBLIC", "1")
    assert client.get("/api/invest/config").status_code == 401
    assert _login(client, "anything").status_code == 403


def test_share_report_is_cached_and_lists_saved_report_cards(client, admin_pw, monkeypatch):
    from app.invest.advice import assistant
    from app.invest.ops import briefing

    calls = []
    monkeypatch.setattr(briefing, "snapshot", lambda conn: calls.append(1) or {"title": "투자 현황 보고", "v": 2})
    _login(client)
    assert client.get("/api/share/report").json()["title"] == "투자 현황 보고"
    client.get("/api/share/report")
    assert len(calls) == 1  # 60초 안에는 다시 만들지 않는다
    from app.core.db import connect

    with connect() as conn:
        assistant.add(conn, "event", "아침 브리핑이 왔어요", kind="report", morning=True, report={"title": "아침 브리핑"}, body="글")
        assistant.add(conn, "assistant", "비서의 다른 대화")
    got = client.get("/api/share/reports").json()
    assert [(r["title"], r["morning"], r["body"]) for r in got] == [("아침 브리핑이 왔어요", True, "글")]
