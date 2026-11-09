"""투자 탭 — 설정(API 키·한도·PIN), 시세·차트·호가·종목 정보, 시장(지수·랭킹·장 운영), 관심 그룹."""

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.db import get_conn
from app.invest import market, watch
from app.invest.broker import config
from app.invest.deps import Pin, require_pin
from app.invest.market import MarketError
from app.invest.broker.toss import TossClient, TossError, clear_token_cache, match_account

router = APIRouter(prefix="/api/invest", tags=["invest"])
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def _market_call(fn):
    try:
        return fn()
    except MarketError as e:
        raise HTTPException(e.status, str(e)) from None
    except TossError as e:
        raise HTTPException(e.status if e.status in (400, 404) else 502, str(e)) from None


# ---- 설정 ----
@router.get("/status")
def status(conn: Conn) -> dict:
    p = market.provider()
    return {"provider": p.key, "provider_title": p.title, "halted": config.halted(conn),
            "toss_configured": config.toss_configured(), "pin_configured": config.pin_configured(),
            "trading_blockers": config.trading_blockers(), "agent_auto_approve": config.agent_auto_approve(conn),
            "limits": config.limits(conn)}


@router.get("/config")
def read_config(conn: Conn) -> dict:
    return config.snapshot(conn)


class KeysIn(BaseModel):
    TOSS_CLIENT_ID: str | None = None
    TOSS_CLIENT_SECRET: str | None = None
    TOSS_ACCOUNT_SEQ: str | None = None
    DART_API_KEY: str | None = None
    clear: list[str] = Field(default_factory=list)  # 지울 키


@router.put("/config/keys")
def save_keys(body: KeysIn, conn: Conn, pin: Pin = None) -> dict:
    require_pin(pin, required=False)  # PIN 을 정해 두면 다른 기기가 계좌·키를 바꾸지 못하게
    values: dict[str, str | None] = {k: v for k, v in body.model_dump(exclude={"clear"}).items() if v}
    values.update({k: None for k in body.clear})
    try:
        config.save_keys(values)
    except config.ConfigError as e:
        raise HTTPException(400, str(e)) from None
    if set(values) != {"DART_API_KEY"}:  # OpenDART 키만 바꿀 때는 토스 토큰을 건드리지 않는다
        clear_token_cache()
        market.clear_cache()
    return config.snapshot(conn)


@router.post("/config/test")
def test_connection() -> dict:
    """토큰 발급 + 계좌 목록으로 연결을 확인한다. 계좌 번호는 끝 4자리만 보여준다.
    selected_seq = 지금 설정(TOSS_ACCOUNT_SEQ)으로 주문·조회에 쓰일 계좌 순번, 못 찾으면 account_error."""
    client = TossClient()
    try:
        client.token(force=True)
        accounts = client.accounts()
    except TossError as e:
        raise HTTPException(502, str(e)) from None
    selected, account_error = None, None
    try:
        selected = match_account(accounts, client.account_seq)
    except TossError as e:
        account_error = str(e)
    return {"ok": True, "selected_seq": selected, "account_error": account_error, "accounts": [
        {"account_seq": a.get("accountSeq"), "account_type": a.get("accountType"),
         "account_no": f"••••{str(a.get('accountNo') or '')[-4:]}"} for a in accounts]}


class TradingIn(BaseModel):
    limits: dict[str, float] | None = None
    agent_auto_approve: bool | None = None
    halted: bool | None = None


@router.put("/config/trading")
def save_trading(body: TradingIn, conn: Conn, pin: Pin = None) -> dict:
    only_halt = body.halted is True and body.limits is None and body.agent_auto_approve is None
    if not only_halt:  # 전체 중지 켜기는 PIN 없이도 (더 안전해지는 방향)
        require_pin(pin, required=False)
    try:
        config.save_trading(conn, limits_=body.limits, agent_auto=body.agent_auto_approve, halt=body.halted)
    except config.ConfigError as e:
        raise HTTPException(400, str(e)) from None
    return config.snapshot(conn)


# ---- 시세·종목 ----
@router.get("/search")
def search(conn: Conn, q: str = "", limit: Annotated[int, Query(ge=1, le=50)] = 20) -> list[dict]:
    p = market.provider()
    return _market_call(lambda: p.search(conn, q, limit))


@router.get("/stocks/{symbol}")
def stock(symbol: str) -> dict:
    p = market.provider()
    return _market_call(lambda: market.snapshot(p, symbol))


@router.get("/stocks/{symbol}/candles")
def candles(symbol: str, interval: Literal["1d", "1m"] = "1d",
            count: Annotated[int, Query(ge=1, le=600)] = 200) -> dict:
    p = market.provider()
    return {"symbol": symbol, "interval": interval, "provider": p.key,
            "candles": _market_call(lambda: p.candles(symbol, interval, count))}


@router.get("/stocks/{symbol}/orderbook")
def orderbook(symbol: str) -> dict:
    p = market.provider()
    return {"provider": p.key, **_market_call(lambda: p.orderbook(symbol))}


@router.get("/stocks/{symbol}/investor")
def investor(symbol: str, count: Annotated[int, Query(ge=1, le=60)] = 10) -> list[dict]:
    p = market.provider()
    return _market_call(lambda: p.investor_trading(symbol, count))


@router.get("/market")
def market_overview() -> dict:
    p = market.provider()
    return {"provider": p.key, "indicators": _market_call(p.indicators), "session": _market_call(p.session)}


@router.get("/rankings")
def rankings(conn: Conn, kind: Literal["amount", "volume", "gainers", "losers"] = "amount",
             count: Annotated[int, Query(ge=1, le=50)] = 20) -> list[dict]:
    p = market.provider()
    return _market_call(lambda: p.rankings(kind, count, conn=conn))


# ---- 관심 그룹·관심 종목 (토스 관심 그룹은 Open API 에 없어 앱 안에서 관리) ----
def _watch(fn):
    try:
        return fn()
    except watch.WatchError as e:
        raise HTTPException(e.status, str(e)) from None


@router.get("/watchlist")
def watchlist(conn: Conn) -> list[dict]:
    """모든 그룹의 관심 종목 (중복 없이) + 현재가."""
    return watch.flat(conn)


class WatchIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    name: str = ""
    market: str = ""
    security_type: str = ""
    group_id: int | None = None  # 없으면 기본(첫) 그룹


@router.post("/watchlist", status_code=201)
def add_watch(body: WatchIn, conn: Conn) -> list[dict]:
    _watch(lambda: watch.add(conn, body.symbol, body.group_id, body.name, body.market, body.security_type))
    return watch.flat(conn)


@router.delete("/watchlist/{symbol}")
def remove_watch(symbol: str, conn: Conn, group_id: int | None = None) -> list[dict]:
    """group_id 가 없으면 모든 그룹에서 뺀다."""
    watch.remove(conn, symbol, group_id)
    return watch.flat(conn)


@router.get("/watch-groups")
def watch_groups(conn: Conn) -> list[dict]:
    return watch.groups(conn)


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=30)


@router.post("/watch-groups", status_code=201)
def create_group(body: GroupIn, conn: Conn) -> list[dict]:
    _watch(lambda: watch.create_group(conn, body.name))
    return watch.groups(conn)


@router.patch("/watch-groups/{group_id}")
def rename_group(group_id: int, body: GroupIn, conn: Conn) -> list[dict]:
    _watch(lambda: watch.rename_group(conn, group_id, body.name))
    return watch.groups(conn)


@router.delete("/watch-groups/{group_id}")
def delete_group(group_id: int, conn: Conn) -> list[dict]:
    _watch(lambda: watch.delete_group(conn, group_id))
    return watch.groups(conn)
