"""거래 기록 — 체결된 매수 · 매도마다 금액 · 지금 가치 · 손익과, 그 주문을 낳은 토론(입장판 · 에이전트 의견 · 트레이더 판단).

- 범위: 앱이 낸 주문(invest_orders) + 토스 계좌의 지난 체결(invest_broker_fills — 앱을 쓰기 전 · 토스 앱에서 직접 한 거래).
  토스 체결은 import_broker 가 끝난 주문(GET /orders?status=CLOSED)을 쪽마다 받아 쌓는다 — 처음엔 끝까지, 그 뒤엔 새것만.
  앱 주문과 같은 토스 주문 번호면 앱 주문 하나로 보이고(체결가가 비었으면 토스 값으로 채움), 나머지는 source='toss' 로 시각순에 끼운다.

- 금액 = 체결 수량 × 체결 평균가. 지금 가치 = 체결 수량 × 현재가 (시세를 못 받으면 비운다).
- 매수 손익 = 지금 가치 − 매수 금액 (평가). 매도 손익 = (매도가 − 단가) × 수량 (실현). 단가는 매도 주문에 적어 둔 토스 보유 평단
  (basis_price) 을 먼저 쓰고, 없으면 이 기록 안 앞선 매수의 이동평균 단가로 잡는다. 둘 다 없으면(평단을 적기 전, 앱 밖에서 산 주식)
  산 가격을 몰라 실현 손익에서 뺀다.
- 판 뒤 변화 = 판 수량의 지금 가치 − 매도 금액. + 면 판 뒤에 올랐다는 뜻.
- 토론: 주문의 run_id 실행 폴더 messages.jsonl 에서 그 주문 id 를 담은 decision, 그 앞의 마지막 gate(입장판 결과),
  그 앞에서 이 종목에 낸 에이전트별 마지막 의견을 꺼낸다.
"""

import json
import sqlite3
import time
from functools import lru_cache
from pathlib import Path

from app.core.db import data_dir
from app.invest.broker import config, trading
from app.invest.broker.toss import TossClient, TossError
from app.invest.broker.trading import TradeError
from app.invest.marketdata import market
from app.invest.marketdata.market import MarketError

LIMIT = 500          # 거래 기록에 보이는 체결 주문 수 (최근 것부터)
QUOTE_CHUNK = 20     # 현재가는 이만큼씩 나눠 받는다
EXECUTOR = "trader"  # 트레이더 의견은 decision 으로 따로 보여준다
IMPORT_PAGES = 50    # 토스 끝난 주문을 한 번에 받는 최대 쪽 수 (한 쪽 100건)
IMPORT_EVERY = 60    # 토스 체결 가져오기는 이 초마다 한 번 (화면을 새로 고칠 때마다 부르지 않게)
S_IMPORTED = "invest_broker_fills_complete"  # 끝 쪽까지 한 번 받았나 — 아니면 다음에도 끝까지
_last_import = 0.0

ORDER_SQL = """
SELECT o.id, o.created_at, o.filled_at, o.symbol, o.name, o.currency, o.side, o.order_type, o.filled_quantity,
       o.avg_fill_price, o.price, o.ref_price, o.basis_price, o.status, o.source, o.run_id, o.reason, r.name AS run_name,
       o.broker_order_id
FROM invest_orders o LEFT JOIN runs r ON r.id = o.run_id
WHERE o.filled_quantity > 0
ORDER BY o.id
"""


def _at(o: dict) -> str:
    return (o.get("filled_at") or o.get("created_at") or "").replace("T", " ")[:19]


def _prices(p, symbols: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for i in range(0, len(symbols), QUOTE_CHUNK):
        try:
            quotes = p.quotes(symbols[i:i + QUOTE_CHUNK])
        except (MarketError, TossError):
            continue
        out.update({s: q["price"] for s, q in quotes.items() if q.get("price") is not None})
    return out


def _sync(conn: sqlite3.Connection) -> None:
    """진행 중이던 주문의 체결을 토스에서 다시 받아 둔다 (주문 내역 화면과 같은 방식, 실패해도 그대로 보여준다)."""
    if not config.toss_configured():
        return
    try:
        trading.account(conn).sync()
    except (TradeError, MarketError, TossError):
        pass


def _num(v: object) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _fill_row(o: dict) -> tuple | None:
    """토스 끝난 주문 하나 → invest_broker_fills 한 줄 (체결이 없으면 None)."""
    ex = o.get("execution") or {}
    q = _num(ex.get("filledQuantity")) or 0
    side = str(o.get("side") or "").lower()
    if q <= 0 or side not in ("buy", "sell") or not o.get("orderId") or not o.get("symbol"):
        return None
    price = _num(ex.get("averageFilledPrice"))
    amount = _num(ex.get("filledAmount")) or (q * price if price else None)
    return (str(o["orderId"]), str(o["symbol"]), side, str(o.get("orderType") or "").lower(), o.get("currency") or "KRW", q, price,
            amount, _num(ex.get("commission")), _num(ex.get("tax")), o.get("orderedAt"), ex.get("filledAt") or o.get("orderedAt"),
            o.get("status") or "")


def import_broker(conn: sqlite3.Connection, client=None, *, force: bool = False) -> int:
    """토스 계좌의 끝난 주문에서 체결을 받아 쌓는다 → 새로 쌓은 수. 처음(또는 끝까지 못 받았으면) 끝 쪽까지, 그 뒤엔 아는 주문이 나올 때까지.
    주문을 내지 않는 조회만 한다. 토스 키가 없거나 IMPORT_EVERY 초 안에 다시 부르면 건너뛴다."""
    global _last_import
    if client is None and not config.toss_configured():
        return 0
    if not force and time.monotonic() - _last_import < IMPORT_EVERY:
        return 0
    _last_import = time.monotonic()
    client = client or TossClient()
    known = {r[0] for r in conn.execute("SELECT broker_order_id FROM invest_broker_fills")}
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (S_IMPORTED,)).fetchone()
    full = not (row and row[0] == "true")
    added, cursor = 0, None
    for _ in range(IMPORT_PAGES):
        orders, cursor = client.closed_orders_page(cursor)
        fresh = [o for o in orders if str(o.get("orderId")) not in known]
        for o in fresh:
            rec = _fill_row(o)
            if rec:
                conn.execute("INSERT OR IGNORE INTO invest_broker_fills (broker_order_id, symbol, side, order_type, currency, quantity, price, "
                             "amount, commission, tax, ordered_at, filled_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rec)
                known.add(rec[0])
                added += 1
        conn.commit()
        if cursor is None:
            if full:
                conn.execute("INSERT INTO settings (key, value) VALUES (?, 'true') ON CONFLICT(key) DO UPDATE SET value = 'true'",
                             (S_IMPORTED,))
                conn.commit()
            break
        if not full and len(fresh) < len(orders):
            break  # 아는 주문까지 왔다 — 그 뒤는 이미 받았다
    return added


def _broker_rows(conn: sqlite3.Connection, app_ids: set[str], p) -> tuple[list[dict], dict[str, dict]]:
    """앱 주문이 아닌 토스 체결 → 거래 행(source='toss'), 그리고 토스 주문 번호 → 체결 (앱 주문의 빈 체결가 채우기)."""
    fills = [dict(r) for r in conn.execute("SELECT rowid AS rid, * FROM invest_broker_fills")]
    by_id = {f["broker_order_id"]: f for f in fills}
    mine = [f for f in fills if f["broker_order_id"] not in app_ids]
    try:
        names = p.names(conn, sorted({f["symbol"] for f in mine})) if mine and hasattr(p, "names") else {}
    except (MarketError, TossError):
        names = {}
    rows = [{"id": -f["rid"], "created_at": f["ordered_at"] or "", "filled_at": f["filled_at"], "symbol": f["symbol"],
             "name": names.get(f["symbol"]) or f["symbol"], "currency": f["currency"], "side": f["side"],
             "order_type": f["order_type"] if f["order_type"] in ("market", "limit") else "market", "filled_quantity": f["quantity"],
             "avg_fill_price": f["price"], "price": None, "ref_price": None, "basis_price": None,
             "status": "filled" if f["status"] == "FILLED" else "partial", "source": "toss", "run_id": None, "reason": "",
             "run_name": None, "broker_order_id": f["broker_order_id"]} for f in mine]
    return rows, by_id


def _rate(gain: float | None, base: float | None) -> float | None:
    return gain / base if gain is not None and base else None


def history(conn: sqlite3.Connection, p=None) -> dict:
    """체결된 주문 목록(최근 것부터) + 종목별 합계 + 전체 합계(원화 종목만)."""
    _sync(conn)
    try:
        import_broker(conn)
    except (MarketError, TossError):
        pass  # 받아 둔 것만으로 보여준다
    p = p or market.provider()
    rows = [dict(r) for r in conn.execute(ORDER_SQL).fetchall()]  # 단가는 처음부터 쌓아야 맞다 — 자르는 건 보여줄 때
    outside, fills = _broker_rows(conn, {r["broker_order_id"] for r in rows if r["broker_order_id"]}, p)
    for r in rows:
        f = fills.get(r["broker_order_id"] or "")
        if f and not r["avg_fill_price"]:
            r["avg_fill_price"] = f["price"]
    rows = sorted(rows + outside, key=lambda o: (_at(o), o["id"]))
    now = _prices(p, sorted({r["symbol"] for r in rows}))
    book: dict[str, dict] = {}
    trades: list[dict] = []
    for o in rows:
        sym, q = o["symbol"], o["filled_quantity"]
        fill = o["avg_fill_price"] or o["price"] or o["ref_price"]
        amount = q * fill if fill else None
        cur = now.get(sym)
        value = q * cur if cur is not None else None
        pos = book.setdefault(sym, {"symbol": sym, "name": o["name"] or sym, "currency": o["currency"], "buy_count": 0, "sell_count": 0,
                                    "bought": 0.0, "sold": 0.0, "realized": 0.0, "known_sells": 0, "qty": 0.0, "cost": 0.0,
                                    "unknown_sold": 0.0})
        t = {"id": o["id"], "at": _at(o), "symbol": sym, "name": o["name"] or sym, "currency": o["currency"], "side": o["side"],
             "order_type": o["order_type"], "status": o["status"], "quantity": q, "price": fill, "amount": amount,
             "now_price": cur, "now_value": value, "source": o["source"], "run_id": o["run_id"], "run_name": o["run_name"],
             "reason": o["reason"] or ""}
        if o["side"] == "buy":
            pos["buy_count"] += 1
            pos["bought"] += amount or 0
            pos["qty"] += q
            pos["cost"] += amount or 0
            t["pnl"] = value - amount if value is not None and amount is not None else None
            t["pnl_rate"] = _rate(t["pnl"], amount)
        else:
            pos["sell_count"] += 1
            pos["sold"] += amount or 0
            mine = min(q, pos["qty"])               # 이 기록 안에서 산 몫
            avg = pos["cost"] / pos["qty"] if mine > 0 else None
            if mine > 0:
                pos["cost"] -= avg * mine
                pos["qty"] -= mine
            basis = o["basis_price"] or avg
            matched = q if o["basis_price"] else mine
            t["basis_price"] = basis if matched > 0 else None
            t["basis_quantity"] = matched
            t["basis_from"] = "broker" if o["basis_price"] else ("history" if mine > 0 else None)
            t["realized"] = (fill - basis) * matched if matched > 0 and basis and fill else None
            if t["realized"] is not None:
                pos["realized"] += t["realized"]
                pos["known_sells"] += 1
            pos["unknown_sold"] += q - matched
            t["realized_rate"] = _rate(t["realized"], (t["basis_price"] or 0) * matched)
            t["after"] = value - amount if value is not None and amount is not None else None
            t["after_rate"] = _rate(t["after"], amount)
        trades.append(t)

    symbols = []
    for pos in book.values():
        cur = now.get(pos["symbol"])
        held_value = pos["qty"] * cur if cur is not None else None
        symbols.append({**{k: pos[k] for k in ("symbol", "name", "currency", "buy_count", "sell_count", "bought", "sold", "realized",
                                               "known_sells", "unknown_sold")},
                        "held_quantity": pos["qty"], "held_cost": pos["cost"], "now_price": cur, "held_value": held_value,
                        "unrealized": held_value - pos["cost"] if held_value is not None else None})
    symbols.sort(key=lambda s: -(s["bought"] + s["sold"]))
    won = [s for s in symbols if s["currency"] == "KRW"]
    priced = all(s["now_price"] is not None for s in won if s["held_quantity"] > 0)  # 남은 매수분 현재가를 다 받았나
    totals = {"trades": len(trades), "buys": sum(s["buy_count"] for s in won), "sells": sum(s["sell_count"] for s in won),
              "bought": sum(s["bought"] for s in won), "sold": sum(s["sold"] for s in won), "realized": sum(s["realized"] for s in won),
              "known_sells": sum(s["known_sells"] for s in won), "held_cost": sum(s["held_cost"] for s in won),
              "held_value": sum(s["held_value"] or 0 for s in won) if priced else None,
              "unrealized": sum(s["unrealized"] or 0 for s in won) if priced else None, "priced": priced,
              "other_currency": len(symbols) - len(won)}
    return {"trades": list(reversed(trades[-LIMIT:])), "symbols": symbols, "totals": totals, "quoted": bool(now)}


# ---- 토론 ----
@lru_cache(maxsize=16)
def _events(path: str, mtime: float, size: int) -> tuple[dict, ...]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return tuple(out)


def _load(artifact_path: str | None) -> tuple[dict, ...]:
    if not artifact_path:
        return ()
    path = data_dir() / artifact_path / "messages.jsonl"
    if not path.exists():
        return ()
    st = path.stat()
    return _events(str(path), st.st_mtime, st.st_size)


def _view(st: dict, symbol: str) -> dict | None:
    return next((v for v in st.get("views") or [] if v.get("symbol") == symbol), None)


def debate(conn: sqlite3.Connection, order_id: int) -> dict | None:
    """이 주문을 낳은 토론 — 안건 · 입장판 결과 · 에이전트별 마지막 의견 · 트레이더 판단. 직접 낸 주문이면 토론 없이."""
    o = conn.execute("SELECT o.id, o.symbol, o.side, o.source, o.run_id, o.reason, o.checks_json, r.name AS run_name, "
                     "r.created_at AS run_at, r.artifact_path FROM invest_orders o LEFT JOIN runs r ON r.id = o.run_id WHERE o.id = ?",
                     (order_id,)).fetchone()
    if o is None:
        return None
    try:
        checks = json.loads(o["checks_json"] or "[]")
    except ValueError:
        checks = []
    out = {"order_id": o["id"], "symbol": o["symbol"], "side": o["side"], "source": o["source"], "reason": o["reason"] or "",
           "checks": checks, "run_id": o["run_id"], "run_name": o["run_name"], "run_at": o["run_at"],
           "found": False, "agenda": None, "gate": None, "decision": None, "opinions": []}
    events = _load(o["artifact_path"])
    if not events:
        return out
    sym = o["symbol"]
    end = next((i for i, e in enumerate(events) if e.get("type") == "decision" and order_id in (e.get("order_ids") or [])), None)
    if end is None:
        end = next((i for i, e in enumerate(events) if e.get("type") == "order" and (e.get("order") or {}).get("id") == order_id), None)
    if end is None:
        return out
    out["found"] = True
    if events[end].get("type") == "decision":
        d = events[end].get("decision") or {}
        mine = next((x for x in d.get("decisions") or [] if x.get("symbol") == sym), None)
        out["decision"] = {"at": events[end].get("ts"), "title": events[end].get("title"), "rationale": d.get("rationale") or "",
                           "risk_notes": d.get("risk_notes") or [], "decision": (mine or d).get("decision"),
                           "quantity": (mine or d).get("quantity")}
    before = events[:end]
    gate_at = next((i for i in range(len(before) - 1, -1, -1)
                    if before[i].get("type") == "gate" and sym in ((before[i].get("gate") or {}).get("symbols") or {})), None)
    if gate_at is not None:
        g = before[gate_at]["gate"]
        mine = g["symbols"][sym]
        out["gate"] = {"at": before[gate_at].get("ts"), "mode": g.get("mode"), "status": mine.get("status"),
                       "direction": mine.get("direction"), "counts": mine.get("counts"), "weights": mine.get("weights"),
                       "reason": mine.get("reason") or g.get("reason") or ""}
    look = gate_at if gate_at is not None else len(before)
    out["agenda"] = next((e.get("label") for e in reversed(before[:look]) if e.get("type") == "round"), None)
    last: dict[str, dict] = {}
    for e in before:
        if e.get("type") != "statement" or e.get("error") or e.get("executor") or e.get("agent") == EXECUTOR:
            continue
        v = _view(e, sym)
        if v is None or v.get("implicit"):
            continue
        last[e["agent"]] = {"agent": e["agent"], "title": e.get("title") or e["agent"], "at": e.get("ts"), "round": e.get("round"),
                            "stance": v.get("stance") or "none", "confidence": v.get("confidence"),
                            "reason": v.get("reason") or e.get("thesis") or "", "changed": bool(e.get("changed"))}
    order = {"buy": 0, "sell": 1, "none": 2}
    out["opinions"] = sorted(last.values(), key=lambda x: (x["stance"] != o["side"], order.get(x["stance"], 3), -(x["confidence"] or 0)))
    return out
