import json
import re

import pytest

from app.core.db import connect
from app.invest.debate import agents
from app.invest.marketdata import news
from app.invest.ops import report
from app.llm.client import LLMError
from tests.test_invest import PIN, at_open  # noqa: F401 — 장중 시각 고정 fixture

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>삼성전자, 신제품 공개 - 한국경제</title><link>https://news.google.com/rss/articles/a1</link>
<pubDate>Tue, 13 Oct 2026 01:00:00 GMT</pubDate><description>&lt;a href="x"&gt;삼성전자, 신제품 공개&lt;/a&gt; 반도체 업황 회복 기대</description>
<source url="https://www.hankyung.com">한국경제</source></item>
<item><title>코스피 상승 마감 - 연합뉴스</title><link>https://news.google.com/rss/articles/a2</link>
<pubDate>Tue, 13 Oct 2026 02:00:00 GMT</pubDate><source url="https://www.yna.co.kr">연합뉴스</source></item>
<item><title>내부 링크</title><link>http://10.0.0.1/x</link></item>
</channel></rss>"""


@pytest.fixture
def fake_rss(monkeypatch):
    calls = []

    def http(method, url, headers=None, data=None, timeout=15):
        calls.append(url)
        return 200, RSS.encode()

    monkeypatch.setattr(news, "HTTP", http)
    return calls


@pytest.fixture
def conn(client):
    c = connect()
    yield c
    c.close()


# ---- 뉴스 ----
def test_parse_rss_strips_source_and_skips_non_https():
    items = news.parse_rss(RSS.encode())
    assert [i["title"] for i in items] == ["삼성전자, 신제품 공개", "코스피 상승 마감"]
    assert items[0]["source"] == "한국경제" and items[0]["snippet"] == "반도체 업황 회복 기대"
    dup = news.parse_rss(RSS.replace("반도체 업황 회복 기대", "한국경제").encode())
    assert dup[0]["snippet"] == ""  # 요약이 언론사 이름뿐이면 비운다
    assert items[0]["published_at"]


def test_news_fetch_is_cached(conn, fake_rss):
    first = news.fetch(conn, "삼성전자 주가", "005930")
    again = news.fetch(conn, "삼성전자 주가", "005930")
    assert len(first) == 2 and [n["id"] for n in again] == [n["id"] for n in first]
    assert len(fake_rss) == 1  # 15분 안에는 다시 받지 않는다
    news.fetch(conn, "삼성전자 주가", "005930", force=True)
    assert len(fake_rss) == 2


def test_news_api_by_symbol_and_watchlist(client, fake_rss, at_open):
    res = client.get("/api/invest/news", params={"scope": "symbol", "symbol": "005930", "name": "삼성전자"}).json()
    assert len(res["items"]) == 2 and "%EC%82%BC%EC%84%B1" in fake_rss[0]  # "삼성..." 질의
    assert client.get("/api/invest/news", params={"scope": "watchlist"}).json()["items"] == []
    client.post("/api/invest/watchlist", json={"symbol": "000660"})
    wl = client.get("/api/invest/news", params={"scope": "watchlist", "refresh": True}).json()
    assert wl["targets"][0]["name"] == "SK하이닉스" and wl["items"]


# ---- LLM 리포트 ----
def test_report_job_drops_unknown_citations(client, fake_rss, monkeypatch, at_open):
    async def fake_llm(feature, system, prompt, schema, model):
        if feature == "news_analysis":
            ids = [int(x) for x in re.findall(r"^\[(\d+)\]", prompt, re.M)]
            return {"sentiment": "positive", "sentiment_score": 0.4, "summary": "신제품 기대",
                    "key_points": [{"point": "신제품 공개", "news_ids": ids[:1] + [9999]}],
                    "risks": ["업황 불확실"], "catalysts": ["신제품"]}
        assert "신제품 공개" in prompt  # 종합 보고서는 종목별 분석만 근거로
        return {"title": "오늘의 리포트", "headline": "반도체 기대감", "market_summary": "상승",
                "highlights": [{"symbol": "005930", "verdict": "긍정"}], "action_items": ["실적 발표 확인"], "risks": []}

    monkeypatch.setattr(report, "LLM", fake_llm)
    run = client.post("/api/invest/reports", json={"scope": "symbols", "symbols": ["005930"]}).json()
    assert run["status"] == "succeeded", run.get("error")
    assert run["summary"]["citations_dropped"] == 1 and run["summary"]["sentiments"] == {"005930": 0.4}
    detail = client.get(f"/api/invest/reports/{run['id']}").json()
    rep = detail["report"]
    assert rep["title"] == "오늘의 리포트" and rep["sections"][0]["analysis"]["key_points"][0]["news_ids"] != [9999]
    assert all(9999 not in p["news_ids"] for p in rep["sections"][0]["analysis"]["key_points"])
    assert rep["news"] and rep["provider"] == "sample"
    assert client.get("/api/invest/reports").json()[0]["id"] == run["id"]


def test_report_needs_targets(client, fake_rss):
    run = client.post("/api/invest/reports", json={"scope": "watchlist"}).json()
    assert run["status"] == "failed" and "대상" in run["error"]


# ---- 에이전트 ----
def _title(system: str) -> str:
    return re.search(r"당신은 '([^']+)' 에이전트", system).group(1)


def _candidates(system: str) -> list[str]:
    return re.findall(r"^- .+ \((\w+)\) · (?:주식|ETF)", system, re.M)


class Script:
    """에이전트별 입장 각본 + 실제 도구 호출로 핸들러를 거치는 가짜 LLM.

    stances: 에이전트 이름 → 라운드별 입장. 문자열이면 모든 후보에 같은 입장, dict 면 {종목: 입장} (없는 종목은 의견 없음).
    trade: 트레이더가 낼 주문(들) {symbol?, side, quantity, order_type, reason, retry?}.
    """

    def __init__(self, stances: dict[str, list], trade: dict | list | None = None, fail: set[str] | None = None,
                 hook=None):
        self.stances = stances
        self.trades = [trade] if isinstance(trade, dict) else list(trade or [])
        self.fail = fail or set()
        self.hook = hook
        self.calls: list[str] = []
        self.prompts: list[tuple[str, str]] = []

    async def __call__(self, feature, system, prompt, schema, tools, model, max_turns):
        title = _title(system)
        symbols = _candidates(system)
        self.calls.append(title)
        self.prompts.append((title, prompt))
        if self.hook:
            self.hook(title, prompt)
        by_name = {t.name: t for t in tools}
        if schema is agents.TRADER_SCHEMA:
            await by_name["get_trading_limits"].handler({})
            decisions = []
            for t in self.trades:
                result = await by_name["place_order"].handler(t)
                if t.get("retry") and not result["ok"]:
                    await by_name["place_order"].handler({**t, "quantity": 1})
                decisions.append({"symbol": t.get("symbol", symbols[0]), "decision": t["side"], "quantity": t["quantity"]})
            return {"decisions": decisions or [{"symbol": symbols[0], "decision": "hold", "quantity": 0}],
                    "rationale": "모두 같은 방향" if decisions else "호가 불리"}
        if title in self.fail:
            raise LLMError("timeout")
        first_tool = next(iter(by_name.values()))
        await first_tool.handler({"symbol": symbols[0]})
        rnd = 1 if schema is agents.OPINION_SCHEMA else int(re.search(r"라운드 (\d+)", prompt).group(1))
        script = self.stances.get(title, ["none"])  # 각본에 없는 에이전트(명단에 새로 든)는 의견 없음
        spec = script[min(rnd, len(script)) - 1]
        views = [{"symbol": sym, "stance": spec if isinstance(spec, str) else spec.get(sym, "none"), "confidence": 0.7,
                  "reason": "근거"} for sym in symbols]
        return {"views": views, "thesis": f"{title} 판단", "evidence": [{"fact": "f", "tool": first_tool.name}],
                "risks": [], "reason_for_change": "새 근거", "responses": []}


N_ANALYSTS = sum(not a["executor"] for a in agents.DEFAULT_ROSTER)  # 기본 명단의 분석 에이전트 수

ALIGNED = {"차티스트": ["buy", "buy"], "가치 투자자": ["none", "buy"], "뉴스 애널리스트": ["buy", "buy"],
           "포트폴리오 매니저": ["none", "none"], "리스크 매니저": ["sell", "none"]}


def _start(client, **body):
    return client.post("/api/invest/agent-sessions", json={"symbol": "005930", **body}, headers=PIN)


def test_aligned_session_trader_proposes_order_for_approval(client, monkeypatch, broker, at_open):
    script = Script(ALIGNED, trade={"side": "buy", "quantity": 2, "order_type": "market", "reason": "합의"})
    monkeypatch.setattr(agents, "LLM", script)
    run = _start(client, rounds=2).json()
    assert run["status"] == "succeeded", run.get("error")
    s = run["summary"]
    # 기본은 사용자 승인 대기 — 승인해야 체결
    assert s["gate"] == "aligned" and s["direction"] == "buy" and s["decision"] == "buy" and s["order_status"] == "pending_approval"
    assert script.calls.count("트레이더") == 1 and len(script.calls) == N_ANALYSTS * 2 + 1  # 분석 × 2라운드 + 트레이더
    detail = client.get(f"/api/invest/agent-sessions/{run['id']}").json()
    sess = detail["session"]
    assert len(sess["transcript"]) == N_ANALYSTS * 2 and sess["gate"]["counts"] == {"buy": 3, "sell": 0, "none": N_ANALYSTS - 3}
    assert sum(1 for t in sess["transcript"] if t["changed"]) == 2  # 가치 투자자 none→buy, 리스크 sell→none
    assert {c["tool"] for c in sess["tool_calls"]} >= {"compare_candidates", "get_portfolio", "place_order", "get_trading_limits"}
    assert detail["orders"][0]["source"] == "agent" and detail["orders"][0]["run_id"] == run["id"]
    assert client.get("/api/invest/account").json()["holdings"] == [] and broker.posts() == []
    assert client.get("/api/invest/agent-sessions").json()[0]["orders_now"][0]["status"] == "pending_approval"
    approved = client.post(f"/api/invest/orders/{s['order_id']}/approve", json={"confirm_live": True}, headers=PIN).json()
    assert approved["status"] == "filled" and broker.posts()[0]["quantity"] == "2"
    assert client.get("/api/invest/agent-sessions").json()[0]["orders_now"][0]["status"] == "filled"
    acct = client.get("/api/invest/account").json()
    assert acct["holdings"][0]["quantity"] == 2


def test_conflict_blocks_without_calling_trader(client, monkeypatch, broker, at_open):
    script = Script({**ALIGNED, "리스크 매니저": ["sell", "sell"]}, trade={"side": "buy", "quantity": 1, "order_type": "market", "reason": "x"})
    monkeypatch.setattr(agents, "LLM", script)
    run = _start(client).json()
    assert run["summary"]["gate"] == "conflict" and run["summary"]["decision"] == "hold"
    assert "트레이더" not in script.calls and run["summary"]["order_id"] is None
    assert client.get("/api/invest/orders").json() == []


def test_place_order_tool_refuses_opposite_side_and_second_order(client, monkeypatch, broker, at_open):
    script = Script(ALIGNED, trade={"side": "sell", "quantity": 1, "order_type": "market", "reason": "반대로"})
    monkeypatch.setattr(agents, "LLM", script)
    run = _start(client).json()
    detail = client.get(f"/api/invest/agent-sessions/{run['id']}").json()
    refusal = next(c for c in detail["session"]["tool_calls"] if c["tool"] == "place_order")
    assert "게이트 방향" in refusal["result"] and detail["orders"] == []
    assert "만들어지지 않은 종목" in detail["session"]["decision"]["note"]


def test_guard_rejection_then_smaller_retry(client, conn, monkeypatch, broker, at_open):
    from app.invest.broker import config
    price = client.get("/api/invest/stocks/005930").json()["price"]
    config.save_trading(conn, limits_={"max_order_krw": price * 1.5})
    script = Script(ALIGNED, trade={"side": "buy", "quantity": 5, "order_type": "market", "reason": "합의", "retry": True})
    monkeypatch.setattr(agents, "LLM", script)
    run = _start(client).json()
    orders = client.get(f"/api/invest/agent-sessions/{run['id']}").json()["orders"]
    assert [(o["status"], o["quantity"]) for o in orders] == [("rejected", 5), ("pending_approval", 1)]  # 거절도 기록


def test_failed_analyst_makes_gate_incomplete(client, monkeypatch, broker, at_open):
    script = Script(ALIGNED, trade={"side": "buy", "quantity": 1, "order_type": "market", "reason": "x"}, fail={"리스크 매니저"})
    monkeypatch.setattr(agents, "LLM", script)
    run = _start(client, rounds=1).json()
    assert run["summary"]["gate"] == "incomplete" and "트레이더" not in script.calls


def test_unanimity_gate_rules():
    assert agents.unanimity_gate(["buy", "none", "buy"])["direction"] == "buy"
    assert agents.unanimity_gate(["sell", "none"])["direction"] == "sell"
    assert agents.unanimity_gate(["buy", "sell"])["status"] == "conflict"
    assert agents.unanimity_gate(["none", "none"])["status"] == "no_view"
    assert agents.unanimity_gate(["buy"], failed=1)["status"] == "incomplete"
    # 종목별 게이트: 한 종목이라도 열리면 aligned, 열린 종목만 aligned 목록에
    st = lambda **v: {"error": None, "views": [{"symbol": k, "stance": x} for k, x in v.items()]}  # noqa: E731
    g = agents.session_gate([st(A="buy", B="buy"), st(A="none", B="sell")], ["A", "B"])
    assert g["status"] == "aligned" and g["aligned"] == [{"symbol": "A", "direction": "buy"}]
    assert g["symbols"]["B"]["status"] == "conflict"
    assert agents.session_gate([st(A="none"), st(A="none")], ["A"])["status"] == "no_view"


def test_roster_validation_and_api(client):
    data = client.get("/api/invest/agents").json()
    assert len(data["roster"]) == N_ANALYSTS + 1 and any(t["name"] == "place_order" and t["executor_only"] for t in data["tools"])
    assert any(a["key"] == "portfolio" and "get_portfolio" in a["tools"] for a in data["roster"])  # 투자현황 에이전트
    assert data["options"] == {"rounds": 2, "model": "", "max_candidates": 6}
    roster = data["roster"]
    two_exec = [dict(a, executor=True, tools=a["tools"] + ["place_order"]) if a["key"] == "risk" else a for a in roster]
    assert client.put("/api/invest/agents", json={"roster": two_exec}).status_code == 400
    analyst_with_order = [dict(a, tools=a["tools"] + ["place_order"]) if a["key"] == "news" else a for a in roster]
    assert client.put("/api/invest/agents", json={"roster": analyst_with_order}).status_code == 400
    custom = roster + [{"key": "macro", "title": "매크로 전략가", "persona": "금리·환율 중심", "tools": ["get_market_overview"],
                        "executor": False, "enabled": True}]
    saved = client.put("/api/invest/agents", json={"roster": custom}).json()["roster"]
    assert saved[-1]["key"] == "macro"
    only_one = [dict(a, enabled=a["key"] in ("technical", "trader")) for a in saved]
    assert client.put("/api/invest/agents", json={"roster": only_one}).status_code == 400  # 분석 2명 이상
    admin_named = [dict(a, title="관리자") if a["key"] == "news" else a for a in saved]
    assert client.put("/api/invest/agents", json={"roster": admin_named}).status_code == 400  # 사용자 이름과 겹침
    assert len(client.post("/api/invest/agents/reset").json()["roster"]) == N_ANALYSTS + 1


def test_options_from_lab_are_used(client, monkeypatch, broker, at_open):
    assert client.put("/api/invest/agents/options", json={"rounds": 1}).status_code == 403  # PIN 이 있으면 PIN 필요
    res = client.put("/api/invest/agents/options", json={"rounds": 1, "model": "claude-haiku-4-5-20251001"}, headers=PIN)
    assert res.json()["options"]["rounds"] == 1
    assert client.put("/api/invest/agents/options", json={"model": "gpt-4o"}, headers=PIN).status_code == 400
    assert client.put("/api/invest/agents/options", json={"max_candidates": 30}, headers=PIN).status_code == 422
    seen = []
    script = Script(ALIGNED)

    async def spy(feature, system, prompt, schema, tools, model, max_turns):
        seen.append(model)
        return await script(feature, system, prompt, schema, tools, model, max_turns)

    monkeypatch.setattr(agents, "LLM", spy)
    run = client.post("/api/invest/agent-sessions", json={"symbol": "005930"}, headers=PIN).json()  # 라운드·모델 없이
    assert len(script.calls) == N_ANALYSTS and set(seen) == {"claude-haiku-4-5-20251001"}  # 1라운드만 (리스크 매도 → 충돌)
    assert client.get(f"/api/invest/agent-sessions/{run['id']}/messages").json()["messages"][0]["rounds"] == 1


def test_sector_scope_debates_each_candidate_and_gates_per_symbol(client, monkeypatch, broker, at_open):
    assert {"S11", "S20"} <= {x["id"] for x in client.get("/api/invest/sectors").json()}
    preview = client.get("/api/invest/agent-scope", params={"scope": "sector", "sector_id": "S11"}).json()
    assert [c["symbol"] for c in preview["candidates"]] == ["005930", "000660", "091160"]  # 구성 주식 + 관련 ETF
    assert preview["candidates"][2]["security_type"] == "ETF" and preview["label"] == "섹터 반도체"
    split = {"차티스트": [{"005930": "buy", "000660": "buy"}], "가치 투자자": [{"005930": "buy", "000660": "sell"}],
             "뉴스 애널리스트": [{"005930": "buy"}], "포트폴리오 매니저": [{"091160": "none"}], "리스크 매니저": [{"005930": "none"}]}
    script = Script(split, trade=[{"symbol": "005930", "side": "buy", "quantity": 1, "order_type": "market", "reason": "합의"},
                                  {"symbol": "000660", "side": "buy", "quantity": 1, "order_type": "market", "reason": "덤"}])
    monkeypatch.setattr(agents, "LLM", script)
    run = client.post("/api/invest/agent-sessions", json={"scope": "sector", "sector_id": "S11", "rounds": 1}, headers=PIN).json()
    assert run["status"] == "succeeded", run.get("error")
    s = run["summary"]
    assert s["label"] == "섹터 반도체" and s["symbols"] == ["005930", "000660", "091160"]
    assert s["aligned"] == [{"symbol": "005930", "direction": "buy"}]
    assert [(o["symbol"], o["status"]) for o in s["orders"]] == [("005930", "pending_approval")]
    sess = client.get(f"/api/invest/agent-sessions/{run['id']}").json()["session"]
    assert sess["gate"]["symbols"]["000660"]["status"] == "conflict" and sess["gate"]["symbols"]["091160"]["status"] == "no_view"
    refusal = [c for c in sess["tool_calls"] if c["tool"] == "place_order"][1]
    assert "게이트가 열린 종목만" in refusal["result"]  # 닫힌 종목 주문은 도구가 거절
    trader_prompt = next(p for t, p in script.prompts if t == "트레이더")
    assert "닫힌 종목" in trader_prompt and "SK하이닉스" in trader_prompt


def test_portfolio_scope_uses_holdings_then_watchlist(client, monkeypatch, broker, at_open):
    client.post("/api/invest/orders", json={"symbol": "035720", "side": "buy", "order_type": "market", "quantity": 1,
                                            "confirm_live": True}, headers=PIN)
    client.post("/api/invest/watchlist", json={"symbol": "000660"})
    client.post("/api/invest/watchlist", json={"symbol": "035720"})  # 보유와 겹치면 한 번만
    client.post("/api/invest/watchlist", json={"symbol": "AAPL"})    # 해외는 아직 주문 불가 → 빠짐
    preview = client.get("/api/invest/agent-scope", params={"scope": "portfolio"}).json()
    assert [(c["symbol"], c["held"]) for c in preview["candidates"]] == [("035720", 1), ("000660", 0)]
    client.put("/api/invest/agents/options", json={"max_candidates": 2}, headers=PIN)
    client.post("/api/invest/watchlist", json={"symbol": "005930"})
    preview = client.get("/api/invest/agent-scope", params={"scope": "portfolio"}).json()
    assert len(preview["candidates"]) == 2 and "앞의 2개만" in preview["notes"][0]


def test_admin_chat_reaches_agents_mid_debate(client, monkeypatch, broker, at_open):
    from app.invest.jobs import agent_action
    posted = []

    def hook(title, prompt):
        if not posted:  # 라운드 1 도중 관리자가 채팅으로 의견을 남긴다
            run_id = connect().execute("SELECT id FROM runs WHERE kind = 'agent_action' AND status = 'running'").fetchone()[0]
            posted.append(agent_action.post_user_message(run_id, "반도체 비중이 이미 커요, 신중하게"))

    script = Script(ALIGNED, hook=hook)
    monkeypatch.setattr(agents, "LLM", script)
    run = _start(client, rounds=2, note="단기 반등만 노려요").json()
    assert posted[0]["type"] == "user"
    r1 = [p for t, p in script.prompts[:5]]
    r2 = [p for t, p in script.prompts[5:10]]
    assert all("단기 반등만 노려요" in p for p in r1)  # 첫 메시지는 라운드 1부터
    assert all("반도체 비중이 이미 커요" in p and "관리자" in p for p in r2)  # 토론 중 의견은 다음 라운드부터
    msgs = client.get(f"/api/invest/agent-sessions/{run['id']}/messages").json()["messages"]
    users = [m["text"] for m in msgs if m["type"] == "user"]
    assert users == ["단기 반등만 노려요", "반도체 비중이 이미 커요, 신중하게"]
    res = client.post(f"/api/invest/agent-sessions/{run['id']}/chat", json={"text": "늦은 의견"}, headers=PIN)
    assert res.status_code == 409  # 끝난 토론에는 남길 수 없다
    assert client.post(f"/api/invest/agent-sessions/{run['id']}/chat", json={"text": "x"}).status_code == 403  # PIN


def test_portfolio_overview_includes_other_accounts(client, monkeypatch, broker, at_open):
    client.post("/api/invest/orders", json={"symbol": "005930", "side": "buy", "order_type": "market", "quantity": 2,
                                            "confirm_live": True}, headers=PIN)
    broker.usd_cash = 100.0
    rows = client.post("/api/invest/external-holdings", json={"symbol": "000660", "quantity": 3, "account_name": "키움 ISA",
                                                             "avg_price": 150000}, headers=PIN).json()
    assert rows[0]["name"] == "SK하이닉스" and rows[0]["priced"] and rows[0]["value_krw"] > 0
    assert client.post("/api/invest/external-holdings", json={"symbol": "999999", "quantity": 1}, headers=PIN).status_code == 404
    o = client.get("/api/invest/portfolio").json()
    assert o["other_accounts"] == ["키움 ISA"] and o["cash"]["usd"] == 100 and o["cash"]["usd_in_krw"] == 138000
    toss_value = next(x for x in o["positions"] if x["where"] == "토스")["value_krw"]
    assert o["total_assets_krw"] == round(o["cash"]["krw"] + 138000 + toss_value + rows[0]["value_krw"])
    assert o["concentration"]["count"] == 2 and 0 < o["cash_ratio"] < 1
    other = next(x for x in o["positions"] if x["where"] == "키움 ISA")  # 포트폴리오 화면용 원금·손익
    assert other["cost_krw"] == 450000 and other["pnl_krw"] == other["value_krw"] - 450000
    assert all(x["security_type"] == "STOCK" for x in o["positions"])
    rows = client.patch(f"/api/invest/external-holdings/{rows[0]['id']}", json={"quantity": 5}, headers=PIN).json()
    assert rows[0]["quantity"] == 5
    assert client.delete(f"/api/invest/external-holdings/{rows[0]['id']}", headers=PIN).json() == []


def test_session_json_is_valid_and_logs(client, monkeypatch, broker, at_open):
    monkeypatch.setattr(agents, "LLM", Script(ALIGNED))
    run = _start(client, rounds=1).json()
    logs = client.get(f"/api/runs/{run['id']}/logs").json()
    assert any("게이트" in log["message"] for log in logs)
    metrics = client.get(f"/api/runs/{run['id']}/metrics").json()
    assert metrics["buy"] == [[1, 2.0]] and metrics["sell"] == [[1, 1.0]]
    assert json.dumps(client.get(f"/api/invest/agent-sessions/{run['id']}").json()["session"])


def test_auto_approve_bypass_fills_immediately(client, conn, monkeypatch, broker, at_open):
    from app.invest.broker import config
    config.save_trading(conn, agent_auto=True)
    monkeypatch.setattr(agents, "LLM", Script(ALIGNED, trade={"side": "buy", "quantity": 1, "order_type": "market", "reason": "합의"}))
    run = _start(client, rounds=2).json()
    assert run["summary"]["gate"] == "aligned" and run["summary"]["order_status"] == "filled"
    assert len(broker.posts()) == 1


def test_chat_messages_stream_in_order(client, monkeypatch, broker, at_open):
    monkeypatch.setattr(agents, "LLM", Script(ALIGNED, trade={"side": "buy", "quantity": 1, "order_type": "market", "reason": "합의"}))
    run = _start(client, rounds=2).json()
    data = client.get(f"/api/invest/agent-sessions/{run['id']}/messages").json()
    types = [m["type"] for m in data["messages"]]
    assert types[0] == "start" and types[-1] == "end" and types[1] == "round"
    assert types.count("round") == 2 and types.count("statement") == N_ANALYSTS * 2 and "tool" in types
    assert types.index("gate") < types.index("trader_start") < types.index("order") < types.index("decision")
    assert [m["seq"] for m in data["messages"]] == list(range(1, len(types) + 1))
    start = data["messages"][0]
    assert start["auto_approve"] is False and len(start["agents"]) == N_ANALYSTS + 1 and start["candidates"][0]["symbol"] == "005930"
    later = client.get(f"/api/invest/agent-sessions/{run['id']}/messages", params={"after": 5}).json()["messages"]
    assert later[0]["seq"] == 6
    assert data["orders"][0]["status"] == "pending_approval" and data["messages"][-1]["message"] == "승인을 기다리는 주문이 1건 있어요"
    assert data["orders"][0]["amount"] > 0 and isinstance(data["orders"][0]["checks"], list)  # 승인 카드의 예상 금액
    stmt = next(m for m in data["messages"] if m["type"] == "statement")
    assert stmt["views"][0]["symbol"] == "005930" and stmt["views"][0]["reason"] == "근거"


def test_only_one_session_at_a_time_and_stop(client, conn, monkeypatch, broker, at_open):
    cur = conn.execute("INSERT INTO runs (kind, name, params_json, status) VALUES ('agent_action', '진행 중', ?, 'running')",
                       (json.dumps({"symbol": "035720"}),))
    conn.commit()
    running_id = cur.lastrowid
    assert client.get("/api/invest/agent-sessions/active").json()["id"] == running_id
    res = _start(client)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "session_running" and res.json()["detail"]["run_id"] == running_id
    assert client.post(f"/api/invest/agent-sessions/{running_id}/stop").json()["cancel_requested"] is True
    conn.execute("UPDATE runs SET status = 'cancelled' WHERE id = ?", (running_id,))
    conn.commit()
    assert client.get("/api/invest/agent-sessions/active").json() is None
    monkeypatch.setattr(agents, "LLM", Script(ALIGNED))
    assert _start(client, rounds=1).status_code == 201


def test_stop_cuts_a_running_session(client, monkeypatch, broker, at_open):
    import asyncio
    import time

    async def slow(feature, system, prompt, schema, tools, model, max_turns):
        c = connect()  # 사용자가 중지를 누른 것처럼
        c.execute("UPDATE runs SET cancel_requested = 1 WHERE kind = 'agent_action' AND status = 'running'")
        c.commit()
        c.close()
        await asyncio.sleep(30)
        return {}

    monkeypatch.setattr(agents, "LLM", slow)
    started = time.perf_counter()
    run = _start(client, rounds=1).json()
    assert run["status"] == "cancelled" and time.perf_counter() - started < 5
    msgs = client.get(f"/api/invest/agent-sessions/{run['id']}/messages").json()["messages"]
    assert msgs[-1]["type"] == "end" and msgs[-1]["status"] == "cancelled"


def test_holding_indicators_table(client, broker, at_open):
    broker.positions["005930"] = {"quantity": 10.0, "cost": 700_000.0}
    broker.positions["000660"] = {"quantity": 1.0, "cost": 150_000.0}
    rows = client.get("/api/invest/holdings/indicators").json()["rows"]
    assert [r["symbol"] for r in rows] == sorted([r["symbol"] for r in rows], key=lambda s: -next(x["value_krw"] for x in rows
                                                                                                   if x["symbol"] == s))
    r = next(x for x in rows if x["symbol"] == "005930")
    assert r["quantity"] == 10 and r["weight"] > 0 and r["rsi"] is not None and r["trend"] in (-1, 0, 1)
    assert r["change_20d"] is not None and r["volume_ratio"] is not None and r["pnl_rate"] is not None


def test_performance_items_by_stock_order_and_meeting(client, broker, at_open):
    from app.core.db import connect
    from app.invest.ops import briefing
    from app.invest.marketdata.market import SampleMarket
    broker.positions["005930"] = {"quantity": 10.0, "cost": 700_000.0}
    broker.positions["000660"] = {"quantity": 1.0, "cost": 400_000.0}
    c = connect()
    briefing.set_baseline(c, [{"date": "2026-10-01", "amount": 2_000_000, "note": "처음"}])
    run_id = c.execute("INSERT INTO runs (kind, name, status, params_json) VALUES ('agent_action', '자동 운용 · 09:10', 'succeeded', '{}')"
                       ).lastrowid
    for sym, name, side, qty, fill in (("005930", "삼성전자", "buy", 2, 60_000), ("000660", "SK하이닉스", "sell", 1, 999_999)):
        c.execute("INSERT INTO invest_orders (symbol, name, side, order_type, quantity, status, filled_quantity, avg_fill_price, filled_at, "
                  "source, run_id) VALUES (?, ?, ?, 'market', ?, 'filled', ?, ?, '2026-10-13T10:00:00', 'agent', ?)",
                  (sym, name, side, qty, qty, fill, run_id))
    c.commit()
    perf = briefing.performance(c, SampleMarket())
    items = perf["portfolio"]["items"]
    assert [x["symbol"] for x in items] == sorted([x["symbol"] for x in items], key=lambda s: -next(x["pnl_krw"] for x in items
                                                                                                   if x["symbol"] == s))
    sam = next(x for x in items if x["symbol"] == "005930")
    assert sam["cost_krw"] == 700_000 and sam["contribution"] == round(sam["pnl_krw"] / 2_000_000, 4)
    assert perf["portfolio"]["contribution_base"] == "기준 원금"
    ai = perf["ai"]
    o = next(x for x in ai["orders"] if x["symbol"] == "005930")
    assert o["at"] == "2026-10-13 10:00" and o["amount_krw"] == 120_000 and o["pnl_krw"] == round(2 * (o["now"] - 60_000))
    assert ai["by_meeting"] == [{"run_id": run_id, "name": "자동 운용 · 09:10", "at": ai["by_meeting"][0]["at"], "orders": 2,
                                 "amount_krw": 1_119_999, "pnl_krw": ai["pnl_krw"], "change": ai["change"]}]


def test_stale_news_report_is_not_handed_to_agents(client, monkeypatch):
    """뉴스 리포트는 수동으로만 만든다 — 오래되면 판단 자료로 내주지 않고 이유를 알려 준다."""
    import asyncio
    from datetime import timedelta
    from app.core.db import connect
    from app.invest.marketdata import market

    conn = connect()
    conn.execute("INSERT INTO runs (kind, name, status, finished_at, artifact_path) VALUES ('news_report', 'r', 'succeeded', ?, 'x')",
                 ((market.now_kst() - timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S"),))
    conn.commit()
    monkeypatch.setattr(agents, "load_report", lambda _p: {"headline": "옛 리포트", "sections": []})
    s = agents.Session(conn=conn, scope={"scope": "stream", "label": "x", "candidates": [{"symbol": "005930", "name": "삼성전자"}]},
                       run_id=None, provider=None)
    got = asyncio.run(agents._handlers(s)["get_latest_report"]({}))
    assert got["available"] is False and "20일 전" in got["note"] and "search_news" in got["note"]
    conn.execute("UPDATE runs SET finished_at = ?", (market.now_kst().strftime("%Y-%m-%d %H:%M:%S"),))
    conn.commit()
    assert asyncio.run(agents._handlers(s)["get_latest_report"]({}))["headline"] == "옛 리포트"


def test_each_agent_call_carries_its_agent_key_for_routing(client, monkeypatch, broker, at_open):
    from app.llm import routes

    seen: dict[str, set] = {}
    script = Script(ALIGNED, trade={"side": "buy", "quantity": 1, "order_type": "market", "reason": "합의"},
                    hook=lambda title, _p: seen.setdefault(title, set()).add(routes.AGENT.get()))
    monkeypatch.setattr(agents, "LLM", script)
    assert _start(client, rounds=2).json()["status"] == "succeeded"
    assert seen["차티스트"] == {"technical"} and seen["리스크 매니저"] == {"risk"} and seen["트레이더"] == {"trader"}
    assert routes.AGENT.get() is None  # 호출이 끝나면 표시가 풀린다


def test_debate_session_leaves_a_trace_tree(client, monkeypatch, broker, at_open):
    from app.core.db import connect

    script = Script(ALIGNED, trade={"side": "buy", "quantity": 1, "order_type": "market", "reason": "합의"})
    monkeypatch.setattr(agents, "LLM", script)
    run = _start(client, rounds=2).json()
    with connect() as conn:
        spans = [dict(r) for r in conn.execute("SELECT * FROM trace_spans WHERE run_id = ? ORDER BY start_ms, rowid", (run["id"],))]
    by_id = {s["id"]: s for s in spans}
    root = next(s for s in spans if s["kind"] == "run")
    assert root["parent_id"] is None and {s["trace_id"] for s in spans} == {root["id"]}
    rounds = [s for s in spans if s["kind"] == "step"]
    assert [s["name"] for s in rounds] == ["라운드 1 · 독립 의견", "라운드 2 · 설득·반박"]
    speakers = [s for s in spans if s["kind"] == "agent" and by_id[s["parent_id"]]["kind"] == "step"]
    assert {s["agent"] for s in speakers} >= {"technical", "value", "risk"}
    events = [s["name"] for s in spans if s["kind"] == "event"]
    assert "gate" in events and "decision" in events and "order" in events
    trader = next(s for s in spans if s["kind"] == "agent" and s["agent"] == "trader")
    assert trader["parent_id"] == root["id"]
