"""보유 종목 매도 점검: 코드가 꺼낸 사실·신호, 토론이 보유 종목의 매도 타이밍을 따지게 하는 프롬프트, 자동 운용 회의에 보유 종목을
언제나 넣기, 상시 운용의 보유 종목 자리·새 매도 신호 대화. 안 가진 종목의 매도 안건 닫기(#41), 주가 등락과 보유 손익 나누기(#40),
회의록 글을 문장에서 자르기(#39)."""

import asyncio
from datetime import timedelta

from app.invest import agents, exits, minutes, policy, scopes, screening, stream
from app.invest.marketdata.market import SampleMarket
from tests.test_invest import at_open  # noqa: F401 — fixture
from tests.test_invest_ai import Script, _start
from tests.test_invest_autopilot import at, conn  # noqa: F401 — fixture
from tests.test_invest_stream import _bare_stream

M = {"price": 90_000, "change_5d": -0.03, "change_20d": -0.08, "rsi": 38, "trend": -1, "drawdown": -0.15, "ma20": 95_000}
H = {"symbol": "005930", "name": "삼성전자", "quantity": 10, "avg_price": 100_000, "last_price": 90_000, "pnl_rate": -0.1, "weight": 0.35}


def _hold(broker, symbol, quantity=2.0, markup=1.0):
    """가짜 계좌에 보유를 넣는다 — markup 만큼 비싸게 산 것으로 (1.2 면 지금 약 −16.7%)."""
    broker.positions[symbol] = {"quantity": quantity, "cost": broker.price(symbol) * quantity * markup}


def test_check_turns_facts_into_sell_signals():
    c = exits.check(H, M, 30)
    assert [f["key"] for f in c["flags"]] == ["loss", "drawdown", "ma20", "downtrend", "weight"]
    text = exits.line(c)
    assert text.startswith("평단 100,000원 → 지금 90,000원 (-10.0%) · 계좌 비중 35.0%")
    assert "20일선 대비 -5.3%" in text and "신호: 손실 -10.0%, 60일 고점 대비 -15.0%, 20일선 아래 -5.3%" in text
    assert exits.why(c).startswith("보유 — 매도 점검 · 손실 -10.0%") and "비중 35.0% > 한도 30%" in exits.why(c)
    hot = exits.check({**H, "last_price": 130_000, "pnl_rate": 0.3, "weight": 0.1}, {**M, "trend": 1, "drawdown": 0, "rsi": 75}, 30)
    assert [f["key"] for f in hot["flags"]] == ["gain", "hot"]                                     # 차익 실현 · 과열
    calm = exits.check({**H, "last_price": 101_000, "pnl_rate": 0.01, "weight": 0.1}, {**M, "trend": 1, "drawdown": -0.02, "rsi": 55}, 30)
    assert calm["flags"] == [] and exits.line(calm).endswith("신호: 없음") and exits.why(calm) == "보유 — 매도 점검"
    assert exits.urgent({"a": calm, "b": c, "c": hot}) == ["b", "c", "a"]                         # 신호가 많은 순
    assert exits.check(H, None)["flags"] == [{"key": "loss", "text": "손실 -10.0%"}]               # 일봉이 없어도 손익 신호는


def test_position_limit_takes_the_stricter_one(conn):  # noqa: F811
    assert exits.position_limit(conn) == 30.0                                                     # 주문 한도 기본값
    policy.save(conn, {"max_position": 20}, by="test")
    assert exits.position_limit(conn) == 20                                                       # 운용 목표가 더 낮으면 그쪽


def test_prompts_make_analysts_weigh_selling_what_they_hold():
    held = {"symbol": "005930", "name": "삼성전자", "security_type": "STOCK", "held": 10.0}
    other = {"symbol": "000660", "name": "SK하이닉스", "security_type": "STOCK", "held": 0.0}
    s = agents.Session(conn=None, scope={"label": "x", "candidates": [held, other]}, run_id=None, provider=None,
                       holdings={"005930": exits.check(H, M, 30)})
    analyst, trader = agents.DEFAULT_ROSTER[0], next(a for a in agents.DEFAULT_ROSTER if a["executor"])
    system = agents.system_prompt(analyst, s)
    assert agents.EXIT_RULES in system and "· 보유 10주\n  · 매도 점검: 평단 100,000원" in system
    assert agents._candidates_text(s).count("매도 점검") == 1                                      # 가진 종목에만
    assert agents.EXIT_RULES not in agents.system_prompt(trader, s)                               # 트레이더는 주문 프롬프트에서
    assert "매도 타이밍" in agents.round1_prompt(s) and "매도 타이밍" in agents.rebuttal_prompt(s, analyst, 2, [])
    bare = agents.Session(conn=None, scope={"label": "x", "candidates": [other]}, run_id=None, provider=None)
    assert agents.EXIT_RULES not in agents.system_prompt(analyst, bare) and "매도 타이밍" not in agents.round1_prompt(bare)
    said = [{"round": 2, "agent": "technical", "title": "차티스트", "thesis": "t",
             "views": [{"symbol": "005930", "stance": "sell", "confidence": 0.7, "reason": "20일선 이탈"}]}]
    gate = lambda d: {"symbols": {"005930": {"status": "aligned", "reason": "열림"}}, "aligned": [{"symbol": "005930", "direction": d}]}  # noqa: E731
    assert agents.SELL_SIZING in agents.trader_prompt(s, said, gate("sell"), False)               # 일부·전량 수량을 이유에 맞춰
    assert agents.SELL_SIZING not in agents.trader_prompt(s, said, gate("buy"), False)
    assert agents.SELL_SIZING in stream.trade_prompt(s, gate("sell"), "-", "급변", 3)


def test_single_stock_debate_on_a_holding_carries_the_sell_check(client, broker, monkeypatch, at_open):  # noqa: F811
    _hold(broker, "005930", 10.0, 1.1)
    script = Script({})
    monkeypatch.setattr(agents, "LLM", script)
    run = _start(client, rounds=1).json()
    assert run["status"] == "succeeded", run.get("error")
    first = script.prompts[0][1]
    assert "보유 10주\n  · 매도 점검: 평단" in first and "손실 -9.1%" in first and "매도 타이밍" in first


def test_autopilot_meeting_always_brings_holdings(conn, broker, monkeypatch):  # noqa: F811
    for sym in ("035420", "035720", "005380"):
        _hold(broker, sym)
    p = SampleMarket()
    out = scopes.resolve(conn, p, {"scope": "autopilot", "intents": ["하이닉스 좀 잘 봐줘"]}, 2)
    syms = [c["symbol"] for c in out["candidates"]]
    why = {c["symbol"]: c["why"] for c in out["candidates"]}
    assert syms[0] == "000660" and len(syms) == 4 and {"035420", "035720", "005380"} <= set(syms)  # 후보 2개 + 고르지 않은 보유 2개
    assert all(why[s_].startswith("보유 — 매도 점검") and s_ in {"035420", "035720", "005380"} for s_ in syms[2:])
    assert {r["symbol"] for r in out["screening"]["rows"] if r["picked"]} == set(syms)            # 판단 기록에도 고른 것으로
    monkeypatch.setattr(exits, "MAX_HELD", 2)
    cut = scopes.resolve(conn, p, {"scope": "autopilot", "intents": ["하이닉스 좀 잘 봐줘"]}, 2)
    assert len(cut["candidates"]) == 3 and any("보유 3종목 중 2개만" in n for n in cut["notes"])


def test_stream_keeps_a_seat_for_holdings_and_talks_on_new_sell_signals(client, conn, broker, at_open):  # noqa: F811
    syms = ["000660", "035420", "035720", "005380", "051910"]
    st = _bare_stream(conn, candidates=syms)
    _hold(broker, "005930", 10.0, 1.2)
    st.talked = {"005930": at("09:30")}                                                           # 보유 종목은 방금 이야기했어도
    focus = st._focus([])
    assert len(focus) == stream.FOCUS and "005930" in [c["symbol"] for c in focus]                # 대화마다 한 자리
    s = st._session(focus, "점검")
    assert list(s.holdings) == ["005930"] and "손실 -16.7%" in agents._candidates_text(s)
    now = at("10:00")
    trig = st._exit_signals(now)
    assert trig["kind"] == "exit" and trig["symbols"] == ["005930"] and "손실 -16.7%" in trig["text"]
    assert st._exit_signals(now + timedelta(minutes=1)) is None                                   # EXIT_CHECK_MIN 분마다 잰다
    assert st._exit_signals(now + timedelta(minutes=stream.EXIT_CHECK_MIN)) is None               # 같은 신호는 하루 한 번
    assert st._speakers("exit")[0]["key"] == "risk" and stream.KIND_KO["exit"] == "매도 신호"


def _votes(**v):
    return {"error": None, "views": [{"symbol": k, "stance": x} for k, x in v.items()]}


def test_sell_votes_on_a_stock_not_held_never_become_an_order_item():
    """#41 — 안 가진 종목의 sell 은 '사지 말자'라 매도 안건으로 열지 않는다. 보유를 모르는 종목은 그대로(주문 단계의 판매 가능 수량이 막는다)."""
    final = [_votes(A="sell", B="sell", C="sell"), _votes(A="sell", B="sell", C="sell")]
    g = agents.session_gate(final, ["A", "B", "C"], held={"A": 10.0, "B": 0.0})
    assert g["aligned"] == [{"symbol": "A", "direction": "sell"}, {"symbol": "C", "direction": "sell"}]
    assert g["symbols"]["B"]["status"] == "not_held" and g["symbols"]["B"]["reason"] == agents.NOT_HELD
    only = agents.session_gate(final, ["B"], held={"B": 0.0}, mode="supermajority")
    assert only["status"] == "not_held" and only["aligned"] == [] and only["reason"] == agents.NOT_HELD
    both = agents.session_gate(final, ["A", "B"], held={"A": 0.0, "B": 0.0})
    assert both["status"] == "not_held" and "보유하지 않은 종목뿐" in both["reason"]
    assert agents.session_gate([_votes(B="buy"), _votes(B="buy")], ["B"], held={"B": 0.0})["aligned"] == [{"symbol": "B", "direction": "buy"}]
    b = stream.Board()
    now = at("10:00")
    for agent in ("a", "b", "c"):
        b.update({"agent": agent, "title": agent, "views": [{"symbol": "000660", "stance": "sell", "confidence": 0.7, "reason": "사지 말자"}]}, now)
    assert b.gate(["000660"], {}, now)["aligned"]                                                 # 보유를 모르면 그대로
    g = b.gate(["000660"], {}, now, {"000660": 0.0})
    assert g["aligned"] == [] and g["symbols"]["000660"]["status"] == "not_held"


def test_stream_keeps_sell_votes_on_stocks_not_held_away_from_the_trader(client, conn, broker, monkeypatch, at_open):  # noqa: F811
    st = _bare_stream(conn, candidates=["000660"])
    _hold(broker, "005930", 10.0)
    now = at("10:00")
    for a in st.analysts[:3]:
        st.board.update({"agent": a["key"], "title": a["title"], "views": [{"symbol": "000660", "stance": "sell", "confidence": 0.7, "reason": "r"}]}, now)
    calls = []

    async def llm(feature, system, prompt, *a):
        calls.append(prompt)
        return {"thesis": "점검", "views": [{"symbol": "000660", "stance": "sell", "confidence": 0.6, "reason": "r"},
                                          {"symbol": "005930", "stance": "sell", "confidence": 0.6, "reason": "손절"}]}

    monkeypatch.setattr(agents, "LLM", llm)
    assert st._blocked() is None
    asyncio.run(st.trade(now, "급변"))
    assert calls == [] and st.orders == []                                                        # 필수 의견도 트레이더도 부르지 않음
    asyncio.run(st.review(now))
    assert [q["symbols"] for q in st.queue] == [["005930"]]                                       # 트레이더 점검도 가진 종목만 판다


def test_screening_table_keeps_price_moves_apart_from_holding_pnl(conn):  # noqa: F811
    """#40 — 20일 주가 등락을 보유 평가 손익으로 읽지 않게: 표에 둘을 따로, 고르는 프롬프트에도 구분."""
    m = {"price": 1, "change_1d": 0.01, "change_5d": 0.02, "change_20d": 0.12, "volume_ratio": 1.0, "rsi": 50, "trend": 1}
    row = {"symbol": "005930", "name": "삼성전자", "sources": ["보유"], "metrics": m, "score": 1.2, "holding_pnl": -0.05}
    text = screening.table([row])
    assert "보유 손익(평단 대비) -5.0% · 주가 등락 1일 +1.0% · 5일 +2.0% · 20일 +12.0%" in text
    assert screening._why(row) == "보유 · 보유 손익 -5.0% · 주가 20일 등락 +12.0%"
    assert "보유 손익" not in screening.table([{**row, "holding_pnl": None}])
    assert "보유 손익(평단 대비)" in screening.PICK_SYSTEM and "차익 실현" in screening.PICK_SYSTEM
    rows = screening.pool(conn, SampleMarket(), [], [], [{"symbol": "005930", "name": "삼성전자", "pnl_rate": -0.05}], [])
    assert next(r for r in rows if r["symbol"] == "005930")["holding_pnl"] == -0.05
    assert "주가 등락 5일 -3.0% · 20일 -8.0%" in exits.line(exits.check(H, M, 30))                  # 매도 점검 줄도


def test_minutes_cut_long_text_at_a_sentence_with_an_ellipsis():
    """#39 — 회의록 사실·요약 글을 문장 중간에서 말없이 자르지 않는다."""
    long = "추세가 꺾였습니다. " * 150
    cut = minutes.clip(long, 100)
    assert cut.endswith("꺾였습니다.…") and len(cut) <= 100
    assert minutes.clip(" 짧은 글 ", 100) == "짧은 글" and minutes.clip(None, 10) == ""
    assert minutes.clip("가" * 300, 100) == "가" * 99 + "…"                                       # 끊을 곳이 없으면 그 자리에서
    f = minutes.facts(label="x", kind="수동 회의", created_at="t", scope={"candidates": []}, roster=[{"key": "a", "title": "A"}],
                      transcript=[{"round": 1, "agent": "a", "title": "A", "thesis": long}], gate={}, decision={"rationale": long},
                      orders=[], notes=[{"text": "[운용 지시] " + long}], schedule=None)
    assert f["views"][0]["thesis"].endswith("…") and len(f["views"][0]["thesis"]) <= minutes.THESIS_MAX
    assert f["trader"].endswith("…") and f["notes"][0].endswith("…")
    assert minutes.clean({"headline": "h", "summary": long}, f)["summary"].endswith("…")
