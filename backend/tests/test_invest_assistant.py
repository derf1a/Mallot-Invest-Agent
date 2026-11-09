"""투자 비서: 내 말에 답하기, 운용 지시 정리(더하기·고치기·지우기·이번만), 다른 에이전트에게 묻기, 즉시 지시로 판단 시작, PIN·실패."""

import re
from datetime import datetime

import pytest

from app.invest import assistant
from app.invest.ops import autopilot
from app.invest.debate import agents
from app.invest.marketdata import market
from app.invest.marketdata.market import KST
from app.llm.client import LLMError
from tests.test_invest import PIN, at_open  # noqa: F401 — 장중 시각 고정 fixture


class Fake:
    """비서 각본: plan 의 도구를 차례로 부르고 reply 로 답한다. 분석 에이전트로 불리면(ANSWER_SCHEMA) 첫 도구를 한 번 쓰고 답한다."""

    def __init__(self, plan: list[tuple[str, dict]], reply: str = "네, 정리했어요"):
        self.plan = plan
        self.reply = reply
        self.prompts: list[str] = []
        self.results: list[object] = []
        self.features: list[str] = []

    async def __call__(self, feature, system, prompt, schema, tools, model, max_turns):
        self.features.append(feature)
        by = {t.name: t for t in tools}
        if schema is assistant.ANSWER_SCHEMA:
            title = re.search(r"당신은 '([^']+)' 에이전트", system).group(1)
            await next(iter(by.values())).handler({"symbol": "000660"})
            return {"answer": f"{title}: 20일선 위, 추세는 아직 살아 있어요"}
        self.prompts.append(prompt)
        for name, args in self.plan:
            self.results.append(await by[name].handler(args))
        return {"reply": self.reply}


def say(client, text, headers=PIN):
    return client.post("/api/invest/assistant/messages", json={"text": text}, headers=headers)


def thread(client):
    return client.get("/api/invest/assistant/messages").json()["messages"]


def test_assistant_organizes_intents(client, broker, at_open, monkeypatch):
    fake = Fake([("list_intents", {}), ("add_intent", {"text": "반도체 섹터 주목"}),
                 ("add_intent", {"text": "SK하이닉스는 다음 판단에서 집중해서 봐 줘", "once": True})])
    monkeypatch.setattr(assistant, "LLM", fake)
    r = say(client, "반도체 좀 주목해 주고, 다음엔 하이닉스 집중해서 봐 줘")
    assert r.status_code == 201 and r.json()["message"]["role"] == "user"
    msgs = thread(client)
    assert [m["role"] for m in msgs] == ["user", "event", "event", "assistant"]
    assert msgs[2]["text"] == "운용 지시 추가 (이번만): SK하이닉스는 다음 판단에서 집중해서 봐 줘"
    assert msgs[3]["text"] == "네, 정리했어요" and "add_intent" in msgs[3]["meta"]["tools"]
    assert [(i["text"], i["once"]) for i in client.get("/api/invest/autopilot").json()["intents"]] == [
        ("반도체 섹터 주목", False), ("SK하이닉스는 다음 판단에서 집중해서 봐 줘", True)]
    assert fake.features == ["invest_assistant"] and "관리자의 새 메시지:\n반도체 좀 주목해 주고" in fake.prompts[0]

    fake2 = Fake([("update_intent", {"id": 1, "text": "반도체·2차전지 주목"}), ("remove_intent", {"id": 2}),
                  ("remove_intent", {"id": 99})], reply="반도체에 2차전지도 더했어요")
    monkeypatch.setattr(assistant, "LLM", fake2)
    say(client, "2차전지도 같이 봐 주고 하이닉스 얘기는 취소")
    prompt = fake2.prompts[0]
    assert "- [1] 반도체 섹터 주목" in prompt and "(이번만)" in prompt          # 지금 목록이 프롬프트에
    assert "나: 반도체 좀 주목해 주고" in prompt and "서윤(비서): 네, 정리했어요" in prompt  # 최근 대화
    assert fake2.results[2] == {"ok": False, "error": "없는 운용 지시예요"}
    events = [m["text"] for m in thread(client) if m["role"] == "event"][2:]
    assert events == ["운용 지시 고침: 반도체·2차전지 주목", "운용 지시 지움: SK하이닉스는 다음 판단에서 집중해서 봐 줘"]
    assert [i["text"] for i in autopilot.intents(_conn(client))] == ["반도체·2차전지 주목"]


def _conn(client):
    from app.core.db import connect
    return connect()


def test_assistant_asks_another_agent(client, broker, at_open, monkeypatch):
    fake = Fake([("ask_agent", {"agent": "차티스트", "question": "하이닉스 지금 어때?"}),
                 ("ask_agent", {"agent": "점쟁이", "question": "?"}), ("get_quote", {"symbol": "000660"})],
                reply="차티스트는 추세가 살아 있다고 봐요")
    monkeypatch.setattr(assistant, "LLM", fake)
    say(client, "하이닉스 지금 어때? 차트 쪽 의견도 듣고 싶어")
    msgs = thread(client)
    agent = next(m for m in msgs if m["role"] == "agent")
    assert agent["meta"]["title"] == "차티스트" and agent["meta"]["name"] == "켄지" and agent["text"].startswith("차티스트: 20일선 위")
    assert agent["meta"]["tools"]                                              # 그 에이전트가 자기 도구를 썼다
    assert fake.results[1]["ok"] is False and "차티스트(technical)" in fake.results[1]["error"]
    assert fake.results[2]["symbol"] == "000660" and msgs[-1]["role"] == "assistant"
    assert "get_quote" in msgs[-1]["meta"]["tools"] and "ask_agent:technical" in msgs[-1]["meta"]["tools"]


def test_assistant_starts_session_now(client, broker, at_open, monkeypatch):
    started: list[dict] = []

    def fake_start_run(conn, kind, params, name=""):
        started.append({"kind": kind, "name": name, **params})
        return 70 + len(started)

    monkeypatch.setattr(autopilot, "start_run", fake_start_run)
    client.put("/api/invest/autopilot", json={"enabled": True}, headers=PIN)
    autopilot.add_intent(_conn(client), "반도체 주목")
    monkeypatch.setattr(assistant, "LLM", Fake([("start_now", {"instruction": "지금 하이닉스 조금 사 줘"})], reply="바로 판단할게요"))
    say(client, "지금 하이닉스 조금 사 줘")
    ev = next(m for m in thread(client) if m["role"] == "event")
    assert ev["meta"] == {"kind": "session_started", "run_id": 71, "autopilot": True}
    assert started[0]["autopilot"] and started[0]["note"] == "지금 하이닉스 조금 사 줘" and started[0]["trigger"] == "assistant"
    assert started[0]["name"].startswith("자동 운용 · 11:30")

    client.put("/api/invest/autopilot", json={"enabled": False})            # 꺼져 있으면 승인이 필요한 보통 토론
    say(client, "지금 하이닉스 조금 사 줘")
    assert "autopilot" not in started[1] and started[1]["scope"] == "autopilot"
    assert started[1]["intents"] == ["반도체 주목", "지금 하이닉스 조금 사 줘"] and started[1]["name"] == "비서 요청 · 11:30"
    assert [m for m in thread(client) if m["role"] == "event"][-1]["meta"]["autopilot"] is False

    monkeypatch.setattr(market, "now_kst", lambda: datetime(2026, 10, 13, 18, 0, tzinfo=KST))  # 장 끝남
    fake = Fake([("start_now", {"instruction": "지금 사 줘"})], reply="지금은 장이 끝났어요")
    monkeypatch.setattr(assistant, "LLM", fake)
    say(client, "지금 사 줘")
    assert fake.results[0]["ok"] is False and "자동 판단 시간" in fake.results[0]["error"] and len(started) == 2


def test_assistant_pin_and_failure(client, broker, at_open, monkeypatch):
    assert say(client, "안녕", headers={}).status_code == 403
    assert thread(client) == []

    async def broken(*a, **k):
        raise LLMError("timeout")

    monkeypatch.setattr(assistant, "LLM", broken)
    say(client, "안녕")
    msgs = thread(client)
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["meta"]["error"] == "timeout"
    assert client.get("/api/invest/assistant/messages", params={"after": msgs[0]["id"]}).json()["messages"] == msgs[1:]
    assert client.get("/api/invest/assistant/messages").json()["pending"] is False


def test_assistant_tools_and_feature_registered():
    from app.llm import client
    assert client.FEATURES["invest_assistant"] == "투자 비서"
    assert set(assistant.MARKET_TOOLS) <= set(agents.TOOL_INFO) and agents.EXECUTOR_TOOL not in assistant.MARKET_TOOLS


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_message_rejected(client, broker, bad):
    assert say(client, bad).status_code in (400, 422)


def test_agents_have_multinational_names(client):
    data = client.get("/api/invest/agents").json()
    people = {a["key"]: (a["name"], a["full_name"], a["country"]) for a in data["roster"]}
    assert people["technical"] == ("켄지", "Kenji Watanabe", "일본") and people["trader"][2] == "멕시코"
    assert len({p[2] for p in people.values()}) == len(people)                    # 모두 다른 나라
    assert data["assistant"]["name"] == "서윤" and data["assistant"]["title"] == "비서" and data["assistant"]["pokemon"] == 113
    assert {a["key"]: a["pokemon"] for a in data["roster"]}["trader"] == 25                 # 피카츄
    roster = [{k: v for k, v in a.items() if k not in ("name", "full_name", "country")} for a in data["roster"]]
    roster.append({"key": "macro", "title": "매크로 분석가", "persona": "금리·환율", "tools": ["get_market_overview"]})
    saved = agents.validate_roster(roster)                                         # 이름이 없으면 채운다
    macro = saved[-1]
    assert macro["name"] and macro["name"] not in {p[0] for p in people.values()} and macro["country"]
    assert agents.validate_roster(roster)[-1]["name"] == macro["name"]             # 키로 고정
    assert macro["pokemon"] in agents.POKEMON_POOL
    assert agents.validate_roster([*saved[:-1], {**macro, "pokemon": 6}])[-1]["pokemon"] == 6
    assert agents.validate_roster([*saved[:-1], {**macro, "pokemon": 99999}])[-1]["pokemon"] == macro["pokemon"]
    with pytest.raises(agents.AgentError, match="관리자"):
        agents.validate_roster([*saved[:-1], {**macro, "name": "관리자"}])
    s = agents.Session(conn=None, scope={"label": "x", "candidates": []}, run_id=None, provider=None)
    assert "이름은 켄지(Kenji Watanabe, 일본)입니다" in agents.system_prompt(saved[0], s)


def test_meeting_minutes_written_by_assistant_or_code(client, broker, at_open, monkeypatch):
    from app.invest.debate import minutes
    from tests.test_invest_ai import ALIGNED, Script
    monkeypatch.setattr(agents, "LLM", Script(ALIGNED, trade={"side": "buy", "quantity": 1, "order_type": "market", "reason": "합의"}))
    seen = {}

    async def writer(feature, system, prompt, schema, model):
        seen.update(feature=feature, prompt=prompt)
        return {"headline": "삼성전자 매수 1주 · 승인 대기", "summary": "켄지와 프리야가 매수, 아마라는 처음엔 반대했다가 의견 없음으로.",
                "points": ["켄지(차티스트): 추세 유지"], "follow_up": ["승인 여부 확인"]}

    monkeypatch.setattr(minutes, "LLM", writer)
    run = client.post("/api/invest/agent-sessions", json={"symbol": "005930", "rounds": 2}, headers=PIN).json()
    assert run["summary"]["minutes"] == {"headline": "삼성전자 매수 1주 · 승인 대기", "kind": "수동 회의",
                                          "summary": "켄지와 프리야가 매수, 아마라는 처음엔 반대했다가 의견 없음으로."}
    msgs = client.get(f"/api/invest/agent-sessions/{run['id']}/messages").json()["messages"]
    card = next(m for m in msgs if m["type"] == "minutes")
    assert card["by"] == "assistant" and card["points"] == ["켄지(차티스트): 추세 유지"]
    assert [m["type"] for m in msgs][-2:] == ["minutes", "end"]
    p = seen["prompt"]
    assert seen["feature"] == "invest_assistant" and "켄지(차티스트)" in p and "삼성전자(005930): 매수" in p and "주문 매수 1주" in p
    start = msgs[0]
    assert {a["key"]: a["name"] for a in start["agents"]}["risk"] == "아마라" and start["agents"][0]["pokemon"] == 65

    async def offline(*a, **k):  # 비서가 회의록을 못 쓰면 코드가 정리
        raise LLMError("x")

    monkeypatch.setattr(agents, "LLM", Script(ALIGNED))
    monkeypatch.setattr(minutes, "LLM", offline)
    run2 = client.post("/api/invest/agent-sessions", json={"symbol": "005930", "rounds": 1}, headers=PIN).json()
    m2 = run2["summary"]["minutes"]
    assert m2["headline"] == "주문 없음" and "게이트가 열린 종목이 없어 관망했어요" in m2["summary"]   # 1라운드: 리스크 매니저 반대
    assert m2["summary"].startswith("1종목을 논의했어요 (참석: 켄지(차티스트)")


def test_performance_and_briefing(client, broker, at_open):
    from app.invest.ops import briefing
    from app.invest.broker import trading
    from app.core.db import connect
    conn = connect()
    trading.place_order(conn, symbol="005930", side="buy", order_type="market", quantity=3, source="agent", run_id=None,
                        auto_submit=True)
    trading.place_order(conn, symbol="000660", side="buy", order_type="market", quantity=1, source="manual", confirm_live=True)
    perf = client.get("/api/invest/performance").json()
    pf, ai = perf["portfolio"], perf["ai"]
    assert pf["positions"] == 2 and pf["total_assets_krw"] > pf["stock_value_krw"] > 0 and pf["cost_krw"] > 0
    assert pf["return_rate"] == round(pf["pnl_krw"] / pf["cost_krw"], 4) and pf["top"][0]["value_krw"] >= pf["top"][1]["value_krw"]
    assert ai["count"] == 1 and ai["orders"][0]["symbol"] == "005930" and ai["orders"][0]["change"] is not None  # 수동 주문은 빼고
    assert ai["amount_krw"] == round(3 * ai["orders"][0]["fill"])
    autopilot.add_intent(conn, "반도체 주목")
    b = client.get("/api/invest/briefing").json()
    assert b["title"] == "투자 현황 보고" and b["v"] == 2 and "plan" not in b and "intents" not in b    # 설정·운용 지시는 보고서에 없음
    hs = b["holdings"]
    assert {x["symbol"] for x in hs} == {"005930", "000660"} and hs[0]["value_krw"] >= hs[1]["value_krw"]       # 평가 금액 큰 순
    assert all(x["day_change"] is not None and x["weight"] for x in hs)
    assert b["market"]["indicators"] and b["warnings"] == []
    t = b["text"]
    assert "[보유 종목·현황]" in t and "[회의 요약]" in t and "[장내 이슈]" in t and "- 지표: " in t
    assert "[운영 플랜]" not in t and "반도체 주목" not in t and "삼성전자 3주 " in t and " · 오늘 " in t
    assert briefing.to_text({**b, "comment": "반도체 비중을 볼 때예요"}).splitlines()[2] == "반도체 비중을 볼 때예요"


def test_report_summarizes_meetings_and_market_issues(client, broker, at_open, monkeypatch):
    """회의 요약(회의록·주문·다음에 볼 것, 진행 중인 채널은 지금 입장, 끊긴 회의는 뺌)과 장내 이슈(시장 뉴스·보유 종목 뉴스)."""
    import json
    from app.core.db import connect, data_dir
    from app.invest.ops import briefing
    from app.invest.marketdata import news
    from app.invest.broker import trading
    from tests.test_invest_ai import RSS
    conn = connect()
    monkeypatch.setattr(news, "HTTP", lambda *_a, **_k: (200, RSS.encode()))

    def run(name, status, minutes=None, files=None):
        rid = conn.execute("INSERT INTO runs (kind, name, status, summary_json) VALUES ('agent_action', ?, ?, ?)",
                           (name, status, json.dumps({"minutes": minutes} if minutes else {}, ensure_ascii=False))).lastrowid
        folder = data_dir() / "lab" / "runs" / str(rid)
        folder.mkdir(parents=True, exist_ok=True)
        for fname, body in (files or {}).items():
            (folder / fname).write_text(body, encoding="utf-8")
        conn.execute("UPDATE runs SET artifact_path = ? WHERE id = ?", (str(folder.relative_to(data_dir())), rid))
        conn.commit()
        return rid

    done = run("자동 운용 · 09:10", "succeeded", {"headline": "삼성전자 매수 3주", "summary": "반도체 회복에 다들 동의했어요.", "kind": "정기 회의"},
               {"minutes.json": json.dumps({"follow_up": ["삼성전자 실적 발표 확인"]}, ensure_ascii=False)})
    trading.place_order(conn, symbol="005930", side="buy", order_type="market", quantity=3, source="agent", run_id=done, auto_submit=True)
    run("상시 운용", "failed")                                                      # 서버 재시작으로 끊긴 채널 — 회의록도 주문도 없음
    talk = [{"type": "round", "seq": 1, "candidates": [{"symbol": "000660", "name": "SK하이닉스"}]}]
    talk += [{"type": "statement", "seq": 2 + i, "agent": k, "views": [{"symbol": "000660", "stance": st}]}
             for i, (k, st) in enumerate((("a", "buy"), ("b", "buy"), ("c", "sell")))]
    talk += [{"type": "statement", "seq": 8, "agent": "t", "executor": True, "views": [{"symbol": "000660", "stance": "sell"}]},
             {"type": "decision", "seq": 9, "decision": {"rationale": "같은 종목 쉬는 시간이라 관망"}}]
    live = run("상시 운용", "running", files={"messages.jsonl": "\n".join(json.dumps(m, ensure_ascii=False) for m in talk)})
    news.collect(conn, [{"symbol": "005930", "name": "삼성전자"}], False)              # 에이전트가 모아 둔 보유 종목 뉴스

    snap = briefing.snapshot(conn)
    ms = snap["meetings"]
    assert [m["id"] for m in ms] == [live, done]
    assert ms[0]["live"] == {"talks": 1, "board": [{"symbol": "000660", "name": "SK하이닉스", "buy": 2, "sell": 1}],
                             "trader": "같은 종목 쉬는 시간이라 관망"}                     # 트레이더 말은 입장 수에 안 셈
    assert ms[1]["follow_up"] == ["삼성전자 실적 발표 확인"] and ms[1]["summary"] == "반도체 회복에 다들 동의했어요."
    assert [(o["symbol"], o["status"]) for o in ms[1]["orders"]] == [("005930", "filled")] and ms[1]["orders"][0]["change"] is not None
    mk = snap["market"]
    assert [n["title"] for n in mk["news"]] == ["코스피 상승 마감", "삼성전자, 신제품 공개"]
    assert [n["name"] for n in mk["holding_news"]] == ["삼성전자", "삼성전자"] and mk["view"] is None   # 리서치 전엔 시황 없음
    t = briefing.to_text(snap)
    assert "상시 운용 (진행 중) — 대화 1번 · 주문 0건" in t and "지금 입장: SK하이닉스 매수 1·매도 1" not in t
    assert "지금 입장: SK하이닉스 매수 2·매도 1" in t and "트레이더: 같은 종목 쉬는 시간이라 관망" in t
    assert "— 삼성전자 매수 3주" in t and "주문: 삼성전자 매수 3주 (체결, 주문 뒤 " in t and "다음에 볼 것: 삼성전자 실적 발표 확인" in t
    assert "- 뉴스: 코스피 상승 마감 (연합뉴스 " in t and "- 삼성전자 뉴스: 삼성전자, 신제품 공개 (한국경제 " in t


def test_report_history_and_sector_rates(client, broker, at_open, monkeypatch):
    """수익률 추이: 지나간 보고서(기준 원금 시작일이 같은 것)의 값 + 지금, 지수는 시작 전 종가 대비. 섹터 의견에는 기간 등락."""
    from datetime import timedelta
    from app.core.db import connect
    from app.invest import policy
    from app.invest.ops import briefing, desk
    from app.invest.marketdata import market, news
    from app.invest.marketdata.market import MarketError, SampleMarket
    from tests.test_invest_ai import RSS
    conn = connect()
    monkeypatch.setattr(news, "HTTP", lambda *_a, **_k: (200, RSS.encode()))
    assert briefing.snapshot(conn)["history"] is None                                # 기준 원금을 정하기 전엔 없음

    since = (market.now_kst().date() - timedelta(days=30)).isoformat()
    briefing.set_baseline(conn, [{"date": since, "amount": 5_000_000, "note": "처음"}])
    closes = market.provider().indicator_closes("KOSPI", 250)
    base, level = next(c["c"] for c in reversed(closes) if c["t"] < since), closes[-5]["c"]
    old = lambda at, start, rate: {"created_at": at, "performance": {"baseline": {"since": start, "return_rate": rate}},  # noqa: E731
                                   "market": {"indicators": [{"name": "코스피", "price": level}]}}
    assistant.add(conn, "event", "다시 정하기 전", kind="report", report=old("2026-01-02 08:30", "2026-01-01", 0.5))    # 기준이 달라 빠짐
    assistant.add(conn, "event", "보고", kind="report", report=old(f"{since} 16:00", since, 0.01))
    assistant.add(conn, "event", "회의 시각을 옮겼어요", kind="setting")                                                # 보고서가 아님

    snap = briefing.snapshot(conn)
    h, now = snap["history"], snap["performance"]["baseline"]["return_rate"]
    assert h["index"] == "코스피" and len(h["points"]) == 3
    assert h["points"][0] == {"at": since, "rate": 0.0, "index": 0.0}                # 출발점
    assert h["points"][1] == {"at": f"{since} 16:00", "rate": 0.01, "index": round(level / base - 1, 4)}
    kospi = next(i for i in snap["market"]["indicators"] if i["name"] == "코스피")
    assert h["points"][2] == {"at": snap["created_at"], "rate": now, "index": round(kospi["price"] / base - 1, 4)}

    policy.save(conn, {"benchmark": "KOSDAQ"}, by="assistant")                       # 벤치마크를 정하면 그 지수로 — 코스닥이 없던 보고서는 비어 있음
    h = briefing.snapshot(conn)["history"]
    assert h["index"] == "코스닥" and h["points"][1]["index"] is None and h["points"][2]["index"] is not None

    def down(*_a):
        raise MarketError("지수 일봉을 못 받음")
    monkeypatch.setattr(SampleMarket, "indicator_closes", down)                      # 지수를 못 받아도 내 수익률 선은 남는다
    h = briefing.snapshot(conn)["history"]
    assert h["index"] is None and [p["rate"] for p in h["points"]] == [0.0, 0.01, now] and all(p["index"] is None for p in h["points"])

    desk.save(conn, "sector", {"overweight": [{"id": "1", "name": "반도체", "reason": "1주 +11%로 강해요. 뒤 문장은 줄여요."}],
                               "underweight": [{"id": "2", "name": "게임", "reason": "약해요"}, {"id": "3", "name": "표에 없음"}],
                               "rows": [{"id": "1", "rates": {"1w": 0.11}}, {"id": "2", "rates": {"1mo": -0.12}}]})
    snap = briefing.snapshot(conn)
    assert snap["market"]["sectors"]["rows"] == [
        {"name": "반도체", "side": "over", "reason": "1주 +11%로 강해요.", "rates": {"1w": 0.11}},
        {"name": "게임", "side": "under", "reason": "약해요", "rates": {"1mo": -0.12}},
        {"name": "표에 없음", "side": "under", "reason": "", "rates": {}}]
    assert "비중 확대 반도체(1주 +11%로 강해요)" in briefing.to_text(snap)           # 글 복사는 그대로


def test_assistant_makes_report(client, broker, at_open, monkeypatch):
    fake = Fake([("make_report", {"comment": "현금 비중이 높아요. 다음 회의에서 반도체를 보죠."})], reply="보고서를 만들었어요")
    monkeypatch.setattr(assistant, "LLM", fake)
    say(client, "지금까지 상황 보고해 줘")
    msgs = thread(client)
    assert msgs[-1]["meta"].get("error") is None, msgs[-1]
    report = next(m for m in msgs if m["role"] == "event")
    assert report["meta"]["kind"] == "report" and report["meta"]["report"]["comment"].startswith("현금 비중이 높아요")
    assert report["meta"]["body"].startswith("■ 투자 현황 보고") and "[보유 종목·현황]" in report["meta"]["body"]
    assert fake.results[0]["ok"] and "market" in fake.results[0] and msgs[-1]["text"] == "보고서를 만들었어요"


def test_morning_briefing_daily(client, broker, monkeypatch):
    from datetime import datetime
    from app.core.db import connect
    from app.invest.ops import morning
    from app.invest.marketdata.market import KST, SampleMarket

    class Holiday(SampleMarket):
        def session(self):
            return {"date": "2026-10-17", "start": None, "end": None, "is_open": False}

    conn = connect()
    seen = []

    async def writer(feature, system, prompt, schema, model):
        seen.append(prompt)
        return {"comment": "좋은 아침이에요! 오늘은 09:10 회의부터 반도체를 봐요."}

    monkeypatch.setattr(morning, "LLM", writer)
    autopilot.add_intent(conn, "반도체 주목")
    at = lambda d, hm: datetime(2026, 10, d, *map(int, hm.split(":")), tzinfo=KST)  # noqa: E731
    p = SampleMarket()
    assert morning.tick(conn, at(13, "08:10"), p) is None                       # 08:30 전
    msg = morning.tick(conn, at(13, "08:31"), p)
    assert msg["meta"]["kind"] == "report" and msg["meta"]["morning"] and msg["meta"]["by"] == "assistant"
    rep = msg["meta"]["report"]
    assert rep["title"] == "아침 브리핑" and rep["comment"].startswith("좋은 아침이에요") and "intents" not in rep
    assert "[보유 종목·현황]" in seen[0] and "반도체 주목" not in seen[0] and msg["meta"]["body"].startswith("■ 아침 브리핑")
    assert morning.tick(conn, at(13, "09:00"), p) is None                       # 하루 한 번
    assert morning.tick(conn, at(14, "12:00"), p) is None                       # 다음 날이라도 3시간 넘게 늦으면 건너뜀
    assert morning.tick(conn, at(17, "08:40"), Holiday()) == {"skipped": "휴장일"}
    assert morning.tick(conn, at(17, "08:50"), p) is None                       # 휴장일로 남겨 다시 보내지 않음

    async def offline(*a, **k):
        raise LLMError("x")

    monkeypatch.setattr(morning, "LLM", offline)
    msg = morning.tick(conn, at(15, "08:30"), p)                                 # LLM 이 없으면 코드가 짧게
    assert msg["meta"]["by"] == "code" and msg["meta"]["report"]["comment"].startswith("좋은 아침이에요. 총 자산 ")
    assert [m["meta"].get("morning") for m in thread(client) if m["role"] == "event"] == [True, True]

    assert client.get("/api/invest/morning-briefing").json()["last"] == "2026-10-15"
    assert client.put("/api/invest/morning-briefing", json={"time": "13:00"}).status_code == 400
    cfg = client.put("/api/invest/morning-briefing", json={"time": "07:50", "enabled": False}).json()
    assert cfg["time"] == "07:50" and cfg["enabled"] is False and morning.tick(conn, at(16, "08:00"), p) is None
    sent = client.post("/api/invest/morning-briefing/send").json()                # 지금 받아 보기 (꺼져 있어도)
    assert sent["meta"]["report"]["title"] == "아침 브리핑"


def test_assistant_changes_settings_by_chat(client, broker, at_open, monkeypatch):
    from app.invest.ops import morning
    from app.invest.broker import config
    fake = Fake([
        ("get_settings", {}),
        ("update_schedule", {"slots": ["09:10", "11:00", "13:30", "15:00"], "max_extra": 1}),
        ("update_debate", {"rounds": 3, "max_candidates": 8}),
        ("update_briefing", {"time": "08:00", "weekdays": [0, 4]}),
        ("set_agent", {"agent": "제이크", "enabled": False}),
        ("set_agent", {"agent": "트레이더", "enabled": False}),
        ("update_debate", {"rounds": 9}),
    ], reply="바꿨어요")
    monkeypatch.setattr(assistant, "LLM", fake)
    say(client, "회의 하루 4번으로, AI 추가는 1번만, 토론 3라운드·후보 8개, 보고는 월·금 8시, 제이크는 빼 줘")
    got = fake.results[0]
    assert got["autopilot"]["slots"] == ["09:10", "12:30", "15:00"] and got["briefing"]["summary"] == "매일 08:30"
    assert autopilot.get_config(_conn(client))["slots"] == ["09:10", "11:00", "13:30", "15:00"]
    assert config.agent_options(_conn(client))["rounds"] == 3 and morning.get_config(_conn(client))["summary"] == "매주 월·금 08:00"
    assert not next(a for a in agents.get_roster(_conn(client)) if a["key"] == "growth")["enabled"]
    assert fake.results[5]["ok"] is False and "실행 담당" in fake.results[5]["error"]           # 트레이더는 못 뺌
    assert fake.results[6]["ok"] is False and "라운드" in fake.results[6]["error"]
    events = [m["text"] for m in thread(client) if m["role"] == "event"]
    assert events == [
        "설정 변경 · 정기 회의(하루 4번): 09:10 · 12:30 · 15:00 → 09:10 · 11:00 · 13:30 · 15:00",
        "설정 변경 · AI 가 더할 회의(하루): 3번 → 1번",
        "설정 변경 · 토론 라운드: 2 → 3",
        "설정 변경 · 후보 종목 수: 6 → 8",
        "설정 변경 · 보고(아침 브리핑): 매일 08:30 → 매주 월·금 08:00",
        "설정 변경 · 제이크(모멘텀 투자자) 회의 참여: 참여 → 빠짐",
    ]
    monkeypatch.delenv("TOSS_CLIENT_ID")
    fake2 = Fake([("update_schedule", {"enabled": True})])
    monkeypatch.setattr(assistant, "LLM", fake2)
    say(client, "자동 운용 켜 줘")
    assert fake2.results[0]["ok"] is False and "토스" in fake2.results[0]["error"]


def test_briefing_schedule_days():
    from datetime import date
    from app.invest.ops import morning
    cfg = {**morning.DEFAULT_CONFIG}
    assert morning.describe(cfg) == "매일 08:30" and morning.due_on(cfg, date(2026, 10, 31))
    weekly = {**cfg, "weekdays": [0]}
    assert morning.describe(weekly) == "매주 월 08:30"
    assert morning.due_on(weekly, date(2026, 11, 2)) and not morning.due_on(weekly, date(2026, 11, 3))
    monthly = {**cfg, "month_days": [1, 15], "skip_holidays": False}
    assert morning.describe(monthly) == "매월 1·15일 08:30 · 휴장일에도"
    assert morning.due_on(monthly, date(2026, 11, 1)) and not morning.due_on(monthly, date(2026, 11, 2))
    assert morning.describe({**cfg, "weekdays": [0, 1, 2, 3, 4]}) == "평일 08:30"


def test_briefing_on_holiday_when_not_skipping(client, monkeypatch):
    from datetime import datetime
    from app.core.db import connect
    from app.invest.ops import morning
    from app.invest.marketdata.market import KST, SampleMarket

    class Holiday(SampleMarket):
        def session(self):
            return {"start": None, "end": None}

    conn = connect()
    morning.save_config(conn, month_days=[1], skip_holidays=False)
    msg = morning.tick(conn, datetime(2026, 11, 1, 8, 40, tzinfo=KST), Holiday())   # 일요일·휴장이어도 매월 1일엔 보냄
    assert msg["meta"]["kind"] == "report"
    morning.save_config(conn, skip_holidays=True)
    assert morning.tick(conn, datetime(2026, 12, 1, 8, 40, tzinfo=KST), Holiday()) == {"skipped": "휴장일"}
    assert client.put("/api/invest/morning-briefing", json={"weekdays": []}).status_code == 400
    assert client.put("/api/invest/morning-briefing", json={"month_days": [32]}).status_code == 400
    assert client.put("/api/invest/morning-briefing", json={"weekdays": [5, 6], "month_days": []}).json()["summary"] == "매주 토·일 08:30"


def test_baseline_return(client, broker, at_open, monkeypatch):
    from app.core.db import connect
    from app.invest.ops import briefing
    from app.invest.broker import trading
    conn = connect()
    assert client.get("/api/invest/performance").json()["baseline"] is None             # 기준을 정하기 전
    trading.place_order(conn, symbol="005930", side="buy", order_type="market", quantity=2, source="manual", confirm_live=True)
    b = client.put("/api/invest/baseline", json={"contributions": [{"date": "2026-10-13", "amount": 10_000_000, "note": "일시금"}]}).json()
    assert b == {"contributions": [{"date": "2026-10-13", "amount": 10_000_000, "note": "일시금"}], "amount": 10_000_000, "since": "2026-10-13"}
    perf = client.get("/api/invest/performance").json()
    bl = perf["baseline"]
    stock = 2 * broker.price("005930")
    assert bl["value_krw"] == round(broker.cash + stock) and bl["pnl_krw"] == bl["value_krw"] - 10_000_000
    assert bl["return_rate"] == round(bl["pnl_krw"] / 10_000_000, 4) and bl["days"] == 0
    added = client.post("/api/invest/baseline/contributions", json={"date": "2026-10-13", "amount": 500_000, "note": "추가 입금"}).json()
    assert added["amount"] == 10_500_000 and len(added["contributions"]) == 2
    assert client.put("/api/invest/baseline", json={"contributions": [{"date": "2026-10-13", "amount": -5}]}).status_code == 400
    assert client.put("/api/invest/baseline", json={"contributions": [{"date": "13-10-2026", "amount": 5}]}).status_code == 422
    text = client.get("/api/invest/briefing").json()["text"]
    assert "- 운용 수익률" in text and "기준 원금 10,500,000원(2026-10-13부터, 0일)" in text

    fake = Fake([("set_baseline", {"amount": 1_000_000, "mode": "reset", "date": "2026-10-13"}),
                 ("set_baseline", {"amount": 500_000, "mode": "add"}), ("set_baseline", {"amount": -300_000, "mode": "add"})])
    monkeypatch.setattr(assistant, "LLM", fake)
    say(client, "기준 원금은 100만원으로 다시, 50만원 더 넣었고 30만원 뺐어")
    assert briefing.get_baseline(conn)["amount"] == 1_200_000
    events = [m["text"] for m in thread(client) if m["role"] == "event"]
    assert events == ["설정 변경 · 기준 원금: 10,500,000원(10-13부터) → 1,000,000원(10-13부터)",
                      "설정 변경 · 기준 원금: 1,000,000원(10-13부터) → 1,500,000원(10-13부터)",
                      "설정 변경 · 기준 원금: 1,500,000원(10-13부터) → 1,200,000원(10-13부터)"]
    assert [c["note"] for c in briefing.get_baseline(conn)["contributions"]][-2:] == ["추가 입금", "출금"]
