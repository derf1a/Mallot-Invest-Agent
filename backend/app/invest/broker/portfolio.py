"""내 투자현황 — 토스 실계좌(국내·해외 보유, 원화·달러 예수금) + 다른 계좌 보유(수기)를 원화로 합친 전체 자산.

포트폴리오 매니저 에이전트(get_portfolio 도구)와 포트폴리오 화면이 쓴다. 해외 주식·달러 예수금은 토스 환율(참고용 표시 환율)로 환산.
다른 계좌 보유는 토스 밖(다른 증권사·연금 계좌 등)에 가진 종목을 사용자가 직접 적어 둔 것 — 현재가를 못 구하면 평단으로 평가한다.
indicator_rows: AI Trading 현황 서랍의 보유 종목 지표 표.
"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

from app.invest.analysis import screening
from app.invest.broker import trading
from app.invest.broker.toss import TossError, num
from app.invest.marketdata import market as market_mod
from app.invest.marketdata.market import MarketError


INDICATOR_WORKERS = 4


class PortfolioError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---- 다른 계좌 보유 (수기) ----
def external_rows(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM invest_external_holdings ORDER BY account_name, id")]


def external(conn: sqlite3.Connection, market=None, fx: float | None = None) -> list[dict]:
    """다른 계좌 보유 + 현재가·평가액(원화)."""
    rows = external_rows(conn)
    if not rows:
        return []
    p = market or market_mod.provider()
    try:
        quotes = p.quotes(sorted({r["symbol"] for r in rows}))
    except (MarketError, TossError):
        quotes = {}
    if fx is None and any(r["currency"] == "USD" for r in rows):
        fx = _fx(p)
    for r in rows:
        last = (quotes.get(r["symbol"]) or {}).get("price")
        r["last_price"] = last
        r["priced"] = last is not None
        unit = last if last is not None else (r["avg_price"] or 0)
        r["value"] = r["quantity"] * unit
        r["value_krw"] = r["value"] * (fx or 0) if r["currency"] == "USD" else r["value"]
        r["pnl_rate"] = (unit / r["avg_price"] - 1) if r["avg_price"] and last is not None else None
    return rows


def add_external(conn: sqlite3.Connection, *, symbol: str, quantity: float, account_name: str = "", name: str = "",
                 avg_price: float | None = None, memo: str = "", market=None) -> int:
    symbol = symbol.strip()
    if not symbol:
        raise PortfolioError("종목을 고르세요")
    if not quantity or quantity <= 0:
        raise PortfolioError("수량은 0보다 커야 합니다")
    p = market or market_mod.provider()
    security_type, currency = "", "KRW"
    try:
        info = p.info(symbol)
        name = name or info.get("name") or ""
        security_type, currency = info.get("security_type") or "", info.get("currency") or "KRW"
    except (MarketError, TossError):
        if not name:
            raise PortfolioError(f"종목을 찾을 수 없습니다: {symbol}", 404) from None
    cur = conn.execute(
        "INSERT INTO invest_external_holdings (account_name, symbol, name, security_type, currency, quantity, avg_price, memo) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (account_name.strip()[:40], symbol, name[:60], security_type, currency, float(quantity),
         float(avg_price) if avg_price else None, memo.strip()[:200]))
    conn.commit()
    return cur.lastrowid


def update_external(conn: sqlite3.Connection, row_id: int, fields: dict) -> None:
    if conn.execute("SELECT 1 FROM invest_external_holdings WHERE id = ?", (row_id,)).fetchone() is None:
        raise PortfolioError("항목을 찾을 수 없습니다", 404)
    allowed = {k: v for k, v in fields.items() if k in ("account_name", "quantity", "avg_price", "memo") and v is not None}
    if "quantity" in allowed and float(allowed["quantity"]) <= 0:
        raise PortfolioError("수량은 0보다 커야 합니다")
    if allowed:
        cols = ", ".join(f"{k} = ?" for k in allowed)
        conn.execute(f"UPDATE invest_external_holdings SET {cols}, updated_at = datetime('now', 'localtime') WHERE id = ?",
                     (*allowed.values(), row_id))
        conn.commit()


def delete_external(conn: sqlite3.Connection, row_id: int) -> None:
    conn.execute("DELETE FROM invest_external_holdings WHERE id = ?", (row_id,))
    conn.commit()


# ---- 전체 자산 ----
def _fx(p) -> float | None:
    try:
        return p.usd_krw()
    except (MarketError, TossError, AttributeError):
        return None


def _security_types(conn: sqlite3.Connection, p, symbols: list[str]) -> dict[str, str]:
    """종목 유형(STOCK/ETF): 검색용 종목 목록에서, 없으면 시세 제공자의 종목 정보(토스는 1시간 캐시)."""
    if not symbols:
        return {}
    types = {r[0]: r[1] for r in conn.execute(
        f"SELECT symbol, security_type FROM invest_universe WHERE symbol IN ({','.join('?' * len(symbols))})", symbols)}
    for sym in symbols:
        if not types.get(sym):
            try:
                types[sym] = p.info(sym).get("security_type") or ""
            except (MarketError, TossError):
                types[sym] = ""
    return types


def _add_cost_and_type(conn: sqlite3.Connection, p, positions: list[dict], fx: float | None) -> None:
    """포트폴리오 화면용: 매입 원금·평가 손익(원화)과 종목 유형. 평단을 모르거나 현재가가 없으면 손익은 None."""
    types = _security_types(conn, p, sorted({x["symbol"] for x in positions if not x.get("security_type")}))
    for x in positions:
        x["security_type"] = x.get("security_type") or types.get(x["symbol"], "")
        rate = 1.0 if x["currency"] == "KRW" else (fx or 0)
        x["cost_krw"] = x["quantity"] * x["avg_price"] * rate if x["avg_price"] else None
        priced = x.get("priced", x["last_price"] is not None)
        x["pnl_krw"] = x["value_krw"] - x["cost_krw"] if x["cost_krw"] is not None and priced else None
        x["exposure_krw"] = x["value_krw"] * (trading.exposure_factor(p, x["symbol"]) if x["security_type"] in market_mod.ETP_TYPES else 1.0)


def overview(conn: sqlite3.Connection, market=None, candidates: list[str] | None = None) -> dict:
    """토스 실계좌(국내·해외·예수금) + 다른 계좌 보유를 원화로 합친 내 투자현황."""
    p = market or market_mod.provider()
    acct = trading.account(conn, market=p)
    raw = acct._call(acct.client.holdings)
    fx = _fx(p)
    cash_krw = acct.buying_power()
    try:
        cash_usd = acct.client.buying_power("USD")
    except TossError:
        cash_usd = None

    positions: list[dict] = []
    for i in raw.get("items") or []:
        q, avg, last = num(i.get("quantity")) or 0, num(i.get("averagePurchasePrice")), num(i.get("lastPrice"))
        currency = i.get("currency") or "KRW"
        value = q * (last or 0)
        rate = 1.0 if currency == "KRW" else (fx or 0)
        positions.append({"where": "토스", "symbol": i.get("symbol"), "name": i.get("name") or "", "currency": currency,
                          "quantity": q, "avg_price": avg, "last_price": last, "value": value, "value_krw": value * rate,
                          "pnl_rate": (last / avg - 1) if last and avg else None})
    for r in external(conn, p, fx):
        positions.append({"where": r["account_name"] or "다른 계좌", "symbol": r["symbol"], "name": r["name"],
                          "currency": r["currency"], "quantity": r["quantity"], "avg_price": r["avg_price"],
                          "last_price": r["last_price"], "value": r["value"], "value_krw": r["value_krw"],
                          "pnl_rate": r["pnl_rate"], "priced": r["priced"], "security_type": r["security_type"]})
    _add_cost_and_type(conn, p, positions, fx)

    cash_usd_krw = (cash_usd or 0) * (fx or 0)
    stock_krw = sum(x["value_krw"] for x in positions)
    total = cash_krw + cash_usd_krw + stock_krw
    by_symbol: dict[str, dict] = {}
    for x in positions:
        x["weight"] = x["value_krw"] / total if total else None
        agg = by_symbol.setdefault(x["symbol"], {"symbol": x["symbol"], "name": x["name"], "value_krw": 0.0, "quantity": 0.0})
        agg["value_krw"] += x["value_krw"]
        agg["quantity"] += x["quantity"]
    weights = sorted((a["value_krw"] / total for a in by_symbol.values()), reverse=True) if total else []
    out = {
        "total_assets_krw": round(total), "stock_value_krw": round(stock_krw),
        "cash": {"krw": round(cash_krw), "usd": cash_usd, "usd_in_krw": round(cash_usd_krw)},
        "cash_ratio": round((cash_krw + cash_usd_krw) / total, 4) if total else None,
        "exposure_ratio": round(sum(x["exposure_krw"] for x in positions) / total, 4) if total else None,  # 주식 비중 — 레버리지는 |배수|를 곱해서
        "usd_krw": fx,
        "positions": positions,
        "concentration": {"top1": round(weights[0], 4) if weights else None,
                          "top3": round(sum(weights[:3]), 4) if weights else None,
                          "hhi": round(sum(w * w for w in weights), 4) if weights else None,
                          "count": len(by_symbol)},
        "other_accounts": sorted({x["where"] for x in positions if x["where"] != "토스"}),
    }
    if candidates is not None:
        out["candidate_exposure"] = [{"symbol": c, "held_quantity": by_symbol.get(c, {}).get("quantity", 0.0),
                                      "weight": round(by_symbol[c]["value_krw"] / total, 4) if c in by_symbol and total else 0.0}
                                     for c in candidates]
    return out


def indicator_rows(conn: sqlite3.Connection, p=None) -> dict:
    """AI Trading 현황 서랍의 보유 종목 표 — 보유마다 수량·평단·현재가·비중·손익과 지표(오늘·5일·20일 등락, RSI14, 거래량 배수,
    이동평균 정렬). 지표는 국내 종목만(일봉 60일, screening.metrics 와 같은 계산), 비중 큰 순."""
    p = p or market_mod.provider()
    o = overview(conn, p)
    pos = sorted(o["positions"], key=lambda x: -x["value_krw"])
    kr = list(dict.fromkeys(x["symbol"] for x in pos if str(x["symbol"]).isdigit() and len(str(x["symbol"])) == 6))
    with ThreadPoolExecutor(INDICATOR_WORKERS) as ex:
        ms = dict(zip(kr, ex.map(lambda s: screening.metrics(p, s), kr)))
    rows = []
    for x in pos:
        m = ms.get(x["symbol"]) or {}
        rows.append({k: x.get(k) for k in ("symbol", "name", "where", "quantity", "avg_price", "last_price", "value_krw", "weight",
                                           "pnl_rate", "pnl_krw", "security_type", "currency")}
                    | {k: m.get(k) for k in ("change_1d", "change_5d", "change_20d", "rsi", "volume_ratio", "trend")})
    return {"rows": rows, "total_assets_krw": o["total_assets_krw"], "cash_ratio": o["cash_ratio"], "source": p.key}
