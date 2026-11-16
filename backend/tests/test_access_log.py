"""접속 기록 — IP 하루 묶음 · 로그인 사건 · 한국 밖 경보 · 관리자만 보기."""

import pytest

from app.auth import security

PASSWORD = "correct horse battery"
KR = {"CF-Connecting-IP": "203.0.113.7", "CF-IPCountry": "KR"}
US = {"CF-Connecting-IP": "198.51.100.9", "CF-IPCountry": "US"}


@pytest.fixture
def admin(client, monkeypatch):
    monkeypatch.setenv("MIA_ADMIN_PASSWORD_HASH", security.hash_password(PASSWORD))
    return client


def test_requests_are_grouped_per_ip_and_logins_are_events(admin):
    c = admin
    assert c.post("/api/auth/login", json={"password": "wrong-password"}, headers=KR).status_code == 401
    assert c.post("/api/auth/login", json={"password": PASSWORD}, headers=KR).status_code == 200
    for _ in range(3):
        c.get("/api/invest/config", headers=KR)
    c.get("/api/health", headers=KR)                                                   # 세지 않음
    c.get("/api/invest/config")                                                                 # 터널 밖(이 컴퓨터)
    v = c.get("/api/auth/access").json()
    kr = next(r for r in v["ips"] if r["ip"] == "203.0.113.7")
    assert kr["country"] == "KR" and kr["requests"] == 5 and kr["denied"] == 1 and set(kr["roles"].split(",")) >= {"none", "admin"}
    assert [(e["kind"], e["ok"], e["note"], e["ip"]) for e in v["events"]] == [("login", 1, "", "203.0.113.7"),
                                                                            ("login", 0, "비밀번호 틀림", "203.0.113.7")]
    assert v["alerts"] == [] and v["open_alerts"] == 0
    local = next(r for r in v["ips"] if r["ip"] == "local")
    assert local["country"] is None and local["roles"] == "admin"


def test_foreign_ip_raises_one_alert_per_day_until_acked(admin, caplog):
    c = admin
    c.post("/api/auth/login", json={"password": "nope-nope-nope"}, headers=US)
    c.get("/api/invest/config", headers=US)
    c.post("/api/auth/login", json={"password": PASSWORD})
    alerts = c.get("/api/auth/access/alerts").json()
    assert len(alerts) == 1 and alerts[0]["country"] == "US" and "관리자 로그인 실패" in alerts[0]["reason"]
    assert any("한국 밖 접속 감지" in r.message for r in caplog.records)
    assert c.post("/api/auth/access/ack", json={}).json() == {"acked": 1}
    assert c.get("/api/auth/access/alerts").json() == []
    c.get("/api/invest/config", headers=US)                                                     # 같은 날 같은 IP 는 다시 경보하지 않는다
    assert c.get("/api/auth/access/alerts").json() == []


def test_access_log_is_admin_only(admin):
    c = admin
    c.post("/api/auth/login", json={"password": PASSWORD})
    key = c.put("/api/auth/share", json={"enabled": True}).json()["key"]
    c.post("/api/auth/logout")
    assert c.get("/api/auth/access").status_code == 401
    c.post("/api/auth/share/enter", json={"key": key}, headers=KR)
    assert c.get("/api/auth/access").status_code == 403 and c.get("/api/auth/access/alerts").status_code == 403
    assert c.post("/api/auth/access/ack", json={}).status_code == 403
