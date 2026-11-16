"""주문 장부 — 토론에서 나온 수량 안에서만 주문, 같은 실행 · 종목 · 방향의 중복 주문 거절, 오늘 낸 주문이 에이전트에게 보임,
하루 자동 주문 건수를 안건 문구와 get_trading_limits 가 같은 기준으로."""

import asyncio
from datetime import datetime, timedelta

import pytest

from app.invest.debate import agents, order_guard, stream
from app.invest.marketdata import market
from app.invest.marketdata.market import KST, SampleMarket
from tests.test_invest_ai import conn  # noqa: F401 — fixture


@pytest.fixture
def now(monkeypatch):
    """주문 기록(created_at, DB 시각)과 같은 시계로 — 중복 창(DUP_MIN 분)을 잰다."""
    t = datetime.now(KST).replace(microsecond=0)
    monkeypatch.setattr(market, "now_kst", lambda: t)
    return t


def _run(conn) -> int:
    rid = conn.execute("INSERT INTO runs (kind, name, status) VALUES ('agent_action', '상시 운용 · 오늘', 'running')").lastrowid
    conn.commit()
    return rid


def _session(conn, rid, sizes=None, max_orders=agents.MAX_ORDERS) -> agents.Session:
    s = agents.Session(conn=conn, scope={"scope": "stream", "label": "상시 운용", "candidates": [{"symbol": "005930", "name": "삼성전자"}]},
                       run_id=rid, provider=SampleMarket(), auto_submit=True, max_orders=max_orders)
    s.gate = {"status": "aligned", "symbols": {}, "aligned": [{"symbol": "005930", "direction": "buy"}]}
    s.sizes = sizes or {}
    return s


def _order(s, quantity):
    return asyncio.run(agents._handlers(s)["place_order"]({"symbol": "005930", "side": "buy", "quantity": quantity,
                                                           "order_type": "market", "reason": "합의"}))


def test_trader_cannot_size_above_what_agreeing_agents_proposed(client, conn, broker, now):  # noqa: F811
    """토론에서 찬성한 에이전트가 1주를 말했는데 트레이더가 2주를 내던 경우 — 코드가 막고, 시도 횟수는 쓰지 않는다."""
    views = [("가치 투자자", {"stance": "buy", "quantity": 1}), ("차티스트", {"stance": "buy", "quantity": None}),
             ("리스크 매니저", {"stance": "sell", "quantity": 5})]                                    # 반대 쪽 수량은 세지 않는다
    sizes = {"005930": order_guard.proposals(views, "buy")}
    assert sizes["005930"] == {"max": 1, "by": [("가치 투자자", 1)]}
    s = _session(conn, _run(conn), sizes)
    got = _order(s, 2)
    assert not got["ok"] and "최대 1주" in got["error"] and "가치 투자자 1주" in got["error"]
    assert s.order_attempts["005930"] == 0 and broker.posts() == []
    assert _order(s, 1)["ok"]
    assert order_guard.proposals([("차티스트", {"stance": "buy"})], "buy") is None                    # 아무도 수량을 안 냈으면 막지 않음
    assert order_guard.size_error(50, None, "삼성전자") is None


def test_same_run_symbol_side_is_not_ordered_twice(client, conn, broker, now):  # noqa: F811
    """상시 운용의 다음 대화가 같은 종목을 또 사려 하면 — 앞 주문이 DUP_MIN 분 안이거나 아직 살아 있으면 거절."""
    rid = _run(conn)
    assert _order(_session(conn, rid), 1)["ok"]
    again = _order(_session(conn, rid), 1)                                                            # 새 대화 = 새 세션, 같은 실행
    assert not again["ok"] and "중복 주문 방지" in again["error"] and len(broker.posts()) == 1
    other = _run(conn)
    assert _order(_session(conn, other), 1)["ok"]                                                     # 다른 실행(다른 날 채널 등)은 막지 않음
    assert order_guard.duplicate(conn, rid, "005930", "buy", now + timedelta(minutes=order_guard.DUP_MIN + 1)) is None
    assert order_guard.duplicate(conn, rid, "005930", "sell", now) is None                            # 반대 방향(손절)은 막지 않음
    conn.execute("INSERT INTO invest_orders (symbol, name, side, order_type, quantity, status, run_id, created_at) "
                 "VALUES ('000660', 'SK하이닉스', 'buy', 'limit', 1, 'open', ?, '2026-01-01 09:00:00')", (rid,))
    conn.commit()
    assert "아직 미체결" in order_guard.duplicate(conn, rid, "000660", "buy", now)                     # 오래됐어도 살아 있으면 막음


def test_agents_see_todays_orders_and_one_order_count(client, conn, broker, now):  # noqa: F811
    """보유가 늘어난 이유(오늘 낸 주문 · 실행 번호)가 get_portfolio · get_trading_limits 에 보이고, 하루 건수는 안건 문구와 같은 수."""
    rid = _run(conn)
    s = _session(conn, rid, max_orders=2)
    assert _order(s, 1)["ok"]
    conn.execute("INSERT INTO invest_orders (symbol, name, side, order_type, quantity, status, source, run_id) "
                 "VALUES ('005930', '삼성전자', 'buy', 'market', 9, 'rejected', 'agent', ?)", (rid,))  # 거절은 세지 않는다
    conn.commit()
    h = agents._handlers(s)
    pf = asyncio.run(h["get_portfolio"]({}))
    assert [(o["symbol"], o["quantity"], o["run_id"]) for o in pf["today_orders"]] == [("005930", 1, rid)]
    lim = asyncio.run(h["get_trading_limits"]({}))
    assert (lim["orders_this_session"], lim["max_orders_this_session"], lim["orders_left_this_session"]) == (1, 2, 1)
    assert (lim["agent_orders_today"], lim["max_agent_orders_per_day"], lim["agent_orders_left_today"]) == (1, order_guard.MAX_DAY_ORDERS,
                                                                                                         order_guard.MAX_DAY_ORDERS - 1)
    assert "max_orders_per_session" not in lim and lim["today_orders"][0]["order_id"] == pf["today_orders"][0]["order_id"]
    day = order_guard.day_status(conn)
    text = order_guard.day_text(day)
    assert f"오늘 남은 자동 주문 {order_guard.MAX_DAY_ORDERS - 1}건" in text
    assert text in stream.trader_review_prompt(s, "없음", day)                                       # 안건 문구 = 같은 수 · 같은 말
    gate = {"aligned": [{"symbol": "005930", "direction": "buy"}], "symbols": {"005930": {"reason": "r"}}}
    trade = stream.trade_prompt(s, gate, "없음", "계기", day)
    assert text in trade and "이번 대화 2건까지" in trade
    assert "두 번 나간 것이 아닙니다" in trade and f"주문 #{pf['today_orders'][0]['order_id']} · 실행 #{rid}" in trade


def test_stream_board_carries_proposed_sizes(client, conn):  # noqa: F811
    t = datetime(2026, 11, 13, 10, 0, tzinfo=KST)
    b = stream.Board()
    st = agents.normalize_statement({"views": [{"symbol": "005930", "stance": "buy", "confidence": 0.6, "reason": "r", "quantity": "2"}],
                                     "thesis": "t"}, {"key": "value", "title": "가치 투자자"}, 1, None, ["005930"])
    assert st["views"][0]["quantity"] == 2
    b.update(st, t)
    b.update({"agent": "risk", "title": "리스크 매니저", "views": [{"symbol": "005930", "stance": "buy", "confidence": 0.5, "quantity": 4}]}, t)
    b.update({"agent": "news", "title": "뉴스", "views": [{"symbol": "005930", "stance": "sell", "confidence": 0.5, "quantity": 9}]}, t)
    assert b.sizes("005930", "buy", t) == {"max": 4, "by": [("가치 투자자", 2), ("리스크 매니저", 4)]}
    assert b.sizes("005930", "buy", t + timedelta(minutes=stream.STALE_MIN + 1)) is None              # 오래된 입장은 빠진다
    s = _session(conn, None)
    assert "제안 2주" in agents._statement_text(s, st)
