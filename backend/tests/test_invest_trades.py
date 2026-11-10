"""거래 기록 — 금액 · 지금 가치 · 손익 계산, 주문을 낳은 토론 꺼내기, 매도 때 평단 적기."""

import json

import pytest

from app.core.db import data_dir
from app.invest.ops import trades
from tests.test_invest import PIN, at_open, conn  # noqa: F401 — fixture
from tests.test_invest import _order


class Quotes:
    """현재가만 돌려주는 가짜 시세."""

    def __init__(self, prices: dict[str, float]):
        self.prices = prices

    def quotes(self, symbols: list[str]) -> dict[str, dict]:
        return {s: {"symbol": s, "price": self.prices[s]} for s in symbols if s in self.prices}


def _fill(conn, symbol, side, qty, price, *, name="", source="agent", run_id=None, basis=None, status="filled"):
    cur = conn.execute(
        "INSERT INTO invest_orders (symbol, name, side, order_type, quantity, price, ref_price, status, filled_quantity, "
        "avg_fill_price, filled_at, source, run_id, reason, basis_price) VALUES (?, ?, ?, 'limit', ?, ?, ?, ?, ?, ?, "
        "'2026-11-10T09:30:00+09:00', ?, ?, '근거', ?)",
        (symbol, name or symbol, side, qty, price, price, status, qty, price, source, run_id, basis))
    conn.commit()
    return cur.lastrowid


def test_history_amounts_values_and_pnl(conn):  # noqa: F811
    _fill(conn, "005930", "buy", 10, 100, name="삼성전자")
    _fill(conn, "005930", "sell", 4, 120)                      # 앱에서 산 몫 → 이동평균 100 으로 실현
    _fill(conn, "000660", "sell", 2, 300, basis=250)           # 매도 때 적어 둔 토스 평단으로 실현
    _fill(conn, "035420", "sell", 1, 200)                      # 산 가격을 모름
    conn.execute("INSERT INTO invest_orders (symbol, side, order_type, quantity, status) VALUES ('005380', 'buy', 'market', 1, 'cancelled')")
    conn.commit()
    h = trades.history(conn, Quotes({"005930": 110, "000660": 280, "035420": 260}))
    by = {(t["symbol"], t["side"]): t for t in h["trades"]}
    assert len(h["trades"]) == 4 and h["trades"][0]["symbol"] == "035420"     # 체결된 것만, 최근 것부터
    buy = by[("005930", "buy")]
    assert buy["amount"] == 1000 and buy["now_value"] == 1100 and buy["pnl"] == 100 and buy["pnl_rate"] == pytest.approx(0.1)
    sell = by[("005930", "sell")]
    assert sell["realized"] == 80 and sell["basis_from"] == "history" and sell["after"] == 440 - 480
    known = by[("000660", "sell")]
    assert known["realized"] == 100 and known["basis_from"] == "broker" and known["realized_rate"] == pytest.approx(0.2)
    unknown = by[("035420", "sell")]
    assert unknown["realized"] is None and unknown["basis_from"] is None and unknown["after"] == 60
    sym = {s["symbol"]: s for s in h["symbols"]}
    assert sym["005930"]["held_quantity"] == 6 and sym["005930"]["held_cost"] == 600 and sym["005930"]["unrealized"] == 60
    assert sym["035420"]["unknown_sold"] == 1
    t = h["totals"]
    assert (t["bought"], t["sold"], t["realized"], t["held_value"], t["unrealized"]) == (1000, 480 + 600 + 200, 180, 660, 60)
    assert t["known_sells"] == 2 and sym["035420"]["known_sells"] == 0 and t["priced"] is True


def test_history_without_quotes_keeps_amounts(conn):  # noqa: F811
    _fill(conn, "005930", "buy", 2, 100)
    h = trades.history(conn, Quotes({}))
    assert h["quoted"] is False and h["trades"][0]["amount"] == 200 and h["trades"][0]["now_value"] is None
    assert h["trades"][0]["pnl"] is None and h["totals"]["priced"] is False and h["totals"]["held_value"] is None


def _run_with(conn, events: list[dict]) -> int:
    rid = conn.execute("INSERT INTO runs (kind, name, status, created_at) VALUES ('agent_action', '상시 운용', 'succeeded', "
                       "'2026-11-10 09:05:00')").lastrowid
    folder = data_dir() / "runs" / str(rid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "messages.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events), encoding="utf-8")
    conn.execute("UPDATE runs SET artifact_path = ? WHERE id = ?", (f"runs/{rid}", rid))
    conn.commit()
    return rid


def _say(agent, stance, conf, ts, symbol="005930", **kw):
    return {"type": "statement", "ts": ts, "round": 1, "agent": agent, "title": agent.upper(),
            "views": [{"symbol": symbol, "stance": stance, "confidence": conf, "reason": f"{agent} {stance}"}], **kw}


def test_debate_finds_gate_opinions_and_decision(conn):  # noqa: F811
    oid = _fill(conn, "005930", "buy", 1, 100)
    events = [
        {"type": "round", "ts": "09:05:00", "label": "09:05 · 필수 의견 — 삼성전자 매수"},
        _say("technical", "sell", 0.6, "09:05:10"),
        _say("technical", "buy", 0.7, "09:06:10", changed=True),     # 마지막 의견만
        _say("risk", "sell", 0.5, "09:06:20"),
        _say("news", "buy", 0.9, "09:06:30", error="응답 실패"),      # 실패는 뺀다
        _say("trader", "buy", 0.9, "09:06:40", executor=True),       # 트레이더는 decision 으로
        _say("value", "buy", 0.6, "09:06:50", symbol="000660"),      # 다른 종목
        {"type": "gate", "ts": "09:07:00", "gate": {"mode": "rolling", "reason": "열림", "symbols": {"005930": {
            "status": "aligned", "direction": "buy", "counts": {"buy": 1, "sell": 1, "none": 0}, "weights": {"buy": 2.4, "sell": 0.9},
            "reason": "매수 무게 2.4 ≥ 2"}}}},
        {"type": "decision", "ts": "09:07:30", "title": "트레이더", "order_ids": [oid],
         "decision": {"decisions": [{"symbol": "005930", "decision": "buy", "quantity": 1}], "rationale": "산 이유", "risk_notes": ["조심"]}},
        _say("growth", "buy", 0.8, "09:08:00"),                      # 주문 뒤 의견은 뺀다
    ]
    rid = _run_with(conn, events)
    conn.execute("UPDATE invest_orders SET run_id = ? WHERE id = ?", (rid, oid))
    conn.commit()
    d = trades.debate(conn, oid)
    assert d["found"] and d["agenda"] == "09:05 · 필수 의견 — 삼성전자 매수" and d["run_name"] == "상시 운용"
    assert d["gate"]["weights"] == {"buy": 2.4, "sell": 0.9} and d["gate"]["reason"] == "매수 무게 2.4 ≥ 2"
    assert d["decision"]["rationale"] == "산 이유" and d["decision"]["risk_notes"] == ["조심"] and d["decision"]["quantity"] == 1
    assert [(x["agent"], x["stance"]) for x in d["opinions"]] == [("technical", "buy"), ("risk", "sell")]   # 같은 편 먼저
    assert d["opinions"][0]["changed"] is True


def test_debate_for_manual_order_and_api(client, conn):  # noqa: F811
    oid = _fill(conn, "005930", "buy", 1, 100, source="manual")
    d = client.get(f"/api/invest/trades/{oid}/debate").json()
    assert d["found"] is False and d["source"] == "manual" and d["opinions"] == []
    assert client.get("/api/invest/trades/9999/debate").status_code == 404
    body = client.get("/api/invest/trades").json()
    assert body["trades"][0]["id"] == oid and set(body) == {"trades", "symbols", "totals", "quoted"}


def test_sell_order_records_broker_avg_price(client, broker, conn, at_open):  # noqa: F811
    broker.positions["005930"] = {"quantity": 5, "cost": 5 * 50_000}            # 앱을 쓰기 전부터 갖고 있던 주식
    res = _order(client, side="sell", quantity=2)
    assert res.status_code == 201, res.text
    row = conn.execute("SELECT basis_price FROM invest_orders WHERE id = ?", (res.json()["id"],)).fetchone()
    assert row["basis_price"] == 50_000
    sold = next(t for t in client.get("/api/invest/trades").json()["trades"] if t["id"] == res.json()["id"])
    assert sold["basis_from"] == "broker" and sold["realized"] == pytest.approx((sold["price"] - 50_000) * 2)
    buy = _order(client, quantity=1)                                              # 매수에는 적지 않는다
    assert conn.execute("SELECT basis_price FROM invest_orders WHERE id = ?", (buy.json()["id"],)).fetchone()[0] is None
