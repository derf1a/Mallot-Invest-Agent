"""홈 대시보드용 요약 — 열쇠가 맞는 이 컴퓨터 안 GET /api/feed/* 만 로그인 없이, 숫자만 준다."""

import pytest

from app.auth import security

TOKEN = "t" * 40


@pytest.fixture
def locked(monkeypatch):
    monkeypatch.setenv("MIA_ADMIN_PASSWORD_HASH", security.hash_password("correct horse battery"))
    monkeypatch.setenv("MIA_FEED_TOKEN", TOKEN)


def test_token_reads_feed_from_local(client, locked):
    r = client.get("/api/feed/resources", headers={"X-Mallot-Feed": TOKEN})
    assert r.status_code == 200
    body = r.json()
    assert body["version"] not in ("0", "?") and body["app"] == "invest" and len(body["llm"]["daily"]) == 30 and "runs" in body
    s = client.get("/api/feed/summary", headers={"X-Mallot-Feed": TOKEN}).json()
    assert s["app"] == "invest" and "available" in s   # 테스트에는 토스가 없어 available=False 여도 200


@pytest.mark.parametrize("headers", [
    {"X-Mallot-Feed": "wrong" * 10},
    {"X-Mallot-Feed": TOKEN, "Cf-Connecting-IP": "203.0.113.1"},   # 터널을 거쳐 온 요청
    {},
])
def test_feed_refused_without_right_local_token(client, locked, headers):
    assert client.get("/api/feed/resources", headers=headers).status_code == 401


def test_token_opens_nothing_else(client, locked):
    assert client.get("/api/invest/portfolio", headers={"X-Mallot-Feed": TOKEN}).status_code == 401


def test_short_token_is_off(client, monkeypatch):
    monkeypatch.setenv("MIA_ADMIN_PASSWORD_HASH", security.hash_password("correct horse battery"))
    monkeypatch.setenv("MIA_FEED_TOKEN", "short")
    assert client.get("/api/feed/resources", headers={"X-Mallot-Feed": "short"}).status_code == 401


def test_admin_can_read_feed(client, locked):
    assert client.post("/api/auth/login", json={"password": "correct horse battery"}).status_code == 200
    assert client.get("/api/feed/summary").status_code == 200


def test_summary_numbers_from_portfolio(monkeypatch):
    from app.invest.broker import portfolio
    from app.system import feed

    pos = [{"symbol": "AAA", "name": "가짜전자", "value_krw": 600.0, "cost_krw": 500.0, "pnl_krw": 100.0},
           {"symbol": "BBB", "name": "테스트상사", "value_krw": 200.0, "cost_krw": None, "pnl_krw": None}]
    monkeypatch.setattr(portfolio, "overview", lambda conn: {
        "total_assets_krw": 1000, "stock_value_krw": 800, "cash": {"krw": 150, "usd": 0, "usd_in_krw": 50},
        "cash_ratio": 0.2, "usd_krw": 1400.0, "positions": pos, "concentration": {"count": 2}})
    s = feed.summary(None)
    assert (s["available"], s["pnl_krw"], s["pnl_rate"], s["holdings"]) == (True, 100, 0.2, 2)
    assert [(a["name"], a["weight"]) for a in s["allocation"]] == [("가짜전자", 0.6), ("테스트상사", 0.2), ("현금", 0.2)]


def test_summary_survives_broker_error(monkeypatch):
    from app.invest.broker import portfolio
    from app.system import feed

    def boom(conn):
        raise RuntimeError("토스 키가 없어요")
    monkeypatch.setattr(portfolio, "overview", boom)
    assert feed.summary(None) == {"app": "invest", "available": False, "error": "토스 키가 없어요"}


def test_health_gives_facts_for_home(client, locked):
    from app.core.migrations import MIGRATIONS

    h = client.get("/api/feed/health", headers={"X-Mallot-Feed": TOKEN}).json()
    assert h["integrity"] == "ok" and h["schema_version"] == h["schema_latest"] == len(MIGRATIONS)
    assert h["data_writable"] is True and h["last_backup_age_s"] is None
    assert h["llm_24h"] == {"calls": 0, "failed": 0} and h["runs_stuck"] == 0 and h["runs_failed_24h"] == 0


def test_health_needs_token(client, locked):
    assert client.get("/api/feed/health").status_code == 401
