"""에이전트가 정하는 비중 한도(종목당 · 섹터별)와 주식 비중 100% 원칙."""

import asyncio

import pytest

from app.core.db import connect
from app.invest.analysis import policy
from app.invest.broker import config
from app.invest.debate import agents
from app.invest.marketdata.market import SampleMarket
from tests.test_invest import _order, at_open  # noqa: F401 — 픽스처

BIG = {"max_order_krw": 100_000_000, "daily_max_krw": 100_000_000}


@pytest.fixture
def conn(client):
    c = connect()
    yield c
    c.close()


def test_save_risk_limits_checks_range_and_keeps_history(conn):
    assert config.risk_limits(conn)["max_position_pct"] == config.DEFAULT_LIMITS["max_position_pct"]  # 정하기 전엔 설정 화면 값
    with pytest.raises(config.ConfigError):
        config.save_risk_limits(conn, by="리스크 매니저", reason="", max_position_pct=20)          # 이유 필수
    with pytest.raises(config.ConfigError):
        config.save_risk_limits(conn, by="리스크 매니저", reason="한 종목 쏠림 허용", max_position_pct=80)  # 50% 넘게는 못 정한다
    with pytest.raises(config.ConfigError):
        config.save_risk_limits(conn, by="리스크 매니저", reason="섹터 조정 시험", sectors={"IT": 5})
    out = config.save_risk_limits(conn, by="리스크 매니저", reason="변동성이 커져 분산을 늘림", max_position_pct=15,
                                  sectors={"IT": 40, "금융": 30})
    lim = out["limits"]
    assert lim["max_position_pct"] == 15 and lim["position_set_by"] == "리스크 매니저" and lim["sectors"] == {"IT": 40, "금융": 30}
    assert [c["what"] for c in out["changes"]] == ["종목당 한도", "섹터 IT", "섹터 금융"]
    assert config.sector_limit(lim, "IT") == 40 and config.sector_limit(lim, "자동차") == config.DEFAULT_SECTOR_PCT
    again = config.save_risk_limits(conn, by="포트폴리오 매니저", reason="같은 값으로 다시", max_position_pct=15)
    assert again["changes"] == [] and len(config.risk_history(conn)) == 1
    out = config.save_risk_limits(conn, by="포트폴리오 매니저", reason="금융 한도는 기본으로", sectors={"금융": None})
    assert out["limits"]["sectors"] == {"IT": 40} and config.risk_history(conn)[-1]["by"] == "포트폴리오 매니저"
    assert config.snapshot(conn)["risk_limits"]["max_position_pct"] == 15


def test_orders_follow_agent_position_and_sector_limits(client, conn, broker, at_open):  # noqa: F811
    config.save_trading(conn, limits_=BIG)
    config.save_risk_limits(conn, by="리스크 매니저", reason="종목당 5%로 낮춤", max_position_pct=5)
    price = client.get("/api/invest/stocks/005930").json()["price"]
    res = _order(client, quantity=int(10_000_000 * 0.08 // price))
    assert res.status_code == 422 and "종목 비중 한도" in res.json()["detail"]["message"]
    assert "리스크 매니저" in res.json()["detail"]["message"]
    # 종목당은 넉넉히, IT 섹터(삼성전자 · SK하이닉스 · NAVER · 카카오)는 10%까지
    config.save_risk_limits(conn, by="포트폴리오 매니저", reason="IT 쏠림을 막음", max_position_pct=30, sectors={"IT": 10})
    assert _order(client, quantity=int(10_000_000 * 0.07 // price)).status_code == 201
    hynix = client.get("/api/invest/stocks/000660").json()["price"]
    res = _order(client, symbol="000660", quantity=max(1, int(10_000_000 * 0.05 // hynix)))
    assert res.status_code == 422 and "섹터 비중 한도 초과" in res.json()["detail"]["message"] and "IT" in res.json()["detail"]["message"]
    assert _order(client, symbol="005380", quantity=1).status_code == 201             # 다른 섹터(자동차)는 그대로


def test_bond_and_cash_like_products_are_not_bought(client, conn, broker, at_open, monkeypatch):  # noqa: F811
    config.save_trading(conn, limits_=BIG)
    real = SampleMarket.info
    monkeypatch.setattr(SampleMarket, "info", lambda self, sym: {**real(self, sym), "name": "KODEX 국고채3년"} if sym == "069500"
                        else real(self, sym))
    res = _order(client, symbol="069500", quantity=1)
    assert res.status_code == 422 and "채권 · 현금성" in res.json()["detail"]["message"]
    assert config.non_equity("TIGER CD금리투자KIS(합성)", "ETF") and config.non_equity("KODEX 머니마켓액티브", "ETF")
    assert not config.non_equity("KODEX 200", "ETF") and not config.non_equity("한국금리연구", "STOCK")


def test_agents_read_and_change_limits_with_tools(conn, broker, at_open):  # noqa: F811
    events: list[dict] = []
    s = agents.Session(conn=conn, scope={"scope": "symbol", "label": "x", "candidates": [
        {"symbol": "005930", "name": "삼성전자", "security_type": "STOCK", "held": 0}]}, run_id=None, provider=SampleMarket(),
        titles={"risk": "리스크 매니저"}, on_event=events.append)
    tools = agents._handlers(s, "risk")
    got = asyncio.run(tools["set_risk_limits"]({"max_position_pct": 25, "default_sector_pct": 50,
                                                "sectors": [{"sector": "IT", "pct": 45}], "reason": "IT 주도장이라 IT 한도를 넓힘"}))
    assert got["ok"] and got["limits"]["max_position_pct"] == 25 and got["limits"]["sectors"] == {"IT": 45}
    assert got["limits"]["candidate_sectors"] == {"005930": "IT"} and got["limits"]["equity_target_pct"] == 100
    assert events[-1]["type"] == "risk_limits" and events[-1]["by"] == "리스크 매니저"
    bad = asyncio.run(tools["set_risk_limits"]({"max_position_pct": 90, "reason": "한 종목에 거의 다 싣기"}))
    assert not bad["ok"] and "5~50" in bad["error"]
    view = asyncio.run(tools["get_risk_limits"]({}))
    assert view["recent_changes"][-1]["by"] == "리스크 매니저" and "100%" in view["rule"]
    roster = {a["key"]: a["tools"] for a in agents.DEFAULT_ROSTER}
    assert "set_risk_limits" in roster["risk"] and "set_risk_limits" in roster["portfolio"] and "set_risk_limits" not in roster["trader"]
    assert "100% 고정" in agents.COMMON_RULES


def test_equity_target_is_fixed_at_100(conn):
    assert policy.get(conn)["equity_target"] == 100
    out = policy.save(conn, {"equity_target": 70, "target_return": 10}, by="assistant", reason="100%→70%")
    assert out["policy"]["equity_target"] == 100 and out["fixed"] == ["주식 비중 목표"]
    assert "주식 비중 100%" in policy.text(policy.get(conn))
    assert [c["field"] for c in policy.history(conn)[-1]["changes"]] == ["target_return"]
    assert policy.history(conn)[-1]["reason"] == "100%→70% (고정이라 바꾸지 않음: 주식 비중 목표)"   # 기록이 실제와 맞게 (#43)


@pytest.mark.parametrize("value", [0, "0", 1, None])
def test_update_policy_says_equity_target_is_fixed(conn, value):
    """equity_target 만 0 · "0" · 1 · null 로 바꾸려 하면 ok:true · '바뀐 것 없음' 이 아니라 고정이라 못 바꾼다고 답한다 (#43)."""
    from app.invest.advice import assistant
    s = agents.Session(conn=conn, scope={"label": "비서", "candidates": []}, run_id=None, provider=SampleMarket())
    tools = {t.name: t for t in assistant.build_tools(conn, s, [], None)}
    out = asyncio.run(tools["update_policy"].handler({"changes": {"equity_target": value}, "reason": "현금 늘리기"}))
    assert out["ok"] is False and "100% 고정" in out["not_changed"]
    both = asyncio.run(tools["update_policy"].handler({"changes": {"equity_target": value, "turnover": "low"}}))
    assert both["ok"] and both["changed"] == ["매매 빈도: 없음 → 가끔"] and "고정" in both["not_changed"]
    assert policy.get(conn)["equity_target"] == 100
