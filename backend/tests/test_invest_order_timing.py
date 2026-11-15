"""자동 주문 시간 · 장 초반 지정가만 · 호가 기준 지정가 · 체결 안 된 주문 중복 막기 · 판단 시간 끝 정리 · 아침 브리핑이 리서치 기다리기."""

from datetime import datetime

import pytest

from app.core.db import connect
from app.invest.broker import trading
from app.invest.marketdata import market
from app.invest.marketdata.market import KST, SampleMarket, auto_order_problem, snap_tick, tick_step, trading_window
from app.invest.ops import autopilot, desk, morning


def at(hhmm: str, day: int = 17) -> datetime:  # 2026-11-17 화요일
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 11, day, h, m, tzinfo=KST)


def sample(hhmm: str, day: int = 17) -> SampleMarket:
    return SampleMarket(clock=lambda: at(hhmm, day))


@pytest.fixture
def conn(client):
    c = connect()
    yield c
    c.close()


@pytest.fixture
def hours(monkeypatch):
    monkeypatch.setattr(trading, "CHECK_HOURS", True)


def test_window_and_auto_order_problem():
    assert trading_window(sample("10:00")) == ("09:05", "15:15")
    assert "자동 주문 시간" in auto_order_problem(sample("08:50"), "limit")
    assert "자동 주문 시간" in auto_order_problem(sample("15:16"), "limit")      # 마감 동시호가(15:20) 전에 끊는다
    assert "지정가" in auto_order_problem(sample("09:10"), "market")             # 장 초반 30분은 시장가 안 됨
    assert auto_order_problem(sample("09:10"), "limit") is None
    assert auto_order_problem(sample("09:30"), "market") is None
    assert auto_order_problem(sample("15:15"), "market") is None

    class Holiday(SampleMarket):
        def session(self):
            return {"date": "2026-11-17", "is_open": False, "start": None, "end": None, "note": "오늘은 휴장일입니다"}
    assert "휴장일" in auto_order_problem(Holiday(clock=lambda: at("10:00")), "limit")


def test_tick_helpers():
    assert tick_step(2_000, -1) == 1_999 and tick_step(1_999, 1) == 2_000 and tick_step(2_000, 1) == 2_005
    assert tick_step(71_000, -2) == 70_800 and tick_step(50_000, -1) == 49_950
    assert snap_tick(70_930, "buy") == 70_900 and snap_tick(70_930, "sell") == 71_000


def _auto(conn, m, **kw):
    return trading.place_order(conn, symbol="005930", side="buy", quantity=1, source="agent", auto_submit=True, market=m, **kw)


def test_auto_order_refused_outside_hours_and_market_at_open(conn, broker, hours):
    broker.market = m = sample("15:18")
    with pytest.raises(trading.TradeError, match="자동 주문 시간"):
        _auto(conn, m, order_type="market")
    broker.market = m = sample("09:12")
    with pytest.raises(trading.TradeError, match="지정가"):
        _auto(conn, m, order_type="market")
    assert broker.posts() == []
    # 승인 대기로 남는 에이전트 주문 · 수동 주문은 사람이 정하니 시간 검사를 하지 않는다
    pending = trading.place_order(conn, symbol="005930", side="buy", order_type="market", quantity=1, source="agent", market=sample("20:00"))
    assert pending["status"] == "pending_approval"


def test_price_at_bid_ticks_and_duplicate_guard(conn, broker, hours):
    broker.market = m = sample("10:00")
    book = m.orderbook("005930")
    bid = book["bids"][0]["price"]
    order = _auto(conn, m, order_type="market", price_at="bid", ticks=-3)         # price_at 이면 지정가
    want = tick_step(bid, -3)
    assert order["order_type"] == "limit" and order["price"] == want
    assert broker.posts()[-1]["orderType"] == "LIMIT" and float(broker.posts()[-1]["price"]) == want
    assert order["status"] == "open" and order["checks"][0].startswith("가격: 매수 1호가")  # 현재가 아래라 체결을 기다린다
    with pytest.raises(trading.TradeError, match="체결되지 않은 주문"):
        _auto(conn, m, order_type="limit", price_at="ask")
    with pytest.raises(trading.TradeError, match="price_at"):
        trading.place_order(conn, symbol="000660", side="buy", quantity=1, source="agent", auto_submit=True, market=m,
                            order_type="limit", price_at="best")


def test_close_out_cancels_unfilled_auto_orders(conn, broker, hours, monkeypatch):
    broker.market = m = sample("10:00")
    order = _auto(conn, m, order_type="limit", price_at="bid", ticks=-5)
    assert order["status"] == "open"
    monkeypatch.setattr(autopilot, "get_config", lambda c: {**autopilot.DEFAULT_CONFIG, "enabled": True})
    assert autopilot.close_out(conn, at("15:00"), sample("15:00")) is None       # 아직 판단 시간
    [done] = autopilot.close_out(conn, at("15:16"), sample("15:16"))
    assert done["id"] == order["id"] and done["status"] == "cancelled"
    assert autopilot.close_out(conn, at("15:17"), sample("15:17")) is None       # 다시 할 일 없음


def test_morning_briefing_waits_for_running_research(conn, monkeypatch):
    now = at("08:30")
    assert morning.waiting_research(conn, now, 0) is False                        # 리서치가 안 돌면 기다리지 않는다
    monkeypatch.setattr(desk, "research_running", lambda c: {"id": 1})
    assert morning.waiting_research(conn, now, 5) is True
    assert morning.waiting_research(conn, now, morning.RESEARCH_WAIT_MIN) is False  # 너무 오래 기다리면 그냥 보낸다
    monkeypatch.setattr(desk, "latest", lambda c, stage, day=None: {"id": 1} if day == now.date() else None)
    assert morning.waiting_research(conn, now, 5) is False                        # 오늘 시황이 나왔으면 바로
    monkeypatch.setattr(market, "now_kst", lambda: now)
    monkeypatch.setattr(morning, "get_config", lambda c: {**morning.DEFAULT_CONFIG, "last": None, "close_last": None})
    monkeypatch.setattr(desk, "latest", lambda c, stage, day=None: None)
    sent = []
    monkeypatch.setattr(morning, "send", lambda *a, **k: sent.append(1) or {"ok": True})
    assert morning.tick(conn, now, sample("08:30")) is None and sent == []       # 기다림
    monkeypatch.setattr(desk, "research_running", lambda c: None)
    assert morning.tick(conn, now, sample("08:30")) == {"ok": True}
