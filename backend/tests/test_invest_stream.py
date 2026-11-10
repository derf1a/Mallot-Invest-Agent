"""상시 운용: 정기 회의 없이 장 내내 도는 채널 — 입장판·롤링 게이트, 계기(점검·관리자 말·트레이더 점검), 트레이더가 언제든 주문(같은 종목
쉬는 시간), 하루 회의록, tick 이 채널을 열고 끊기면 다시 열기, 상시 운용에서 경보·지금 판단·비서 안건이 채널로."""

import asyncio
import re
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.invest.advice import assistant
from app.invest.broker import trading
from app.invest.debate import agents, stream, track
from app.invest.jobs import agent_action
from app.invest.marketdata import market
from app.invest.marketdata.market import SampleMarket
from app.invest.ops import autopilot, desk, desk_agents
from app.runs.runner import INTERRUPTED, load_run
from tests.test_invest import PIN, at_open  # noqa: F401
from tests.test_invest_ai import Script
from tests.test_invest_autopilot import at, conn  # noqa: F401 — fixture

TITLES = [a["title"] for a in agents.DEFAULT_ROSTER if not a["executor"]]


def _say(b, agent, stance, confidence, now, symbol="005930"):
    b.update({"agent": agent, "title": agent.upper(), "views": [{"symbol": symbol, "stance": stance, "confidence": confidence, "reason": "r"}]}, now)


def test_board_weighs_opinions_and_none_has_no_weight():
    """표의 무게 = 확신 × 성과. 의견 없음은 무게가 없어 머릿수로 막지 않는다 — 두 명이 사자는데 셋이 말이 없어 놓치던 경우."""
    b = stream.Board()
    now = at("10:00")
    for agent, stance, conf in (("a", "buy", 0.55), ("b", "buy", 0.6), ("c", "none", 0.5), ("d", "none", 0.5), ("e", "none", 0.5)):
        _say(b, agent, stance, conf, now)
    g = b.gate(["005930"], {"005930": "삼성전자"}, now)
    assert g["aligned"] == [{"symbol": "005930", "direction": "buy"}] and "삼성전자 매수" in g["reason"]
    row = g["symbols"]["005930"]
    assert row["weights"] == {"buy": 2.3, "sell": 0} and row["counts"] == {"buy": 2, "sell": 0, "none": 3}
    assert "무게 매수 2.3" in b.text(["005930"], {"005930": "삼성전자"}, now)
    _say(b, "f", "sell", 0.7, now)                                                     # 강한 반대 하나 (1.4 > 1.0)
    assert b.gate(["005930"], {}, now)["aligned"] == []
    assert b.gate(["005930"], {}, now + timedelta(minutes=stream.STALE_MIN + 1))["symbols"]["005930"]["status"] == "no_view"
    b.update({"agent": "e", "title": "E", "error": "x", "views": [{"symbol": "000660", "stance": "buy", "confidence": 1}]}, now)
    assert "000660" not in b.symbols(now)                                              # 응답 실패는 입장판에 안 들어감


def test_board_past_performance_changes_the_weight():
    now = at("10:00")
    for skill, opened in ((1.3, True), (0.7, False)):
        b = stream.Board(skill={"a": skill, "b": skill})
        _say(b, "a", "buy", 0.4, now)
        _say(b, "b", "buy", 0.4, now)                                                  # 1.6 × 성과 배수 — 잘 맞힌 쪽만 기준(1.5)을 넘는다
        assert bool(b.gate(["005930"], {}, now)["aligned"]) is opened


def _continuous(conn, broker, monkeypatch, start: str):
    clock = {"t": at(start)}
    monkeypatch.setattr(market, "now_kst", lambda: clock["t"])
    autopilot.save_config(conn, enabled=True, mode="continuous")
    return clock


def test_stream_runs_all_day_and_trader_trades_anytime(client, conn, broker, monkeypatch):
    clock = _continuous(conn, broker, monkeypatch, "14:40")
    autopilot.add_intent(conn, "삼성전자 잘 봐줘", now=at("09:00"))
    script = Script({t: [{"005930": "buy"}] for t in TITLES},
                    trade={"symbol": "005930", "side": "buy", "quantity": 1, "order_type": "market", "reason": "입장판 과반"})
    monkeypatch.setattr(agents, "LLM", script)
    said = {}

    def sleep(sec):
        clock["t"] += timedelta(seconds=sec)
        if clock["t"] >= at("14:50") and not said:
            said["msg"] = agent_action.post_user_message(next(iter(agent_action._LIVE)), "SK하이닉스도 같이 봐 줘")

    monkeypatch.setattr(stream, "SLEEP", sleep)
    rec = autopilot.tick(conn, at("14:40"), SampleMarket())
    assert rec["status"] == "started" and said["msg"]
    run = load_run(conn, rec["run_id"])
    s = run["summary"]
    assert run["status"] == "succeeded", run.get("error")
    assert s["stream"] and s["end"] == "closed" and s["talks"] >= 3 and s["gate_mode"] == "rolling"
    assert [(o["symbol"], o["status"]) for o in s["orders"]] == [("005930", "filled")] * 2    # 35분 — 쉬는 시간(30분) 뒤에 한 번 더
    assert len(broker.posts()) == 2
    msgs = client.get(f"/api/invest/agent-sessions/{rec['run_id']}/messages").json()["messages"]
    start = msgs[0]
    assert start["type"] == "start" and start["stream"] and start["gate"] == "rolling"
    rounds = [m for m in msgs if m["type"] == "round"]
    assert all(r["stream"] for r in rounds) and rounds[0]["label"].startswith("14:40 · 점검")
    user_round = next(r for r in rounds if "관리자" in r["label"])
    assert "SK하이닉스도 같이 봐 줘" in user_round["label"] and len(user_round["agents"]) == stream.SPEAKERS + 1
    after_user = [m for m in msgs if m["type"] == "statement" and m["round"] == user_round["round"]]
    assert after_user and after_user[0]["views"][0]["symbol"] == "000660"                     # 말에 나온 종목을 먼저 본다
    decision = next(m for m in msgs if m["type"] == "decision")
    assert decision["order_ids"] == [s["orders"][0]["id"]]
    gate = next(m for m in msgs if m["type"] == "gate")
    assert gate["gate"]["mode"] == "rolling" and gate["gate"]["aligned"][0]["symbol"] == "005930"
    assert any(m["type"] == "minutes" for m in msgs) and msgs[-1]["type"] == "end" and "장이 끝나" in msgs[-1]["message"]
    speakers = {m["agent"] for m in msgs if m["type"] == "statement" and not m.get("executor")}
    assert len(speakers) == len(TITLES)                                                      # 돌아가며 모두 말함
    assert autopilot.tick(conn, at("15:16"), SampleMarket()) is None                          # 장 끝까지 했으면 다시 열지 않음
    entries = autopilot.status(conn, at("15:16"), SampleMarket())["today"]["entries"]
    assert [(e["kind"], e["status"], e["run_id"]) for e in entries if e["kind"] == "stream"] == [("stream", "started", rec["run_id"])]


def test_trader_review_opens_a_talk(client, conn, broker, monkeypatch):
    """트레이더 점검에서 스스로 매도를 내놓으면 그 종목으로 대화가 열린다 (가진 종목 — 점검은 보유 종목을 먼저 본다)."""
    clock = _continuous(conn, broker, monkeypatch, "15:00")
    broker.positions["005930"] = {"quantity": 5.0, "cost": broker.price("005930") * 5 * 0.8}
    calls = []

    async def llm(feature, system, prompt, schema, tools, model, max_turns):
        calls.append(re.search(r"당신은 '([^']+)' 에이전트", system).group(1))
        syms = re.findall(r"^- .+ \((\w+)\) · (?:주식|ETF)", system, re.M)
        if schema is agents.OPINION_SCHEMA:                                                  # 트레이더 점검
            return {"views": [{"symbol": syms[0], "stance": "sell", "confidence": 0.8, "reason": "차익"}], "thesis": "차익 실현"}
        if schema is agents.TRADER_SCHEMA:
            return {"decisions": [], "rationale": "관망"}
        return {"views": [{"symbol": s_, "stance": "none", "confidence": 0.3, "reason": ""} for s_ in syms], "thesis": "관망"}

    monkeypatch.setattr(agents, "LLM", llm)
    monkeypatch.setattr(stream, "SLEEP", lambda sec: clock.__setitem__("t", clock["t"] + timedelta(seconds=sec)))
    rec = autopilot.tick(conn, at("15:00"), SampleMarket())
    msgs = client.get(f"/api/invest/agent-sessions/{rec['run_id']}/messages").json()["messages"]
    labels = [m["label"] for m in msgs if m["type"] == "round"]
    assert any("트레이더 제안" in x and "삼성전자 매도" in x and "차익 실현" in x for x in labels), labels
    assert any(m["type"] == "statement" and m.get("executor") for m in msgs)                 # 트레이더 말도 채팅방에


def test_stream_tick_reopens_after_failure_not_after_stop(client, conn, broker, monkeypatch):
    _continuous(conn, broker, monkeypatch, "10:00")
    tries = []

    def boom(self, notes):
        tries.append(1)
        raise RuntimeError("서버 재시작")

    monkeypatch.setattr(stream.Stream, "run", boom)
    first = autopilot.tick(conn, at("10:00"), SampleMarket())
    assert load_run(conn, first["run_id"])["status"] == "failed"
    again = autopilot.tick(conn, at("10:01"), SampleMarket())
    assert again["status"] == "started" and again["run_id"] != first["run_id"]
    autopilot.tick(conn, at("10:02"), SampleMarket())
    assert autopilot.tick(conn, at("10:03"), SampleMarket()) is None and len(tries) == autopilot.MAX_STREAMS
    assert autopilot.tick(conn, at("08:00"), SampleMarket()) is None                          # 하루 최대
    autopilot.save_config(conn, enabled=False)
    assert autopilot.tick(conn, at("10:05"), SampleMarket()) is None


def test_continuous_mode_routes_alerts_and_instructions_to_the_channel(client, conn, broker, monkeypatch):
    _continuous(conn, broker, monkeypatch, "10:00")
    broker.positions["000660"] = {"quantity": 2.0, "cost": 180_000.0}
    monkeypatch.setattr(desk_agents, "day_change", lambda p, s: -0.06)
    out = desk_agents.watch_and_act(conn, SampleMarket(), at("10:00"))
    assert out["alerts"] and "adhoc" not in out and desk.state(conn, at("10:00").date())["pending"] is None
    assert "상시 운용 채널에서 바로 볼게요" in [m for m in assistant.messages(conn) if m["role"] == "agent"][-1]["text"]
    with pytest.raises(autopilot.AutopilotError):
        autopilot.run_adhoc(conn, {"000660": "x"}, now=at("10:00"), provider=SampleMarket())
    assert autopilot.post_to_stream(conn, "지금 사 줘") is None                               # 채널이 없으면 None
    assert autopilot.status(conn, at("10:00"), SampleMarket())["config"]["mode"] == "continuous"
    with pytest.raises(autopilot.AutopilotError):
        autopilot.save_config(conn, mode="sometimes")


class _Chat(list):
    """채널 대신 — 올린 메시지를 모은다."""
    __call__ = list.append

    def notes(self):
        return []


def _bare_stream(conn, p=None, candidates=()):
    """대화 없이 Stream 의 계산만 볼 때 — 채널·실행 기록을 쓰지 않는다."""
    st = stream.Stream(SimpleNamespace(conn=conn, run_id=None, log=lambda *_a, **_k: None), _Chat(), p or SampleMarket(), None,
                       agents.DEFAULT_ROSTER, 4, lambda *_a: None, lambda *_a: None)
    st.candidates = [{"symbol": c, "name": c, "security_type": "", "held": 0.0, "why": ""} for c in candidates]
    return st


def _buy(conn, symbol, quantity=2):
    return trading.place_order(conn, symbol=symbol, side="buy", order_type="market", quantity=quantity, source="agent", run_id=None, auto_submit=True)


def test_holdings_stay_on_watch_with_fresh_quantity(client, conn, broker, at_open):  # noqa: F811
    """후보 밖에서 산 보유 — 볼 종목에 들어와야 급변을 보고 입장이 모여 손절할 수 있고, 판매 가능 수량도 보여야 한다."""
    st = _bare_stream(conn, candidates=["005930"])
    _buy(conn, "000660")
    _buy(conn, "005930", 3)
    focus = {c["symbol"]: c for c in st._focus([])}
    assert focus["000660"]["held"] == 2 and focus["000660"]["why"] == "보유 종목" and focus["005930"]["held"] == 3   # 후보를 고른 뒤 산 수량까지
    assert "000660" in [c["symbol"] for c in st.candidates]                            # 급변 감시(_moved)도 후보를 본다
    only = {"symbol": "005930", "name": "삼성전자", "held": 3}
    s = agents.Session(conn=conn, scope={"scope": "stream", "label": "x", "candidates": [only]}, run_id=None, provider=st.p)
    limits = asyncio.run(agents._handlers(s)["get_trading_limits"]({}))
    assert limits["sellable_quantity"] == {"005930": 3, "000660": 2}                   # 볼 종목에 없는 보유도


def test_focus_rotates_to_symbols_not_discussed_lately(client, conn, broker, at_open):  # noqa: F811
    syms = ["005930", "000660", "035420", "035720", "005380", "051910"]
    st = _bare_stream(conn, candidates=syms)
    assert [c["symbol"] for c in st._focus([])] == syms[:4]
    st.talked = {s_: at("10:00") for s_ in syms[:4]}
    assert [c["symbol"] for c in st._focus([])] == [*syms[4:], *syms[:2]]              # 뒤쪽 종목도 차례가 온다
    assert [c["symbol"] for c in st._focus(["035420"])][0] == "035420"                 # 계기의 종목이 먼저


def test_move_trigger_says_which_prices(client, conn, broker, at_open):  # noqa: F811
    prices = {"p": 100_000}
    p = SimpleNamespace(quotes=lambda syms: {s_: {"price": prices["p"]} for s_ in syms})
    st = _bare_stream(conn, p, ["005930"])
    assert st._moved() is None
    prices["p"] = 102_500
    assert "+2.5% (100,000원 → 102,500원," in st._moved()["text"]


def test_minutes_keep_the_gate_at_order_time(client, conn, broker, monkeypatch):  # noqa: F811
    """입장은 STALE_MIN 분이면 빠져 마감 때 다시 재면 닫혀 보인다 — 그래도 회의록은 주문할 때의 게이트, 주문은 지금 상태로 적는다."""
    clock = {"t": at("09:07")}
    monkeypatch.setattr(market, "now_kst", lambda: clock["t"])
    st = _bare_stream(conn, candidates=["005930"])
    for a in st.analysts:
        st.board.update({"agent": a["key"], "title": a["title"], "views": [{"symbol": "005930", "stance": "buy", "confidence": 0.7}]}, clock["t"])

    async def trader(*_a):
        return {"decisions": [{"symbol": "005930", "decision": "buy", "quantity": 1, "reason": "r"}], "rationale": "r"}

    def fake_session(self, focus, agenda):
        s = agents.Session(conn=conn, scope={"scope": "stream", "label": "x", "candidates": focus}, run_id=None, provider=self.p, gate_mode="supermajority",
                           auto_submit=True)
        s.orders["005930"] = {**_buy(conn, "005930", 1), "status": "open"}                # 낼 때 상태
        return s

    monkeypatch.setattr(agents, "LLM", trader)
    monkeypatch.setattr(stream.Stream, "_session", fake_session)
    asyncio.run(st.trade(clock["t"], "트레이더 제안"))
    assert [o["status"] for o in st.orders] == ["open"]
    clock["t"] = at("15:15")                                                           # 6시간 뒤 — 입장이 모두 빠졌다
    assert st.board.gate(["005930"], {}, clock["t"])["aligned"] == []
    scope, _, gate, _ = st.minutes_facts()
    row = gate["symbols"]["005930"]
    assert row["status"] == "aligned" and row["reason"].startswith("주문한 09:07 입장판 — 매수 무게")
    assert [c["symbol"] for c in scope["candidates"]] == ["005930"] and [o["status"] for o in st.orders] == ["filled"]


def _seat(st, votes, at_time):
    for a in st.analysts:
        if a["key"] in votes:
            st.board.update({"agent": a["key"], "title": a["title"], "views": [{"symbol": "005930", "stance": votes[a["key"]], "confidence": 0.7}]}, at_time)


def _opinions(answers, seen):
    """필수 의견 각본 — 에이전트 제목 → 입장(없으면 응답 실패). 묻는 대로 seen 에 남는다."""
    async def llm(feature, system, prompt, schema, tools, model, max_turns):
        title = re.search(r"당신은 '([^']+)' 에이전트", system).group(1)
        seen.append((title, schema is agents.POLL_SCHEMA))
        if title == "트레이더":
            return {"decisions": [], "rationale": "관망"}
        got = answers.get(title)
        if got is None:
            raise stream.LLMError("timeout")
        return {"views": [{"symbol": "005930", "stance": got, "confidence": 0.7, "reason": "근거"}], "thesis": "t", "evidence": [], "risks": []}
    return llm


def _titles(st):
    return {a["key"]: a["title"] for a in st.analysts}


def test_required_opinions_before_a_buy(client, conn, broker, at_open, monkeypatch):  # noqa: F811
    """3명만 말해 열려도 나머지 전원에게 묻고, 전원이 답해야 주문으로 간다 — 리스크 매니저가 빠지면 매수는 닫힌다."""
    now = at("10:00")
    st = _bare_stream(conn, candidates=["005930"])
    t = _titles(st)
    spoke = {"technical": "buy", "growth": "buy", "news": "buy"}
    _seat(st, spoke, now)
    seen = []
    answers = {t["value"]: "buy", t["portfolio"]: "buy", t["risk"]: "buy", "트레이더": "buy"}
    monkeypatch.setattr(agents, "LLM", _opinions(answers, seen))
    asyncio.run(st.trade(now, "트레이더 제안"))
    asked = [x for x, poll in seen if poll]
    assert sorted(asked) == sorted([t["value"], t["portfolio"], t["risk"]])            # 말하지 않은 사람만, 빠짐없이
    assert "트레이더" in [x for x, _ in seen]                                           # 전원 찬성 → 주문 단계로
    gate = next(e for e in st.chat if e["type"] == "gate")["gate"]
    assert gate["symbols"]["005930"]["status"] == "aligned"
    assert any(e["type"] == "round" and "필수 의견" in e["label"] for e in st.chat)

    st2 = _bare_stream(conn, candidates=["005930"])
    _seat(st2, spoke, now)
    seen2 = []
    monkeypatch.setattr(agents, "LLM", _opinions({t["value"]: "buy", t["portfolio"]: "buy"}, seen2))    # 리스크 매니저 응답 실패
    asyncio.run(st2.trade(now, "트레이더 제안"))
    assert "트레이더" not in [x for x, _ in seen2]                                      # 주문으로 가지 않는다
    row = st2.final_gate({"aligned": [{"symbol": "005930", "direction": "buy"}], "symbols": {}}, now)["symbols"]["005930"]
    assert row["status"] == "incomplete" and t["risk"] in row["reason"]


def test_none_is_not_accepted_when_an_opinion_is_required(client, conn, broker, at_open, monkeypatch):  # noqa: F811
    now = at("10:00")
    st = _bare_stream(conn, candidates=["005930"])
    t = _titles(st)
    _seat(st, {"technical": "buy", "growth": "buy", "news": "buy", "value": "buy", "portfolio": "buy"}, now)
    seen = []
    monkeypatch.setattr(agents, "LLM", _opinions({t["risk"]: "none"}, seen))
    asyncio.run(st.poll([{"symbol": "005930", "direction": "buy"}], "계기", now))
    assert [x for x, _ in seen] == [t["risk"]] * stream.POLL_TRIES                     # 다시 물었고
    assert st.board.final("005930", "buy", st.analysts, now)["status"] == "incomplete"  # 끝내 찬반이 없으면 매수는 닫힘


@pytest.mark.parametrize("votes,status,why", [
    ({"technical": ("buy", 0.6), "growth": ("buy", 0.6), "news": ("sell", 0.2), "value": ("sell", 0.2), "portfolio": ("buy", 0.2), "risk": ("sell", 0.1)},
     "aligned", "약한 반대 여럿은 막지 않는다 (1.0 이하)"),
    ({"technical": ("buy", 0.6), "growth": ("buy", 0.6), "news": ("buy", 0.5), "value": ("buy", 0.5), "portfolio": ("buy", 0.5), "risk": ("sell", 0.6)},
     "conflict", "주요 의견이 아무리 많아도 강한 반대 하나면 막는다 (반대 기준이 보수적)"),
    ({"technical": ("buy", 0.4), "growth": ("buy", 0.3), "news": ("sell", 0.2), "value": ("buy", 0.1), "portfolio": ("buy", 0.1), "risk": ("sell", 0.1)},
     "conflict", "반대가 적어도 주요 의견이 기준 아래면 주문하지 않는다"),
])
def test_buy_needs_main_weight_and_little_opposition(client, conn, broker, at_open, votes, status, why):  # noqa: F811
    st = _bare_stream(conn, candidates=["005930"])
    now = at("10:00")
    for a in st.analysts:
        stance, conf = votes[a["key"]]
        st.board.update({"agent": a["key"], "title": a["title"], "views": [{"symbol": "005930", "stance": stance, "confidence": conf}]}, now)
    assert st.board.final("005930", "buy", st.analysts, now)["status"] == status, why


def test_selling_is_not_blocked_by_silence_or_a_minority(client, conn, broker, at_open):  # noqa: F811
    """위험을 줄이는 주문은 더 어렵게 하지 않는다 — 응답 실패가 있어도, 반대가 기준을 넘어도 매도가 더 무거우면 열린다."""
    st = _bare_stream(conn, candidates=["005930"])
    now = at("10:00")
    _seat(st, {"technical": "sell", "growth": "sell", "risk": "sell", "news": "buy"}, now)
    assert st.board.final("005930", "sell", st.analysts, now)["status"] == "aligned"
    st2 = _bare_stream(conn, candidates=["005930"])
    _seat(st2, {"technical": "sell", "growth": "buy", "risk": "buy", "news": "sell"}, now)
    assert st2.board.final("005930", "sell", st2.analysts, now)["status"] == "conflict"


def test_cooldown_blocks_the_same_direction_only(client, conn, broker, at_open, monkeypatch):  # noqa: F811
    """산 뒤 쉬는 시간에도 바로 팔 수는 있어야 한다 (손절)."""
    now = at("10:00")
    st = _bare_stream(conn, candidates=["005930"])
    _buy(conn, "005930")                                                               # 방금 산 종목
    _seat(st, {a["key"]: "sell" for a in st.analysts}, now)
    st.last_order[("005930", "buy")] = now
    seen = []
    monkeypatch.setattr(agents, "LLM", _opinions({}, seen))
    asyncio.run(st.trade(now, "급변"))
    assert "트레이더" in [x for x, _ in seen]
    st.last_order[("005930", "sell")] = now
    seen.clear()
    asyncio.run(st.trade(now, "급변"))
    assert seen == []


def test_trader_is_told_to_act_when_the_gate_opens(client, conn, broker, at_open):  # noqa: F811
    st = _bare_stream(conn, candidates=["005930"])
    s = st._session(st._focus(["005930"]), "x")
    gate = {"aligned": [{"symbol": "005930", "direction": "buy"}], "symbols": {"005930": {"reason": "r"}}}
    assert "관망(hold)은 한도·현금·호가·찬성 의견의 가격 조건 때문에 주문이 불가능할 때만" in stream.trade_prompt(s, gate, "없음", "계기", 3)


def test_track_scores_judge_against_the_market(client, conn, broker, at_open):  # noqa: F811
    """성과는 코스피 대비 방향 — 같은 판단을 되풀이해도 한 번, 끝난 거래일만, 표본이 적으면 1 근처."""
    days = ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06"]
    closes = {"AAA": [100, 110, 120, 130], "BBB": [100, 98, 96, 94]}
    p = SimpleNamespace(candles=lambda sym, *_a: [{"t": d, "c": c} for d, c in zip(days, closes[sym])],
                        indicator_closes=lambda *_a: [{"t": d, "c": c} for d, c in zip(days, [100, 101, 102, 103])])
    now = at("10:00").replace(year=2026, month=10, day=6)
    st = {"agent": "a", "title": "A", "views": [{"symbol": "AAA", "stance": "buy", "confidence": 0.6}, {"symbol": "BBB", "stance": "buy", "confidence": 0.6}]}
    first = now.replace(day=1)
    track.record(conn, st, first, None, {"AAA": 100, "BBB": 100})
    track.record(conn, st, first.replace(hour=11), None, {"AAA": 105, "BBB": 105})     # 같은 날 되풀이 — 처음 판단만
    track.record(conn, {"agent": "b", "title": "B", "views": [{"symbol": "BBB", "stance": "sell", "confidence": 0.6},
                                                              {"symbol": "AAA", "stance": "none", "confidence": 0.9}]}, first, None, {"BBB": 100})
    track.record(conn, st, now, None, {"AAA": 130, "BBB": 94})                         # 오늘 판단은 아직 성적 없음
    got = track.scores(conn, p, now)
    assert got["a"]["judged"] == 2 and got["a"]["wins"] == 1 and got["a"]["skill"] == 1.0
    assert got["b"]["judged"] == 1 and got["b"]["wins"] == 1 and got["b"]["skill"] == track.skill_of(1, 1) > 1
    assert track.skill_of(100, 100) == track.MAX_SKILL and track.skill_of(0, 100) == track.MIN_SKILL      # 혼자 표를 쥐지 못한다
    assert track.weight(0.5) == 1.0 and track.weight(0.9, 1.3) == 2.34 and track.weight(None) == 0


def test_track_influence_is_weight_share_and_order_agreement(client, conn):  # noqa: F811
    """실제 표의 무게 = 확신 ÷ 0.5 × 성과 배수, 비중은 전체 무게 가운데 몫. 나간 에이전트 주문과 같은 편 · 반대편을 센다."""
    now = at("10:00").replace(year=2026, month=10, day=6)
    track.record(conn, {"agent": "a", "views": [{"symbol": "AAA", "stance": "buy", "confidence": 0.5},
                                                {"symbol": "BBB", "stance": "buy", "confidence": 1.0}]}, now, None, {})
    track.record(conn, {"agent": "b", "views": [{"symbol": "AAA", "stance": "sell", "confidence": 0.5}]}, now, None, {})
    for status in ("filled", "rejected"):  # 거절된 주문은 '나간 주문'이 아니다
        conn.execute("INSERT INTO invest_orders (created_at, symbol, side, order_type, quantity, status, source) "
                     "VALUES ('2026-10-06 10:05:00', ?, 'buy', 'market', 1, ?, 'agent')", ("AAA" if status == "filled" else "BBB", status))
    conn.commit()
    got = track.influence(conn, {"a": 1.2})
    assert got["a"]["views"] == 2 and got["a"]["buy"] == 2 and got["a"]["avg_confidence"] == 0.75
    assert got["a"]["avg_weight"] == round((1.2 + 2.4) / 2, 3)          # 성과 배수 1.2 를 곱한다
    assert got["b"]["avg_weight"] == 1.0                                 # 성적이 없으면 배수 1
    assert got["a"]["share"] == round(3.6 / 4.6, 3) and got["b"]["share"] == round(1.0 / 4.6, 3)
    assert (got["a"]["with_orders"], got["a"]["against_orders"]) == (1, 0)  # AAA 매수 주문과 같은 편, BBB 는 거절이라 안 셈
    assert (got["b"]["with_orders"], got["b"]["against_orders"]) == (0, 1)
    assert track.influence(conn, {}) and track.influence(conn, {})["a"]["avg_weight"] == 1.5


def test_agent_scores_api_carries_influence_and_rules(client, conn, broker):  # noqa: F811
    track.record(conn, {"agent": "risk", "views": [{"symbol": "005930", "stance": "sell", "confidence": 0.8}]}, at("10:00"), None, {})
    body = client.get("/api/invest/agent-scores").json()
    skill = body["scores"].get("risk", {}).get("skill", 1.0)                           # 가짜 시세로 채점됐으면 그 배수
    assert body["influence"]["risk"]["avg_weight"] == track.weight(0.8, skill) and body["influence"]["risk"]["share"] == 1.0
    assert body["rules"]["for_min"] == stream.FOR_MIN and body["rules"]["conviction_unit"] == 0.5 and body["rules"]["mode"] in ("continuous", "meetings")


def test_track_backfills_past_talks_once(client, conn):  # noqa: F811
    import json
    from app.core.db import data_dir
    rid = conn.execute("INSERT INTO runs (kind, name, status, created_at) VALUES ('agent_action', 'x', 'succeeded', '2026-10-30 09:00:00')").lastrowid
    folder = data_dir() / "lab" / "runs" / str(rid)
    folder.mkdir(parents=True, exist_ok=True)
    talk = [{"type": "statement", "ts": "09:10:00", "agent": "technical", "views": [{"symbol": "005930", "stance": "buy", "confidence": 0.6},
                                                                                    {"symbol": "000660", "stance": "none", "confidence": 0.5}]},
            {"type": "statement", "ts": "09:11:00", "agent": "trader", "executor": True, "views": [{"symbol": "005930", "stance": "buy", "confidence": 0.9}]},
            {"type": "statement", "ts": "09:12:00", "agent": "news", "error": "x", "views": [{"symbol": "005930", "stance": "buy", "confidence": 0.9}]}]
    (folder / "messages.jsonl").write_text("\n".join(json.dumps(m) for m in talk), encoding="utf-8")
    conn.execute("UPDATE runs SET artifact_path = ? WHERE id = ?", (str(folder.relative_to(data_dir())), rid))
    conn.commit()
    assert track.backfill(conn) == 1 and track.backfill(conn) == 0                     # 트레이더·응답 실패·의견 없음은 뺀다
    row = conn.execute("SELECT agent, day, at, price FROM invest_agent_views").fetchone()
    assert tuple(row) == ("technical", "2026-10-30", "2026-10-30 09:10:00", None)


def test_research_desk_answers_and_adds_to_watch(client, conn, broker, at_open, monkeypatch):  # noqa: F811
    now = at("10:00")
    st = _bare_stream(conn, candidates=["005930"])
    calls = []

    async def llm(feature, system, prompt, schema, tools, model, max_turns):
        calls.append((feature, system, prompt))
        assert schema is stream.RESEARCH_SCHEMA and "place_order" not in {t.name for t in tools}   # 리서치 담당에게 주문 도구는 없다
        return {"reply": "차티스트 부탁 확인했어요. 구성 비중 자료는 없어요.", "watch": ["069500", "005930", "abc", "999999"]}

    monkeypatch.setattr(agents, "LLM", llm)
    monkeypatch.setattr(SampleMarket, "info", lambda self, sym: {"symbol": sym, "name": "KODEX 200", "currency": "KRW", "suspended": False, "security_type": "ETF"}
                        if sym == "069500" else (_ for _ in ()).throw(market.MarketError("없음")))
    stmt = {"title": "차티스트", "message_to_others": "트레이더: 관망하세요\n\n탄디: 069500 을 볼 종목에 올려 주세요.\n\n시스템 확인 요청: 기타"}
    assert stream.research_asks([stmt]) == {"scout": ["차티스트: 069500 을 볼 종목에 올려 주세요."]}
    asyncio.run(st.answer_research([stmt], now))
    assert len(calls) == 1 and calls[0][0] == "invest_desk" and "탄디" in calls[0][1]
    assert [c["symbol"] for c in st.candidates] == ["005930", "069500"]                 # 잘못된 코드·이미 있는 종목은 코드가 거른다
    assert st.queue[0]["symbols"] == ["069500"] and st.answers[0].startswith("탄디(")     # 바로 대화, 다음 프롬프트에 답이 들어간다
    said = [m for m in assistant.messages(conn) if m["role"] == "agent"][-1]
    assert said["meta"]["name"] == "탄디" and "볼 종목에 올렸어요: KODEX 200" in said["text"]
    asyncio.run(st.answer_research([stmt], now + timedelta(minutes=1)))
    assert len(calls) == 1                                                              # 같은 담당은 ASK_GAP_MIN 분에 한 번
    prompt = stream.stream_prompt(st._session(st.candidates, "x"), 3, {"text": "t"}, [], "없음", st.answers)
    assert "리서치 담당의 답" in prompt and "'탄디: 부탁'" in prompt


def test_stream_reopens_after_server_restarts_without_using_up_retries(client, conn, broker, monkeypatch):
    """서버 재시작으로 끊긴 채널은 다시 열기 횟수에 세지 않는다 — 하루에 몇 번 재시작해도 계속 운용."""
    _continuous(conn, broker, monkeypatch, "10:00")
    monkeypatch.setattr(stream.Stream, "run", lambda self, notes: (_ for _ in ()).throw(RuntimeError("x")))
    for i in range(autopilot.MAX_STREAMS + 2):
        rec = autopilot.tick(conn, at(f"10:{i:02d}"), SampleMarket())
        assert rec and rec["status"] == "started"
        conn.execute("UPDATE runs SET error = ? WHERE id = ?", (INTERRUPTED, rec["run_id"]))  # 재시작이 끊은 것처럼
        conn.commit()


def test_resume_reopens_a_stopped_stream_and_counts_retries_again(client, conn, broker, monkeypatch):
    _continuous(conn, broker, monkeypatch, "10:00")
    monkeypatch.setattr(stream.Stream, "run", lambda self, notes: (_ for _ in ()).throw(RuntimeError("x")))
    for i in range(autopilot.MAX_STREAMS):
        autopilot.tick(conn, at(f"10:{i:02d}"), SampleMarket())
    assert autopilot.tick(conn, at("10:05"), SampleMarket()) is None                          # 오류로 거듭 끊겨 멈춤
    st = autopilot.status(conn, at("10:05"), SampleMarket())["stream"]
    assert st["state"] == "stopped" and st["can_resume"] and "오류로" in st["note"]
    assert autopilot.status(conn, at("08:00"), SampleMarket())["stream"]["can_resume"] is False  # 자동 판단 시간 밖

    run_id = autopilot.resume_stream(conn, at("10:06"), SampleMarket())                       # 재개 → 다시 셈
    assert load_run(conn, run_id)["status"] == "failed"
    rec = autopilot.tick(conn, at("10:07"), SampleMarket())
    assert rec and rec["status"] == "started"

    conn.execute("UPDATE runs SET status = 'cancelled' WHERE id = ?", (rec["run_id"],))       # 관리자가 중지 → 스스로 안 엶
    conn.commit()
    assert autopilot.tick(conn, at("10:08"), SampleMarket()) is None
    assert autopilot.status(conn, at("10:08"), SampleMarket())["stream"]["note"] == "관리자가 채널을 중지했어요"
    assert autopilot.resume_stream(conn, at("10:09"), SampleMarket())

    autopilot.save_config(conn, mode="meetings")
    with pytest.raises(autopilot.AutopilotError):
        autopilot.resume_stream(conn, at("10:10"), SampleMarket())
    assert autopilot.status(conn, at("10:10"), SampleMarket())["stream"] is None


def test_resume_route_needs_no_pin_but_checks_the_window(client, conn, broker, monkeypatch):
    _continuous(conn, broker, monkeypatch, "08:00")
    r = client.post("/api/invest/autopilot/resume")
    assert r.status_code == 409 and "자동 판단 시간" in r.json()["detail"]
