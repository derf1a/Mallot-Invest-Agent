"""매매 비용 — 토스증권 국내 주식 수수료와 매도 거래세. 주문 검사 · 매도 점검 · 에이전트 프롬프트가 같이 쓴다.

토스 체결 기록(invest_broker_fills 의 commission · tax)과 맞춰 본 값:
- 수수료: 매수 · 매도 모두 체결 금액의 0.015% (원 미만 버림이라 기록에는 0.013~0.015%로 보인다).
- 거래세(증권거래세 + 농특세): 매도만, 주식 0.20%. 국내 ETF · ETN 은 0%.
왕복(사고 다시 팔기) 비용 ≈ 주식 0.23%, ETF 0.03% — 이보다 작게 오를 것을 노리는 매매는 비용으로 손해다.
요율이 바뀌면 .env 의 MIA_COMMISSION_RATE · MIA_SELL_TAX_RATE (비율, 예 0.00015) 로 덮어쓴다.
종목 종류를 모르면 주식으로 본다 (비용을 크게 잡는 쪽).
"""

import math
import os
import sqlite3

COMMISSION_RATE = 0.00015
SELL_TAX_RATE = 0.002
TAX_FREE = {"ETF", "ETN"}


def _rate(env: str, default: float) -> float:
    try:
        v = float(os.environ.get(env, ""))
    except ValueError:
        return default
    return v if 0 <= v < 0.05 else default


def commission_rate() -> float:
    return _rate("MIA_COMMISSION_RATE", COMMISSION_RATE)


def sell_tax_rate(security_type: str | None = None) -> float:
    return 0.0 if security_type in TAX_FREE else _rate("MIA_SELL_TAX_RATE", SELL_TAX_RATE)


def estimate(side: str, amount: float, security_type: str | None = None) -> dict:
    """주문 한 건의 예상 비용 (원 미만 버림 — 토스 기록과 같게). → {commission, tax, total}"""
    commission = math.floor(amount * commission_rate())
    tax = math.floor(amount * sell_tax_rate(security_type)) if side == "sell" else 0
    return {"commission": commission, "tax": tax, "total": commission + tax}


def round_trip(security_type: str | None = None) -> float:
    """사고 다시 팔 때 드는 비용 비율 (수수료 두 번 + 매도 거래세)."""
    return 2 * commission_rate() + sell_tax_rate(security_type)


def net_pnl_rate(avg_price: float, price: float, security_type: str | None = None) -> float | None:
    """지금 팔면 남는 손익률 — 산 수수료와 팔 수수료 · 거래세를 뺀다."""
    if not avg_price or not price:
        return None
    cost = avg_price * (1 + commission_rate())
    proceeds = price * (1 - commission_rate() - sell_tax_rate(security_type))
    return round(proceeds / cost - 1, 4)


def breakeven_price(avg_price: float, security_type: str | None = None) -> float:
    """비용까지 빼고 본전이 되는 매도가."""
    return avg_price * (1 + commission_rate()) / (1 - commission_rate() - sell_tax_rate(security_type))


def security_types(conn: sqlite3.Connection, symbols: list[str]) -> dict[str, str]:
    """종목 목록(invest_universe, 하루 한 번 갱신)에서 종목 종류. 없으면 빠진다 (주식으로 본다)."""
    if not symbols:
        return {}
    marks = ",".join("?" * len(symbols))
    try:
        rows = conn.execute(f"SELECT symbol, security_type FROM invest_universe WHERE symbol IN ({marks})", symbols).fetchall()
    except sqlite3.Error:
        return {}
    return {r[0]: r[1] for r in rows if r[1]}


def summary() -> dict:
    """에이전트 도구용 비용 요약."""
    return {"commission_rate_pct": round(commission_rate() * 100, 4), "sell_tax_rate_pct_stock": round(sell_tax_rate() * 100, 3),
            "sell_tax_rate_pct_etf": 0.0, "round_trip_pct_stock": round(round_trip() * 100, 3),
            "round_trip_pct_etf": round(round_trip("ETF") * 100, 3),
            "note": "수수료는 매수 · 매도 모두, 거래세는 매도만(국내 ETF · ETN 은 없음). 기대 수익이 왕복 비용보다 작으면 사고팔지 마세요."}
