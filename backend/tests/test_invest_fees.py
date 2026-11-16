"""매매 비용 — 수수료 · 거래세 계산, 매수 가능 금액에 수수료 포함, 매도 점검의 비용 뺀 손익, 에이전트 프롬프트."""

import pytest

from app.core.db import connect
from app.invest.broker import fees, trading
from app.invest.debate import agents
from app.invest.marketdata.market import SampleMarket, snap_tick


@pytest.fixture
def conn(client):
    c = connect()
    yield c
    c.close()


def test_rates_match_real_fills():
    # 실제 체결: 매수 1,000,000원 → 수수료 150원, 주식 매도 1,000,000원 → 수수료 150원 + 거래세 2,000원
    assert fees.estimate("buy", 1_000_000) == {"commission": 150, "tax": 0, "total": 150}
    assert fees.estimate("sell", 1_000_000, "STOCK") == {"commission": 150, "tax": 2_000, "total": 2_150}
    assert fees.estimate("sell", 1_000_000, "ETF")["tax"] == 0 and fees.estimate("sell", 1_000_000, "ETN")["tax"] == 0
    assert fees.estimate("sell", 32_399, "STOCK") == {"commission": 4, "tax": 64, "total": 68}   # 원 미만 버림
    assert fees.round_trip() == pytest.approx(0.0023) and fees.round_trip("ETF") == pytest.approx(0.0003)


def test_net_pnl_and_breakeven():
    assert fees.net_pnl_rate(10_000, 10_020) < 0 < fees.net_pnl_rate(10_000, 10_030)   # +0.2% 평가익은 비용 빼면 손해
    assert fees.net_pnl_rate(10_000, 10_020, "ETF") > 0
    be = fees.breakeven_price(10_000)
    assert fees.net_pnl_rate(10_000, be) == pytest.approx(0, abs=1e-4) and round(be) == 10_023


def test_env_override(monkeypatch):
    monkeypatch.setenv("MIA_COMMISSION_RATE", "0.0001")
    monkeypatch.setenv("MIA_SELL_TAX_RATE", "abc")                                     # 잘못된 값은 기본값
    assert fees.commission_rate() == 0.0001 and fees.sell_tax_rate() == fees.SELL_TAX_RATE
    monkeypatch.setenv("MIA_COMMISSION_RATE", "0.5")                                    # 터무니없는 값도 기본값
    assert fees.commission_rate() == fees.COMMISSION_RATE


def test_buying_power_includes_commission(conn, broker):
    m = SampleMarket()
    price = snap_tick(m.quotes(["005930"])["005930"]["price"], "buy")
    broker.positions["105560"] = {"quantity": 1_000.0, "cost": 1e8}                    # 종목 · 섹터 비중 한도에 걸리지 않게 다른 섹터 보유
    broker.cash = price + fees.estimate("buy", price)["commission"] - 1                # 주문 금액은 되지만 수수료까지는 모자람
    with pytest.raises(trading.TradeError, match="수수료 포함"):
        trading.place_order(conn, symbol="005930", side="buy", order_type="limit", price=price, quantity=1,
                            confirm_live=True, market=m)
    broker.cash += 1
    order = trading.place_order(conn, symbol="005930", side="buy", order_type="limit", price=price, quantity=1,
                                confirm_live=True, market=m)
    assert any(c.startswith("예상 비용") for c in order["checks"])


def test_cost_rules_reach_every_agent():
    text = agents.cost_rules()
    assert "0.23%" in text and "비용 빼면" in text
    assert "trading_costs" not in text                                                   # 도구 결과와 프롬프트는 따로
