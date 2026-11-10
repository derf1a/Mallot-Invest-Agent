"""주문·계좌 — 토스증권 실계좌(TossAccount) 하나. 모의 투자는 없다.

모든 주문(수동·에이전트)은 place_order 를 지나며, 그 앞에서 코드가 검사한다:
  전체 중지 → 주문 조건(토스 키·PIN) → 수동 주문 확인 → 국내(원화) 종목만 → 수량·가격 형식 → 건당 한도 → 하루 한도
  → 매수 가능 금액 / 판매 가능 수량 → 매수 후 종목 비중 한도.
에이전트 주문은 기본으로 승인 대기(pending_approval) — 사용자(관리자)가 승인해야 제출된다. 실험실에서 '승인 생략'을 켜면 바로 제출.
"""

import json
import math
import sqlite3
import uuid
from datetime import date

from app.invest.broker import config
from app.invest.broker.toss import TossClient, TossError, TossUncertain, num
from app.invest.marketdata import market as market_mod
from app.invest.marketdata.market import MarketError, SampleMarket, TossMarket, tick_size

OPEN = ("open", "partial")
TOSS_STATUS = {"PENDING": "open", "PENDING_CANCEL": "open", "PENDING_REPLACE": "open", "CANCEL_REJECTED": "open",
               "REPLACE_REJECTED": "open", "PARTIAL_FILLED": "partial", "FILLED": "filled", "CANCELED": "cancelled",
               "REPLACED": "cancelled", "REJECTED": "rejected"}


class TradeError(Exception):
    def __init__(self, message: str, status: int = 400, checks: list[str] | None = None):
        super().__init__(message)
        self.status = status
        self.checks = checks or []


def order_dict(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["checks"] = json.loads(out.pop("checks_json") or "[]")
    out["amount"] = out["quantity"] * (out["price"] or out["ref_price"] or 0)
    return out


def get_order(conn: sqlite3.Connection, order_id: int) -> dict:
    row = conn.execute("SELECT * FROM invest_orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        raise TradeError("주문을 찾을 수 없습니다", 404)
    return order_dict(row)


def _set(conn: sqlite3.Connection, order_id: int, **fields) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE invest_orders SET {cols}, updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime') "
                 "WHERE id = ?", (*fields.values(), order_id))
    conn.commit()


# ---- 토스증권 계좌 ----
class TossAccount:
    def __init__(self, conn: sqlite3.Connection, market: SampleMarket | TossMarket, client: TossClient | None = None):
        self.conn = conn
        self.market = market
        self.client = client or TossClient()

    def _call(self, fn):
        try:
            return fn()
        except TossError as e:
            raise TradeError(str(e), 502) from None

    def buying_power(self) -> float:
        return self._call(lambda: self.client.buying_power("KRW")) or 0.0

    def sellable(self, symbol: str) -> float:
        return self._call(lambda: self.client.sellable_quantity(symbol)) or 0.0

    def avg_price(self, symbol: str) -> float | None:
        """지금 보유 평단 — 매도 주문에 같이 적어 거래 기록의 실현 손익을 낸다. 못 받으면 None (주문은 막지 않는다)."""
        try:
            items = self.client.holdings().get("items") or []
        except Exception:  # noqa: BLE001 — 기록용 값이라 어떤 실패도 주문을 막으면 안 된다
            return None
        return next((num(i.get("averagePurchasePrice")) for i in items if i.get("symbol") == symbol), None)

    def summary(self) -> dict:
        self.sync()
        raw = self._call(self.client.holdings)
        holdings = []
        for i in raw.get("items") or []:
            q, avg, last = num(i.get("quantity")) or 0, num(i.get("averagePurchasePrice")), num(i.get("lastPrice"))
            value = q * (last or 0)
            cost = q * (avg or 0)
            holdings.append({"symbol": i.get("symbol"), "name": i.get("name") or "", "quantity": q, "avg_price": avg,
                             "last_price": last, "value": value, "cost": cost, "pnl": value - cost,
                             "pnl_rate": (last / avg - 1) if last and avg else None, "currency": i.get("currency")})
        bp = self.buying_power()
        return _totals(bp, holdings)

    def submit(self, order: dict) -> None:
        client_id = f"mallot-{order['id']}-{uuid.uuid4().hex[:8]}"
        try:
            result = self.client.create_order(
                symbol=order["symbol"], side=order["side"].upper(), order_type=order["order_type"].upper(),
                quantity=order["quantity"], price=order["price"], client_order_id=client_id)
        except TossUncertain as e:
            _set(self.conn, order["id"], status="failed",
                 error=f"접수 여부를 확인하지 못했습니다 — 토스증권 앱에서 체결 여부를 꼭 확인하세요 ({e})")
            return
        except TossError as e:
            _set(self.conn, order["id"], status="rejected", error=str(e))
            return
        _set(self.conn, order["id"], status="open", broker_order_id=str(result["orderId"]))
        self._sync_one(get_order(self.conn, order["id"]))

    def _sync_one(self, order: dict) -> None:
        if not order.get("broker_order_id"):
            return
        try:
            r = self.client.order(order["broker_order_id"])
        except TossError:
            return
        ex = r.get("execution") or {}
        status = TOSS_STATUS.get(r.get("status"), order["status"])
        _set(self.conn, order["id"], status=status, filled_quantity=num(ex.get("filledQuantity")) or 0,
             avg_fill_price=num(ex.get("averageFilledPrice")), filled_at=ex.get("filledAt"))

    def sync(self) -> None:
        for r in self.conn.execute("SELECT * FROM invest_orders WHERE status IN ('open', 'partial') "
                                   "AND broker_order_id IS NOT NULL").fetchall():
            self._sync_one(order_dict(r))

    def cancel(self, order: dict) -> None:
        if not order.get("broker_order_id"):
            _set(self.conn, order["id"], status="cancelled")
            return
        self._call(lambda: self.client.cancel_order(order["broker_order_id"]))
        self._sync_one(order)


def _totals(cash: float, holdings: list[dict]) -> dict:
    """cash = 매수 가능 금액(원화). 비중·평가액은 원화 종목만."""
    krw = [h for h in holdings if h["currency"] == "KRW"]
    value = sum(h["value"] for h in krw)
    cost = sum(h["cost"] for h in krw)
    total = cash + value
    for h in holdings:
        h["weight"] = h["value"] / total if total and h["currency"] == "KRW" else None
    return {"cash": cash, "buying_power": cash, "holdings": holdings, "holdings_value": value, "total_value": total,
            "pnl": value - cost, "pnl_rate": (value / cost - 1) if cost else None,
            "foreign_holdings": len(holdings) - len(krw)}


def account(conn: sqlite3.Connection, market=None) -> TossAccount:
    if not config.toss_configured():
        raise TradeError("토스증권 API 키가 없습니다 — 설정 > 투자에서 입력하세요", 409)
    return TossAccount(conn, market or market_mod.provider())


def _today_amount(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(quantity * COALESCE(price, ref_price, 0)), 0) FROM invest_orders "
        "WHERE date(created_at) = ? AND status IN ('pending_approval', 'open', 'partial', 'filled')",
        (date.today().isoformat(),)).fetchone()
    return float(row[0])


def exposure_factor(market, symbol: str) -> float:
    """비중에 쓰는 배수 — 레버리지·인버스는 평가액 × |배수|(2배 ETF 20% = 노출 40%), 일반 종목은 1."""
    try:
        return abs(market.info(symbol).get("leverage") or 1) or 1.0
    except (MarketError, TossError):
        return 1.0


def check_order(conn: sqlite3.Connection, acct: TossAccount, *, symbol: str, side: str,
                quantity: float, unit_price: float, exclude_id: int | None = None) -> list[str]:
    """한도 검사. 통과한 항목 설명 목록을 돌려주고, 어기면 TradeError(422)."""
    lim = config.limits(conn)
    amount = quantity * unit_price
    passed: list[str] = []
    problems: list[str] = []
    if amount > lim["max_order_krw"]:
        problems.append(f"건당 한도 초과: {amount:,.0f}원 > {lim['max_order_krw']:,.0f}원")
    else:
        passed.append(f"건당 한도 {lim['max_order_krw']:,.0f}원 이내 ({amount:,.0f}원)")
    today = _today_amount(conn)
    if exclude_id is not None:
        row = conn.execute("SELECT quantity * COALESCE(price, ref_price, 0) FROM invest_orders WHERE id = ? "
                           "AND status = 'pending_approval'", (exclude_id,)).fetchone()
        today -= float(row[0]) if row else 0
    if today + amount > lim["daily_max_krw"]:
        problems.append(f"하루 한도 초과: 오늘 {today:,.0f}원 + {amount:,.0f}원 > {lim['daily_max_krw']:,.0f}원")
    else:
        passed.append(f"하루 한도 {lim['daily_max_krw']:,.0f}원 이내 (오늘 누적 {today + amount:,.0f}원)")
    if side == "buy":
        bp = acct.buying_power()
        if amount > bp:
            problems.append(f"매수 가능 금액 부족: {amount:,.0f}원 > {bp:,.0f}원")
        else:
            passed.append(f"매수 가능 금액 {bp:,.0f}원 이내")
        summary = acct.summary()
        held = next((h["value"] for h in summary["holdings"] if h["symbol"] == symbol), 0.0)
        total = summary["total_value"]
        factor = exposure_factor(acct.market, symbol)
        weight = (held + amount) * factor / total * 100 if total else 100.0
        what = f"노출 {weight:.1f}%(평가액 × {factor:g}배)" if factor != 1 else f"{weight:.1f}%"
        if weight > lim["max_position_pct"]:
            problems.append(f"종목 비중 한도 초과: 매수 후 {what} > {lim['max_position_pct']:g}%")
        else:
            passed.append(f"매수 후 종목 비중 {what} ≤ {lim['max_position_pct']:g}%")
    else:
        can = acct.sellable(symbol)
        if quantity > can + 1e-9:
            problems.append(f"판매 가능 수량 부족: {quantity:g}주 > {can:g}주")
        else:
            passed.append(f"판매 가능 수량 {can:g}주 이내")
    if problems:
        raise TradeError(" · ".join(problems), 422, checks=passed + [f"✗ {p}" for p in problems])
    return passed


def place_order(conn: sqlite3.Connection, *, symbol: str, side: str, order_type: str, quantity: float,
                price: float | None = None, source: str = "manual", run_id: int | None = None, reason: str = "",
                confirm_live: bool = False, market=None, acct: TossAccount | None = None, auto_submit: bool = False) -> dict:
    """주문 하나 — 검사 → 기록 → (승인 대기가 아니면) 토스로 제출. 만들어진 주문을 돌려준다.
    auto_submit: 에이전트 주문을 승인 없이 바로 낸다 — 사용자가 PIN 으로 켠 자동 운용(autopilot) 세션만 쓴다."""
    if config.halted(conn):
        raise TradeError("전체 주문 중지 중입니다 (설정 > 투자에서 해제)", 409)
    blockers = config.trading_blockers()
    if blockers:
        raise TradeError("주문할 수 없습니다: " + " · ".join(blockers), 409)
    market = market or market_mod.provider()
    acct = acct or account(conn, market=market)
    if side not in ("buy", "sell"):
        raise TradeError("주문 방향은 buy 또는 sell 입니다")
    if order_type not in ("market", "limit"):
        raise TradeError("주문 유형은 market(시장가) 또는 limit(지정가) 입니다")
    if source == "manual" and not confirm_live:
        raise TradeError("실계좌 주문입니다 — 확인(confirm_live)이 필요합니다", 428)
    try:
        info = market.info(symbol)
        quote = market.quotes([symbol]).get(symbol) or {}
    except MarketError as e:
        raise TradeError(str(e), e.status) from None
    if (info.get("currency") or "KRW") != "KRW":
        raise TradeError("해외 주식 주문은 아직 지원하지 않습니다 (국내 주식만)")
    if info.get("suspended"):
        raise TradeError("거래 정지 종목입니다")
    if not quantity or quantity <= 0 or not float(quantity).is_integer():
        raise TradeError("수량은 1주 이상의 정수입니다")
    ref = quote.get("price")
    if order_type == "limit":
        if not price or price <= 0 or not math.isfinite(price):
            raise TradeError("지정가 주문에는 가격이 필요합니다")
        tick = tick_size(price, security_type=info.get("security_type"))
        if abs(price / tick - round(price / tick)) > 1e-9:
            raise TradeError(f"호가 단위({tick:,.0f}원)에 맞지 않는 가격입니다")
    else:
        price = None
    if not ref and order_type == "market":
        raise TradeError("현재가를 알 수 없어 시장가 주문을 낼 수 없습니다", 502)
    unit = price or ref
    require_approval = source == "agent" and not (auto_submit or config.agent_auto_approve(conn))
    try:
        checks = check_order(conn, acct, symbol=symbol, side=side, quantity=quantity, unit_price=unit)
    except TradeError as e:
        if source == "agent":  # 에이전트가 시도한 주문은 거절돼도 기록해 둔다 (토론 기록과 함께 보도록)
            _insert(conn, info, symbol, side, order_type, quantity, price, ref, "rejected", source, run_id,
                    reason, e.checks, error=str(e))
        raise
    status = "pending_approval" if require_approval else "open"
    basis = acct.avg_price(symbol) if side == "sell" and hasattr(acct, "avg_price") else None
    order_id = _insert(conn, info, symbol, side, order_type, quantity, price, ref, status, source, run_id,
                       reason, checks, basis=basis)
    if not require_approval:
        acct.submit(get_order(conn, order_id))
    return get_order(conn, order_id)


def _insert(conn, info, symbol, side, order_type, quantity, price, ref, status, source, run_id, reason, checks,
            error=None, basis=None) -> int:
    cur = conn.execute(
        "INSERT INTO invest_orders (symbol, name, currency, side, order_type, quantity, price, ref_price, status, "
        "source, run_id, reason, checks_json, error, basis_price) VALUES (?, ?, 'KRW', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (symbol, info.get("name") or "", side, order_type, float(quantity), price, ref, status, source, run_id,
         reason[:2000], json.dumps(checks, ensure_ascii=False), error, basis))
    conn.commit()
    return cur.lastrowid


def approve(conn: sqlite3.Connection, order_id: int, *, confirm_live: bool = False, market=None) -> dict:
    """승인 대기(에이전트 주문) → 다시 검사하고 토스로 제출."""
    order = get_order(conn, order_id)
    if order["status"] != "pending_approval":
        raise TradeError(f"승인 대기 주문이 아닙니다 (현재: {order['status']})", 409)
    if config.halted(conn):
        raise TradeError("전체 주문 중지 중입니다", 409)
    blockers = config.trading_blockers()
    if blockers:
        raise TradeError("주문할 수 없습니다: " + " · ".join(blockers), 409)
    if not confirm_live:
        raise TradeError("실계좌 주문입니다 — 확인(confirm_live)이 필요합니다", 428)
    market = market or market_mod.provider()
    acct = account(conn, market=market)
    quote = market.quotes([order["symbol"]]).get(order["symbol"]) or {}
    unit = order["price"] or quote.get("price") or order["ref_price"]
    checks = check_order(conn, acct, symbol=order["symbol"], side=order["side"], quantity=order["quantity"],
                         unit_price=unit, exclude_id=order_id)
    _set(conn, order_id, status="open", ref_price=quote.get("price") or order["ref_price"],
         checks_json=json.dumps(checks, ensure_ascii=False))
    acct.submit(get_order(conn, order_id))
    return get_order(conn, order_id)


def reject(conn: sqlite3.Connection, order_id: int) -> dict:
    order = get_order(conn, order_id)
    if order["status"] != "pending_approval":
        raise TradeError(f"승인 대기 주문이 아닙니다 (현재: {order['status']})", 409)
    _set(conn, order_id, status="rejected", error="사용자가 거절함")
    return get_order(conn, order_id)


def cancel(conn: sqlite3.Connection, order_id: int, market=None) -> dict:
    order = get_order(conn, order_id)
    if order["status"] == "pending_approval":
        return reject(conn, order_id)
    if order["status"] not in OPEN:
        raise TradeError(f"취소할 수 없는 상태입니다 ({order['status']})", 409)
    account(conn, market=market).cancel(order)
    return get_order(conn, order_id)


def list_orders(conn: sqlite3.Connection, status: str = "all", limit: int = 100, run_id: int | None = None,
                market=None) -> list[dict]:
    if config.toss_configured():
        try:
            account(conn, market=market).sync()
        except (TradeError, MarketError):
            pass
    where, args = ["1 = 1"], []
    if status == "open":
        where.append("status IN ('pending_approval', 'open', 'partial')")
    elif status == "closed":
        where.append("status NOT IN ('pending_approval', 'open', 'partial')")
    if run_id is not None:
        where = ["run_id = ?"]
        args = [run_id]
    rows = conn.execute(f"SELECT * FROM invest_orders WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
                        (*args, limit)).fetchall()
    return [order_dict(r) for r in rows]
