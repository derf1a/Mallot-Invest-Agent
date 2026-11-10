"""투자 자동 운용: 과반 게이트, 운용 지시에서 종목·섹터 찾기, 판단 일정(tick·놓침·건너뜀), AI 가 정하는 다음 판단, API·PIN,
일정이 시작한 세션이 운용 지시를 넣고 과반 게이트로 승인 없이 주문하는지."""

from datetime import datetime

import pytest

from app import registry
from app.core import envfile
from app.core.db import connect
from app.invest import service
from app.invest.broker import config
from app.invest.debate import agents, scopes
from app.invest.marketdata.market import KST, SampleMarket
from app.invest.ops import autopilot
from app.llm.client import LLMError
from app.runs.runner import start_run
from tests.test_invest import OPEN_TIME, PIN, at_open  # noqa: F401 — 장중 시각 고정 fixture
from tests.test_invest_ai import Script

DAY = (2026, 10, 13)  # 화요일


def at(hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(*DAY, h, m, tzinfo=KST)


@pytest.fixture
def conn(client):
    c = connect()
    yield c
    c.close()


class Holiday(SampleMarket):
    def session(self) -> dict:
        return {"date": "2026-10-13", "is_open": False, "start": None, "end": None, "note": "오늘은 휴장일입니다"}


# ---- 게이트 ----
@pytest.mark.parametrize("stances, failed, status, direction", [
    (["buy", "buy", "buy", "none", "sell"], 0, "aligned", "buy"),       # 5명 중 3 매수, 반대 1명(허용 1명)
    (["buy", "buy", "buy", "sell", "sell"], 0, "conflict", None),       # 반대 2명 > 허용 1명
    (["buy", "buy", "none", "none", "none"], 0, "conflict", None),      # 과반이 아님 (만장일치 게이트라면 열림)
    (["sell", "sell", "sell", "buy"], 0, "conflict", None),             # 4명이면 허용 반대 0명
    (["sell", "sell", "sell", "none"], 0, "aligned", "sell"),
    (["buy", "buy", "buy"], 2, "aligned", "buy"),                       # 응답 실패는 의견 없음처럼 — 5명 중 3
    (["buy", "buy"], 2, "conflict", None),
    (["none", "none"], 0, "no_view", None),
])
def test_supermajority_gate(stances, failed, status, direction):
    g = agents.supermajority_gate(stances, failed)
    assert (g["status"], g["direction"]) == (status, direction), g["reason"]


def test_prompts_state_the_gate_rule(conn):
    scope = {"scope": "autopilot", "label": "자동 운용", "candidates": [
        {"symbol": "005930", "name": "삼성전자", "security_type": "STOCK", "held": 0, "why": "운용 지시"}]}
    s = agents.Session(conn=conn, scope=scope, run_id=None, provider=SampleMarket(), gate_mode="supermajority")
    text = agents.system_prompt(agents.DEFAULT_ROSTER[0], s)
    assert agents.GATE_RULE["supermajority"] in text and "후보 이유: 운용 지시" in text
    assert agents.GATE_RULE["unanimous"] in agents.system_prompt(agents.DEFAULT_ROSTER[0], agents.Session(
        conn=conn, scope=scope, run_id=None, provider=SampleMarket()))


def test_session_gate_modes():
    def st(stance, error=None):
        return {"views": [{"symbol": "005930", "stance": stance}], "error": error}
    final = [st("buy"), st("buy"), st("buy"), st("sell"), st("none", error="timeout")]
    assert agents.session_gate(final, ["005930"])["status"] == "incomplete"           # 만장일치: 실패가 있으면 닫힘
    g = agents.session_gate(final, ["005930"], mode="supermajority")
    assert g["status"] == "aligned" and g["aligned"] == [{"symbol": "005930", "direction": "buy"}] and g["mode"] == "supermajority"


# ---- 운용 지시 → 후보 ----
def test_intent_symbols_and_sectors(conn):
    p = SampleMarket()
    names = lambda t: [x["name"] for x in scopes.intent_symbols(conn, p, t)]  # noqa: E731
    assert names("하이닉스 잘 봐줘, 삼성전자는 공격적으로, LG에너지솔루션 관심") == ["SK하이닉스", "삼성전자", "LG에너지솔루션"]
    assert names("반도체 쪽 ETF") == []                                       # ETF 는 이름 전체가 있을 때만
    assert names("KODEX 반도체 사 줘") == ["KODEX 반도체"]
    assert names("005380 은 팔아도 돼") == ["현대차"]
    assert names("공격적으로 해 줘") == []
    assert [s["name"] for s in scopes.intent_sectors(p, "2차전지 섹터를 좀 주목해서")] == ["2차전지"]
    assert scopes.intent_sectors(p, "공격적으로") == []


def test_scan_scope_screens_many_stocks(conn, broker):
    broker.positions["035420"] = {"quantity": 3.0, "cost": 570_000.0}
    p = SampleMarket()
    out = scopes.resolve(conn, p, {"scope": "autopilot", "intents": ["하이닉스 좀 잘 봐줘", "2차전지 섹터 주목"]}, 10)
    sc = out["screening"]
    rows = {r["symbol"]: r for r in sc["rows"]}
    assert sc["pool"] >= 10 and sc["by"] == "code" and out["label"] == "보유 + 운용 지시"
    assert "운용 지시" in rows["000660"]["sources"] and "보유" in rows["035420"]["sources"]
    assert "섹터 2차전지" in rows["373220"]["sources"] and any("상위" in x for r in sc["rows"] for x in r["sources"])
    scores = [r["score"] for r in sc["rows"] if r["score"] is not None]
    assert scores == sorted(scores, reverse=True) and all(r["metrics"]["rsi"] is not None for r in sc["rows"] if r["score"] is not None)
    assert [c["symbol"] for c in out["candidates"][:2]] == ["000660", "035420"]                  # 지시·보유 하나씩 먼저
    assert len(out["candidates"]) == 10 and out["candidates"][1]["held"] == 3.0
    assert all(r["picked"] == (r["symbol"] in {c["symbol"] for c in out["candidates"]}) for r in sc["rows"])
    assert any("운용 지시에서 찾은 종목: SK하이닉스(000660)" in n for n in out["notes"])
    cut = scopes.resolve(conn, p, {"scope": "scan", "intents": []}, 2)
    assert [c["symbol"] for c in cut["candidates"]][0] == "035420" and len(cut["candidates"]) == 2 and cut["label"] == "시장 스캔"


def test_screening_score_and_llm_pick(monkeypatch):
    from app.invest.analysis import screening
    m = {"trend": 1, "change_20d": 0.5, "change_5d": -0.01, "volume_ratio": 2.0, "rsi": 80, "change_1d": 0.0, "price": 1}
    assert screening.score(m, ["운용 지시", "거래대금 상위"]) == round(1 + 0.3 * 5 - 0.06 + 0.5 - 0.7 + 1.0 + 0.1, 2)
    assert screening.score(None, ["보유"]) is None
    rows = [{"symbol": s, "name": s, "security_type": "", "sources": src, "score": sc, "metrics": None}
            for s, src, sc in (("000001", ["거래대금 상위"], 3.0), ("000002", ["보유"], 1.0), ("000003", ["운용 지시"], 0.5),
                               ("000004", ["관심"], None))]
    seen = {}

    async def fake(feature, system, prompt, schema, model):
        seen["prompt"] = prompt
        return {"picks": [{"symbol": "000003", "reason": "지시"}, {"symbol": "999999", "reason": "없는 종목"},
                          {"symbol": "000003", "reason": "중복"}], "summary": "반도체 중심"}

    monkeypatch.setattr(screening, "LLM", fake)
    got = screening.llm_pick(rows, "반도체 주목", 3)
    assert [x["symbol"] for x in got["picks"]] == ["000003", "000001", "000002"] and got["by"] == "llm"   # 모자라면 점수 순
    assert got["picks"][1]["reason"].startswith("점수 순으로 채움") and "반도체 주목" in seen["prompt"]
    assert "000004(000004) [관심] 시세를 못 받음" in seen["prompt"]

    async def offline(*a, **k):
        raise LLMError("x")

    monkeypatch.setattr(screening, "LLM", offline)
    code = screening.llm_pick(rows, "", 2)
    assert code["by"] == "code" and [x["symbol"] for x in code["picks"]] == ["000003", "000002"]          # 지시·보유 먼저


# ---- 일정 ----
@pytest.fixture
def pilot(conn, monkeypatch):
    """켜진 자동 운용 + 주문 가능 + 세션 시작은 기록만."""
    autopilot.save_config(conn, enabled=True)
    monkeypatch.setattr(config, "trading_blockers", lambda: [])
    started: list[dict] = []

    def fake_start(c, entry, **kw):
        started.append({**entry, **kw})
        return 100 + len(started)

    monkeypatch.setattr(autopilot, "_start", fake_start)
    return started


def test_tick_runs_due_slots_and_records(conn, pilot):
    p = SampleMarket()
    assert autopilot.tick(conn, at("09:05"), p) is None                      # 09:10 아직
    rec = autopilot.tick(conn, at("09:12"), p)
    assert rec["status"] == "started" and rec["run_id"] == 101 and pilot[0]["key"] == "fixed:09:10"
    assert autopilot.tick(conn, at("09:13"), p) is None                      # 이미 함
    rec = autopilot.tick(conn, at("12:52"), p)                               # 22분 늦음 → 놓침
    assert rec["status"] == "missed" and len(pilot) == 1
    assert autopilot.tick(conn, at("15:01"), p)["status"] == "started"
    entries = autopilot.status(conn, at("15:02"), p)["today"]["entries"]
    assert [(e["time"], e["status"], e["run_id"]) for e in entries] == [
        ("09:10", "started", 101), ("12:30", "missed", None), ("15:00", "started", 102)]
    # 다음 날은 새 일정
    assert autopilot.load_plan(conn, datetime(2026, 10, 14).date())["log"] == []


def test_tick_skips_when_blocked_or_closed(conn, pilot, monkeypatch):
    config.save_trading(conn, halt=True)
    assert autopilot.tick(conn, at("09:10"), SampleMarket())["note"].startswith("전체 주문 중지")
    config.save_trading(conn, halt=False)
    assert autopilot.tick(conn, at("12:31"), Holiday())["note"] == "휴장일이에요"
    autopilot.save_config(conn, slots=["09:01", "15:00"])
    plan = autopilot.load_plan(conn, at("09:00").date())
    plan["log"] = []
    autopilot._put(conn, autopilot.S_PLAN, plan)
    assert "자동 판단 시간(09:05~15:15)" in autopilot.tick(conn, at("09:02"), SampleMarket())["note"]
    monkeypatch.setattr(autopilot, "active_session", lambda c: {"id": 9})
    assert autopilot.tick(conn, at("15:03"), SampleMarket()) is None          # 다른 토론이 끝나길 기다림
    assert pilot == []
    autopilot.save_config(conn, enabled=False)
    monkeypatch.setattr(autopilot, "active_session", lambda c: None)
    assert autopilot.tick(conn, at("15:04"), SampleMarket()) is None


def test_apply_proposal_add_move_and_checks(conn):
    autopilot.save_config(conn, enabled=True, max_extra=1)
    win = ("09:05", "15:15")
    now = at("10:00")
    apply = lambda **p: autopilot.apply_proposal(conn, p, now, win, run_id=7)  # noqa: E731
    assert apply(action="none", time="", reason="")["note"] == "오늘 남은 판단 시각은 그대로예요"
    assert "10분 뒤부터" in apply(action="add", time="10:05", reason="r")["note"]
    assert "밖이라" in apply(action="add", time="15:40", reason="r")["note"]
    assert "12:30 판단과 20분 안" in apply(action="add", time="12:20", reason="r")["note"]
    assert "형식" in apply(action="add", time="25:00", reason="r")["note"]
    ok = apply(action="add", time="11:00", reason="변동성 확인")
    assert ok["accepted"] and ok["note"] == "11:00에 판단을 하나 더했어요"
    assert "다 써서" in apply(action="add", time="14:00", reason="r")["note"]
    moved = apply(action="move", time="13:40", reason="점심 뒤 거래량")
    assert moved["accepted"] and moved["from"] == "12:30"
    entries = autopilot.schedule(autopilot.get_config(conn), autopilot.load_plan(conn, now.date()))
    assert [(e["time"], e["kind"], e["origin"]) for e in entries] == [
        ("09:10", "fixed", "09:10"), ("11:00", "extra", None), ("13:40", "fixed", "12:30"), ("15:00", "fixed", "15:00")]
    assert autopilot.apply_proposal(conn, {"action": "add", "time": "14:00"}, now, None)["note"].startswith("오늘은 휴장일")


def test_config_validation(conn):
    with pytest.raises(autopilot.AutopilotError, match="HH:MM"):
        autopilot.save_config(conn, slots=["9:10"])
    with pytest.raises(autopilot.AutopilotError, match="정규장"):
        autopilot.save_config(conn, slots=["08:30"])
    assert autopilot.save_config(conn, slots=["15:00", "09:30", "09:30"])["slots"] == ["09:30", "15:00"]
    with pytest.raises(autopilot.AutopilotError, match="20개"):
        for i in range(21):
            autopilot.add_intent(conn, f"지시 {i}")


# ---- API ----
def test_autopilot_api_pin_and_intents(client, broker):
    s = client.get("/api/invest/autopilot").json()
    assert s["config"] == autopilot.DEFAULT_CONFIG and s["intents"] == [] and s["next"] is None
    assert client.put("/api/invest/autopilot", json={"enabled": True, "slots": ["09:20"]}).status_code == 403  # 시각은 PIN
    assert not autopilot.switch_on()                                                                      # 막히면 스위치도 그대로
    s = client.put("/api/invest/autopilot", json={"enabled": True, "slots": ["09:20", "13:00", "15:05"]}, headers=PIN).json()
    assert s["config"]["enabled"] and s["config"]["slots"] == ["09:20", "13:00", "15:05"]
    assert client.post("/api/invest/autopilot/intents", json={"text": "반도체 주목"}).status_code == 403
    s = client.post("/api/invest/autopilot/intents", json={"text": "하이닉스 잘 봐줘"}, headers=PIN).json()
    assert [i["text"] for i in s["intents"]] == ["하이닉스 잘 봐줘"]
    preview = client.get("/api/invest/autopilot/preview").json()
    assert preview["candidates"][0]["symbol"] == "000660"
    assert client.delete(f"/api/invest/autopilot/intents/{s['intents'][0]['id']}", headers=PIN).json()["intents"] == []
    assert client.put("/api/invest/autopilot", json={"slots": ["8:00"]}, headers=PIN).status_code == 400
    assert client.put("/api/invest/autopilot", json={"enabled": False}).json()["config"]["enabled"] is False  # 끄기는 PIN 없이
    assert client.post("/api/invest/autopilot/run", headers=PIN).status_code == 409                          # 꺼져 있음


def test_enable_needs_toss_keys(client):
    r = client.put("/api/invest/autopilot", json={"enabled": True}, headers=PIN)
    assert r.status_code == 409 and "토스" in r.json()["detail"]


def test_switch_lives_in_env_without_pin(client, conn, broker):
    """켜기·끄기는 PIN 없이 .env 의 MIA_AUTOPILOT 을 고치고, .env 를 손으로 고쳐도 서버를 다시 켜지 않고 따른다."""
    env = envfile.env_path()
    s = client.put("/api/invest/autopilot", json={"enabled": True}).json()                 # PIN 없이 켜기
    assert s["config"]["enabled"] and "MIA_AUTOPILOT=1" in env.read_text()
    assert "enabled" not in autopilot._get(conn, autopilot.S_CONFIG, {})                   # DB 에는 남기지 않는다
    env.write_text(env.read_text().replace("MIA_AUTOPILOT=1", "MIA_AUTOPILOT=0"))           # 손으로 끄기
    assert client.get("/api/invest/autopilot").json()["config"]["enabled"] is False
    env.write_text(env.read_text().replace("MIA_AUTOPILOT=0", "MIA_AUTOPILOT=true"))
    assert autopilot.get_config(conn)["enabled"]
    assert client.put("/api/invest/autopilot", json={"enabled": False}).json()["config"]["enabled"] is False
    assert "MIA_AUTOPILOT=0" in env.read_text()
    autopilot._put(conn, autopilot.S_CONFIG, {"enabled": True})                             # 예전 DB 값은 보지 않는다
    assert autopilot.get_config(conn)["enabled"] is False
    assert client.put("/api/invest/autopilot", json={"mode": "meetings"}).status_code == 403  # 방식 바꾸기는 그대로 PIN


def test_switch_endpoint_for_sidebar(client, broker):
    """사이드바 표시용 — 켜짐 여부 · 방식만 가볍게."""
    assert client.get("/api/invest/autopilot/switch").json() == {"enabled": False, "mode": "meetings"}
    client.put("/api/invest/autopilot", json={"enabled": True})
    assert client.get("/api/invest/autopilot/switch").json()["enabled"] is True


# ---- 세션 ----
MAJORITY = {"차티스트": ["buy"], "가치 투자자": ["buy"], "뉴스 애널리스트": ["buy"], "모멘텀 투자자": ["buy"],
            "포트폴리오 매니저": ["none"], "리스크 매니저": ["sell"]}  # 분석 6명: 매수 4(과반) · 반대 1(허용 1)


def test_autopilot_session_submits_with_supermajority(client, conn, broker, at_open, monkeypatch):
    client.put("/api/invest/autopilot", json={"enabled": True}, headers=PIN)
    client.post("/api/invest/autopilot/intents", json={"text": "삼성전자 공격적으로 봐줘"}, headers=PIN)
    autopilot.add_intent(conn, "현대차는 이번 판단에서 빼 줘", once=True)
    script = Script(MAJORITY, trade={"symbol": "005930", "side": "buy", "quantity": 1, "order_type": "market", "reason": "과반"})
    monkeypatch.setattr(agents, "LLM", script)
    seen = {}

    async def planner(feature, system, prompt, schema, model):
        seen.update(feature=feature, prompt=prompt)
        return {"action": "add", "time": "13:00", "reason": "체결 확인"}

    monkeypatch.setattr(autopilot, "PLANNER", planner)
    run = client.post("/api/invest/autopilot/run", headers=PIN).json()
    assert run["status"] == "succeeded", run.get("error")
    s = run["summary"]
    assert s["autopilot"] and s["gate_mode"] == "supermajority" and s["gate"] == "aligned"
    assert s["order_status"] == "filled" and len(broker.posts()) == 1                   # 승인 없이 제출
    assert s["next_check"] == "13:00에 판단을 하나 더했어요"
    msgs = client.get(f"/api/invest/agent-sessions/{run['id']}/messages").json()["messages"]
    start = msgs[0]
    assert start["type"] == "start" and start["gate"] == "supermajority" and start["auto_approve"]
    assert start["candidates"][0]["why"].startswith("운용 지시")
    screening_msg = next(m for m in msgs if m["type"] == "screening")
    assert screening_msg["pool"] >= 10 and screening_msg["by"] == "code" and screening_msg["picks"][0]["symbol"] == "005930"
    assert any(m["type"] == "user" and m["text"] == "[운용 지시] 삼성전자 공격적으로 봐줘" for m in msgs)
    assert any(m["type"] == "user" and m["text"] == "[운용 지시 · 이번만] 현대차는 이번 판단에서 빼 줘" for m in msgs)
    assert [i["text"] for i in autopilot.intents(conn)] == ["삼성전자 공격적으로 봐줘"]      # '이번만'은 쓴 뒤 지움
    sched = next(m for m in msgs if m["type"] == "schedule")
    assert sched["accepted"] and sched["time"] == "13:00"
    assert "운용 지시:\n- 삼성전자 공격적으로 봐줘" in seen["prompt"] and seen["feature"] == "agent_action"
    st = client.get("/api/invest/autopilot").json()
    assert [e["time"] for e in st["today"]["entries"] if e["kind"] == "extra"] == ["13:00"]
    assert any(e["kind"] == "manual" and e["run_id"] == run["id"] for e in st["today"]["entries"])
    # 같은 입장이라도 수동 토론(만장일치)이면 반대 1명 때문에 닫힌다
    manual = client.post("/api/invest/agent-sessions", json={"symbol": "005930", "rounds": 1}, headers=PIN).json()
    assert manual["summary"]["gate"] == "conflict" and len(broker.posts()) == 1


def test_autopilot_job_refuses_when_disabled(conn, broker, at_open):
    run_id = start_run(conn, "agent_action", {"autopilot": True, "slot": "fixed:09:10"})
    row = conn.execute("SELECT status, error FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert row["status"] == "failed" and "꺼져" in row["error"]


def test_service_registered_and_off_in_tests():
    assert service in registry.services()
    service.start()  # MIA_SERVICES=0 이라 띄우지 않는다
    assert service._thread is None or not service._thread.is_alive()
