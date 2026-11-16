"""주문·계좌 — 토스증권 실계좌(TossAccount) 하나. 모의 투자는 없다.

모든 주문(수동·에이전트)은 place_order 를 지나며, 그 앞에서 코드가 검사한다:
  전체 중지 → 주문 조건(토스 키·PIN) → 수동 주문 확인 → 국내(원화) 종목만 → 수량·가격 형식 → 건당 한도 → 하루 한도
  → 매수 가능 금액(수수료 포함) / 판매 가능 수량 → (매수) 채권 · 현금성 상품 금지(주식 비중 100%) → 매수 후 종목 비중 한도
  → 매수 후 섹터 비중 한도. 검사 기록 끝에 예상 비용(수수료 · 거래세 — fees.py).
종목 · 섹터 비중 한도는 에이전트(리스크 · 포트폴리오 매니저)가 정한 값(config.risk_limits)을 쓴다.
승인 없이 바로 나가는 에이전트 주문(자동 운용)은 더: 자동 주문 시간(정규장 +5분 ~ 끝 −15분, 마감 동시호가 전) 안에서만,
장 시작 30분 안에는 지정가만(market.auto_order_problem), 같은 종목에 체결 안 된 에이전트 주문이 있으면 새 주문을 내지 않는다.
호가 기준 지정가: price_at(bid 매수 1호가 · ask 매도 1호가 · mid 가운데 · last 현재가) + ticks(호가 단위 칸, + 위 · − 아래)를 주면
코드가 지금 호가에서 가격을 계산한다 — 매수는 bid 근처, 매도는 ask 근처에 걸어 두고 체결을 기다리는 주문.
에이전트 주문은 기본으로 승인 대기(pending_approval) — 사용자(관리자)가 승인해야 제출된다. 실험실에서 '승인 생략'을 켜면 바로 제출.
"""

import json
import math
import sqlite3
import uuid
from datetime import date

from app.invest.broker import config, fees
from app.invest.broker.toss import TossClient, TossError, TossUncertain, num
from app.invest.marketdata import market as market_mod
from app.invest.marketdata.market import MarketError, SampleMarket, TossMarket, tick_size

OPEN = ("open", "partial")
PRICE_AT = {"bid": "매수 1호가", "ask": "매도 1호가", "mid": "호가 가운데", "last": "현재가"}
MAX_TICKS = 20
CHECK_HOURS = True  # 자동 주문 시간 검사 (테스트는 시계를 고정한 것만 켠다)
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


def sectors_of(market) -> dict[str, str]:
    """종목 → 대분류 섹터 (토스 섹터, 6시간 캐시). 못 받으면 빈 dict — 섹터 한도 검사를 건너뛴다."""
    from app.invest.analysis import valuation  # 늦게 — 가치평가 모듈이 무겁고 주문 모듈보다 위에서 쓰인다
    try:
        return {sym: x["sector"] for sym, x in valuation.sector_index(market).items() if x.get("sector")}
    except Exception:  # noqa: BLE001 — 섹터를 몰라도 다른 한도 검사는 그대로 한다
        return {}


def sector_weights(market, holdings: list[dict], total: float) -> dict[str, float]:
    """보유(원화 종목)의 섹터별 비중(%) — 섹터를 모르는 종목은 '기타'."""
    sec = sectors_of(market)
    out: dict[str, float] = {}
    for h in holdings:
        if h.get("currency") not in (None, "KRW") or not total:
            continue
        name = sec.get(h["symbol"], "기타")
        out[name] = out.get(name, 0.0) + h["value"] / total * 100
    return {k: round(v, 1) for k, v in sorted(out.items(), key=lambda x: -x[1])}


def sector_check(market, symbol: str, amount: float, summary: dict, risk: dict, passed: list[str], problems: list[str]) -> None:
    sec = sectors_of(market)
    sector = sec.get(symbol)
    total = summary["total_value"]
    if not sector or not total:
        passed.append("섹터 비중 — 섹터를 알 수 없어 건너뜀")
        return
    held = sum(h["value"] for h in summary["holdings"] if sec.get(h["symbol"]) == sector and h.get("currency") in (None, "KRW"))
    weight = (held + amount) / total * 100
    cap = config.sector_limit(risk, sector)
    if weight > cap:
        problems.append(f"섹터 비중 한도 초과: 매수 후 {sector} {weight:.1f}% > {cap:g}%")
    else:
        passed.append(f"매수 후 섹터 {sector} 비중 {weight:.1f}% ≤ {cap:g}%")


def check_order(conn: sqlite3.Connection, acct: TossAccount, *, symbol: str, side: str,
                quantity: float, unit_price: float, exclude_id: int | None = None, security_type: str | None = None) -> list[str]:
    """한도 검사. 통과한 항목 설명 목록을 돌려주고, 어기면 TradeError(422). 매수 가능 금액은 수수료까지 더해 본다."""
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
    cost = fees.estimate(side, amount, security_type)
    if side == "buy":
        bp = acct.buying_power()
        need = amount + cost["commission"]
        if need > bp:
            problems.append(f"매수 가능 금액 부족: {need:,.0f}원(수수료 포함) > {bp:,.0f}원")
        else:
            passed.append(f"매수 가능 금액 {bp:,.0f}원 이내 (수수료 포함 {need:,.0f}원)")
        try:
            info = acct.market.info(symbol)
        except (MarketError, TossError):
            info = {}
        if config.non_equity(info.get("name") or "", info.get("security_type") or ""):
            problems.append(f"채권 · 현금성 상품은 사지 않습니다 (주식 비중 {config.EQUITY_TARGET_PCT:g}% 원칙): {info.get('name') or symbol}")
        risk = config.risk_limits(conn)
        summary = acct.summary()
        held = next((h["value"] for h in summary["holdings"] if h["symbol"] == symbol), 0.0)
        total = summary["total_value"]
        factor = exposure_factor(acct.market, symbol)
        weight = (held + amount) * factor / total * 100 if total else 100.0
        what = f"노출 {weight:.1f}%(평가액 × {factor:g}배)" if factor != 1 else f"{weight:.1f}%"
        cap = risk["max_position_pct"]
        if weight > cap:
            problems.append(f"종목 비중 한도 초과: 매수 후 {what} > {cap:g}% ({risk['position_set_by']})")
        else:
            passed.append(f"매수 후 종목 비중 {what} ≤ {cap:g}%")
        sector_check(acct.market, symbol, amount, summary, risk, passed, problems)
    else:
        can = acct.sellable(symbol)
        if quantity > can + 1e-9:
            problems.append(f"판매 가능 수량 부족: {quantity:g}주 > {can:g}주")
        else:
            passed.append(f"판매 가능 수량 {can:g}주 이내")
    passed.append(f"예상 비용 {cost['total']:,.0f}원 (수수료 {cost['commission']:,.0f}원"
                  + (f" · 거래세 {cost['tax']:,.0f}원" if side == "sell" else "") + ")")
    if problems:
        raise TradeError(" · ".join(problems), 422, checks=passed + [f"✗ {p}" for p in problems])
    return passed


def resolve_price(market, symbol: str, side: str, price_at: str, ticks: int = 0, security_type: str | None = None) -> tuple[float, str]:
    """호가 기준 지정가 → (가격, 설명). 상한가 · 하한가 안으로 자른다."""
    if price_at not in PRICE_AT:
        raise TradeError("price_at 은 bid(매수 1호가) · ask(매도 1호가) · mid · last 중 하나입니다")
    if not isinstance(ticks, int) or abs(ticks) > MAX_TICKS:
        raise TradeError(f"ticks 는 -{MAX_TICKS}~{MAX_TICKS} 사이 정수입니다")
    try:
        if price_at == "last":
            base = (market.quotes([symbol]).get(symbol) or {}).get("price")
        else:
            book = market.orderbook(symbol)
            bid = (book.get("bids") or [{}])[0].get("price")
            ask = (book.get("asks") or [{}])[0].get("price")
            base = {"bid": bid, "ask": ask, "mid": (bid + ask) / 2 if bid and ask else None}[price_at]
    except (MarketError, TossError) as e:
        raise TradeError(f"호가를 받지 못해 가격을 정할 수 없습니다 ({e})", 502) from None
    if not base:
        raise TradeError(f"{PRICE_AT[price_at]}가 없어 가격을 정할 수 없습니다 (호가 비어 있음)", 502)
    price = market_mod.tick_step(market_mod.snap_tick(base, side, security_type), ticks, security_type)
    note = f"가격: {PRICE_AT[price_at]} {base:,.0f}원" + (f" {ticks:+d}틱" if ticks else "") + f" → {price:,.0f}원"
    try:
        lim = market.price_limits(symbol)
    except (MarketError, TossError):
        lim = {}
    up, low = lim.get("upper"), lim.get("lower")
    if up and price > up:
        price, note = up, note + f" (상한가 {up:,.0f}원으로)"
    if low and price < low:
        price, note = low, note + f" (하한가 {low:,.0f}원으로)"
    if price <= 0:
        raise TradeError("계산한 가격이 0 이하입니다 — ticks 를 줄이세요")
    return price, note


def _open_agent_order(conn: sqlite3.Connection, acct: TossAccount, symbol: str) -> dict | None:
    """이 종목에 아직 체결되지 않은(제출됨 · 일부 체결) 에이전트 주문. 상태를 토스에서 먼저 맞춘다."""
    rows = conn.execute("SELECT * FROM invest_orders WHERE symbol = ? AND source = 'agent' AND status IN ('open', 'partial') "
                        "AND date(created_at) = ?", (symbol, date.today().isoformat())).fetchall()
    for r in rows:
        if hasattr(acct, "_sync_one"):
            acct._sync_one(order_dict(r))
    row = conn.execute("SELECT * FROM invest_orders WHERE symbol = ? AND source = 'agent' AND status IN ('open', 'partial') "
                       "AND date(created_at) = ? ORDER BY id DESC LIMIT 1", (symbol, date.today().isoformat())).fetchone()
    return order_dict(row) if row else None


def place_order(conn: sqlite3.Connection, *, symbol: str, side: str, order_type: str, quantity: float,
                price: float | None = None, source: str = "manual", run_id: int | None = None, reason: str = "",
                confirm_live: bool = False, market=None, acct: TossAccount | None = None, auto_submit: bool = False,
                price_at: str | None = None, ticks: int = 0) -> dict:
    """주문 하나 — 검사 → 기록 → (승인 대기가 아니면) 토스로 제출. 만들어진 주문을 돌려준다.
    auto_submit: 에이전트 주문을 승인 없이 바로 낸다 — 사용자가 PIN 으로 켠 자동 운용(autopilot) 세션만 쓴다.
    price_at · ticks: 호가 기준 지정가 (price 대신 — 주면 order_type 은 limit)."""
    if config.halted(conn):
        raise TradeError("전체 주문 중지 중입니다 (설정 > 투자에서 해제)", 409)
    blockers = config.trading_blockers()
    if blockers:
        raise TradeError("주문할 수 없습니다: " + " · ".join(blockers), 409)
    market = market or market_mod.provider()
    acct = acct or account(conn, market=market)
    if side not in ("buy", "sell"):
        raise TradeError("주문 방향은 buy 또는 sell 입니다")
    if price_at:
        order_type = "limit"
    if order_type not in ("market", "limit"):
        raise TradeError("주문 유형은 market(시장가) 또는 limit(지정가) 입니다")
    if source == "manual" and not confirm_live:
        raise TradeError("실계좌 주문입니다 — 확인(confirm_live)이 필요합니다", 428)
    require_approval = source == "agent" and not (auto_submit or config.agent_auto_approve(conn))
    auto = source == "agent" and not require_approval
    if auto and CHECK_HOURS:
        why = market_mod.auto_order_problem(market, order_type)
        if why:
            raise TradeError(why, 409)
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
    price_note = None
    if price_at:
        price, price_note = resolve_price(market, symbol, side, price_at, ticks, info.get("security_type"))
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
    if auto:
        pending = _open_agent_order(conn, acct, symbol)
        if pending:
            raise TradeError(f"이 종목에 아직 체결되지 않은 주문이 있습니다 (#{pending['id']} {pending['side']} "
                             f"{pending['quantity']:g}주 @ {pending['price'] or 0:,.0f}원) — 체결되거나 취소된 뒤에 다시", 409)
    try:
        checks = check_order(conn, acct, symbol=symbol, side=side, quantity=quantity, unit_price=unit,
                             security_type=info.get("security_type"))
        if price_note:
            checks = [price_note, *checks]
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
    try:
        security_type = market.info(order["symbol"]).get("security_type")
    except (MarketError, TossError):
        security_type = None  # 모르면 주식으로 (비용을 크게 잡는 쪽)
    checks = check_order(conn, acct, symbol=order["symbol"], side=order["side"], quantity=order["quantity"],
                         unit_price=unit, exclude_id=order_id, security_type=security_type)
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


def cancel_open_agent_orders(conn: sqlite3.Connection, market=None) -> list[dict]:
    """오늘 체결 안 된 에이전트 주문(제출됨 · 일부 체결)을 모두 취소한다 — 자동 주문 시간이 끝나면(마감 동시호가 전) 운용이 부른다.
    취소에 실패한 주문은 error 에 남기고 넘어간다. → 취소를 시도한 주문들(최신 상태)"""
    rows = conn.execute("SELECT * FROM invest_orders WHERE source = 'agent' AND status IN ('open', 'partial') "
                        "AND broker_order_id IS NOT NULL AND date(created_at) = ?", (date.today().isoformat(),)).fetchall()
    if not rows:
        return []
    acct = account(conn, market=market)
    out = []
    for r in rows:
        order = order_dict(r)
        acct._sync_one(order)
        order = get_order(conn, order["id"])
        if order["status"] not in OPEN:
            continue
        try:
            acct.cancel(order)
        except TradeError as e:
            _set(conn, order["id"], error=f"자동 주문 시간 끝 — 취소 실패: {e}")
        out.append(get_order(conn, order["id"]))
    return out


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
