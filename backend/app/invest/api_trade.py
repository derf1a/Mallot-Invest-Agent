"""투자 탭 — 토스증권 실계좌(보유·매수 가능 금액), 내 투자현황(다른 계좌 수기 포함), 주문, 승인·거절·취소."""

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.db import get_conn
from app.invest.broker import config, portfolio, trading
from app.invest.broker.trading import TradeError
from app.invest.deps import Pin, require_pin
from app.invest.marketdata import market
from app.invest.marketdata.market import MarketError

router = APIRouter(prefix="/api/invest", tags=["invest"])
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def _trade(fn):
    try:
        return fn()
    except TradeError as e:
        raise HTTPException(e.status, {"message": str(e), "checks": e.checks} if e.checks else str(e)) from None
    except MarketError as e:
        raise HTTPException(e.status, str(e)) from None


@router.get("/account")
def account(conn: Conn) -> dict:
    return _trade(lambda: trading.account(conn).summary())


@router.get("/portfolio")
def portfolio_overview(conn: Conn) -> dict:
    """내 투자현황 — 토스(국내·해외·예수금) + 다른 계좌 보유를 원화로 합친 전체 자산."""
    return _trade(lambda: portfolio.overview(conn))


@router.get("/holdings/indicators")
def holding_indicators(conn: Conn) -> dict:
    """보유 종목마다 손익·비중과 지표(오늘·5일·20일 등락, RSI, 거래량 배수, 이동평균 정렬) — AI Trading 현황 서랍."""
    return _trade(lambda: portfolio.indicator_rows(conn))


@router.get("/external-holdings")
def external_holdings(conn: Conn) -> list[dict]:
    return portfolio.external(conn)


class ExternalIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    quantity: float = Field(gt=0)
    account_name: str = Field(default="", max_length=40)
    name: str = Field(default="", max_length=60)
    avg_price: float | None = Field(default=None, gt=0)
    memo: str = Field(default="", max_length=200)


class ExternalPatch(BaseModel):
    quantity: float | None = Field(default=None, gt=0)
    account_name: str | None = Field(default=None, max_length=40)
    avg_price: float | None = Field(default=None, gt=0)
    memo: str | None = Field(default=None, max_length=200)


def _ext(fn):
    try:
        return fn()
    except portfolio.PortfolioError as e:
        raise HTTPException(e.status, str(e)) from None


@router.post("/external-holdings", status_code=201)
def add_external(body: ExternalIn, conn: Conn, pin: Pin = None) -> list[dict]:
    require_pin(pin, required=False)  # 에이전트 판단 근거가 되는 자료
    _ext(lambda: portfolio.add_external(conn, **body.model_dump()))
    return portfolio.external(conn)


@router.patch("/external-holdings/{row_id}")
def update_external(row_id: int, body: ExternalPatch, conn: Conn, pin: Pin = None) -> list[dict]:
    require_pin(pin, required=False)
    _ext(lambda: portfolio.update_external(conn, row_id, body.model_dump()))
    return portfolio.external(conn)


@router.delete("/external-holdings/{row_id}")
def delete_external(row_id: int, conn: Conn, pin: Pin = None) -> list[dict]:
    require_pin(pin, required=False)
    portfolio.delete_external(conn, row_id)
    return portfolio.external(conn)


@router.get("/order-info/{symbol}")
def order_info(symbol: str, conn: Conn) -> dict:
    """주문 화면: 매수 가능 금액, 판매 가능 수량, 한도, 호가 단위."""
    def load() -> dict:
        acct = trading.account(conn)
        snap = market.snapshot(market.provider(), symbol)
        return {"buying_power": acct.buying_power(), "sellable": acct.sellable(symbol),
                "price": snap["price"], "tick": snap["tick"], "currency": snap["currency"],
                "upper_limit": snap["upper_limit"], "lower_limit": snap["lower_limit"],
                "limits": config.limits(conn), "halted": config.halted(conn)}
    return _trade(load)


class OrderIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit"] = "limit"
    quantity: float = Field(gt=0)
    price: float | None = None
    confirm_live: bool = False


@router.post("/orders", status_code=201)
def create_order(body: OrderIn, conn: Conn, pin: Pin = None) -> dict:
    require_pin(pin)
    return _trade(lambda: trading.place_order(conn, symbol=body.symbol.strip(), side=body.side,
                                              order_type=body.order_type, quantity=body.quantity, price=body.price,
                                              confirm_live=body.confirm_live))


@router.get("/orders")
def orders(conn: Conn, status: Literal["all", "open", "closed"] = "all", run_id: int | None = None,
           limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict]:
    return trading.list_orders(conn, status, limit, run_id)


class ConfirmIn(BaseModel):
    confirm_live: bool = False


@router.post("/orders/{order_id}/approve")
def approve(order_id: int, body: ConfirmIn, conn: Conn, pin: Pin = None) -> dict:
    require_pin(pin)
    return _trade(lambda: trading.approve(conn, order_id, confirm_live=body.confirm_live))


@router.post("/orders/{order_id}/reject")
def reject(order_id: int, conn: Conn) -> dict:
    return _trade(lambda: trading.reject(conn, order_id))


@router.post("/orders/{order_id}/cancel")
def cancel(order_id: int, conn: Conn) -> dict:
    return _trade(lambda: trading.cancel(conn, order_id))
