"""운용 데스크: 리서치 세 단계(코드 대체·LLM 검사), 기록·채팅방 말풍선, 회의 후보에 발굴 목록·리스크 경보, 필요할 때만 회의(agenda),
리스크 감시 → 수시 회의, 일정(tick: 아침 리서치·감시 간격·시장 급변), API·비서 도구·보고서."""

import pytest

from app.invest import assistant, autopilot, briefing, desk, desk_agents, scopes, screening
from app.invest.market import SampleMarket
from tests.test_invest import PIN, at_open  # noqa: F401 — 장중 시각 고정 fixture
from tests.test_invest_assistant import Fake, say, thread
from tests.test_invest_autopilot import Holiday, at, conn, pilot  # noqa: F401 — fixture


def sample(hhmm: str) -> SampleMarket:
    return SampleMarket(clock=lambda: at(hhmm))


def agent_msgs(conn) -> list[dict]:
    return [m for m in assistant.messages(conn) if m["role"] == "agent"]


# ---- 리서치 ----
def test_research_falls_back_to_code_and_records(conn, broker, at_open):
    out = desk_agents.research(conn, SampleMarket(), run_id=None)
    mv, sv, iv = out["market"], out["sector"], out["ideas"]
    assert mv["by"] == "code" and mv["regime"] in desk.REGIME_KO and any(i["symbol"] == "KOSPI" for i in mv["indicators"])
    assert sv["by"] == "code" and sv["count"] == 7 and 1 <= len(sv["overweight"]) <= 3
    assert all(set(r["rates"]) == {"1d", "1w", "1mo", "3mo"} for r in sv["rows"])
    scores = [r["score"] for r in sv["rows"]]
    assert scores == sorted(scores, reverse=True)
    assert iv["by"] == "code" and 0 < len(iv["picks"]) <= desk_agents.IDEAS and iv["pool"] >= len(iv["picks"])
    over = {x["name"] for x in sv["overweight"]}
    assert any(src.startswith("섹터 ") and src[3:] in over for r in iv["rows"] for src in r["sources"])
    day = at_open_day()
    assert [desk.latest(conn, s, day)["data"]["by"] for s in ("market", "sector", "ideas")] == ["code"] * 3
    msgs = agent_msgs(conn)
    assert [(m["meta"]["agent"], m["meta"]["desk"]) for m in msgs] == [("strategist", "market"), ("sector", "sector"), ("scout", "ideas")]
    assert msgs[0]["meta"]["name"] == "올리비아" and msgs[0]["meta"]["pokemon"] == 164
    assert "샘플 시세" in " ".join(out["notes"])


def at_open_day():
    from tests.test_invest import OPEN_TIME
    return OPEN_TIME.date()


def test_research_llm_answers_are_checked(conn, broker, at_open, monkeypatch):
    autopilot.add_intent(conn, "2차전지 섹터를 좀 주목해서, 공격적으로")
    seen: dict[str, tuple[str, str]] = {}

    async def fake(feature, system, prompt, schema, model):
        assert feature == "invest_desk"
        if schema is desk_agents.MARKET_SCHEMA:
            seen["market"] = (system, prompt)
            return {"regime": "risk_on", "headline": "반도체 주도 상승", "summary": "코스피가 올랐어요.", "favor": ["반도체", "2차전지"],
                    "avoid": ["금융"], "cash": "lower"}
        seen["sector"] = (system, prompt)
        return {"overweight": [{"id": "S20", "reason": "지시 + 1개월 강세"}, {"id": "S99", "reason": "없는 섹터"},
                               {"id": "S20", "reason": "중복"}, {"id": "S11", "reason": "반도체"}],
                "underweight": [{"id": "S11", "reason": "확대와 겹침"}, {"id": "S50", "reason": "약세"}], "summary": "성장 섹터 중심"}

    async def pick(feature, system, prompt, schema, model):
        seen["scout"] = (system, prompt)
        return {"picks": [{"symbol": "373220", "reason": "2차전지 대표"}, {"symbol": "000660", "reason": "반도체 강세"}],
                "summary": "성장주 위주"}

    monkeypatch.setattr(desk_agents, "LLM", fake)
    monkeypatch.setattr(screening, "LLM", pick)
    out = desk_agents.research(conn, SampleMarket())
    mv, sv, iv = out["market"], out["sector"], out["ideas"]
    assert (mv["by"], mv["regime"], mv["cash"], mv["favor"]) == ("llm", "risk_on", "lower", ["반도체", "2차전지"])
    assert [x["id"] for x in sv["overweight"]] == ["S20", "S11"] and [x["id"] for x in sv["underweight"]] == ["S50"]
    assert sv["overweight"][0]["name"] == "2차전지" and sv["by"] == "llm"
    assert "[S20] 2차전지 (대분류)" in seen["sector"][1] and "[운용 지시]" in seen["sector"][1] and "반도체 주도 상승" in seen["sector"][1]
    assert "공격적으로" in seen["market"][1] and "코스피" in seen["market"][1] and "올리비아" in seen["market"][0]
    assert seen["scout"][0] == desk_agents.SCOUT_SYSTEM and "비중 확대 섹터: 2차전지" in seen["scout"][1]
    assert "발굴 목록 8개를 고르세요" in seen["scout"][1]
    assert [x["symbol"] for x in iv["picks"]][:2] == ["373220", "000660"] and len(iv["picks"]) == desk_agents.IDEAS
    assert iv["picks"][0]["reason"] == "2차전지 대표" and "섹터 2차전지" in iv["picks"][0]["sources"]
    text = desk.brief(conn, at_open_day())
    assert "위험 선호 — 반도체 주도 상승" in text and "비중 확대 2차전지(지시 + 1개월 강세)" in text and "LG에너지솔루션 — 2차전지 대표" in text


def test_research_job_and_api(client, broker, at_open):
    r = client.post("/api/invest/desk/research", json={"note": "2차전지도 봐 줘"})
    assert r.status_code == 201
    run = client.get(f"/api/runs/{r.json()['id']}").json()
    assert run["status"] == "succeeded" and run["kind"] == "desk_research", run
    assert run["summary"]["regime"] in desk.REGIME_KO and run["summary"]["ideas"] and run["summary"]["sectors"] == 7
    st = client.get("/api/invest/desk").json()
    assert [p["key"] for p in st["people"]] == ["strategist", "sector", "scout"] and st["running"] is None
    steps = {s["key"]: s for s in st["process"]}
    assert list(steps) == ["market", "sector", "ideas", "committee", "execution", "risk"]
    assert steps["market"]["at"] and steps["ideas"]["text"] and steps["committee"]["who"] == "portfolio"
    assert st["research"][0]["trigger"] == "manual" and st["ideas"]["picks"]


def test_research_refuses_while_running(conn, monkeypatch):
    monkeypatch.setattr(desk, "research_running", lambda c: {"id": 7, "name": "리서치"})
    with pytest.raises(desk.DeskError) as e:
        desk.start_research(conn)
    assert e.value.status == 409 and "#7" in str(e.value)


# ---- 회의 후보에 발굴 목록·경보 ----
def test_scan_uses_ideas_brief_and_focus(conn, broker, at_open):
    desk.save(conn, "market", {"regime": "risk_off", "headline": "금리 급등", "favor": [], "avoid": ["성장주"], "cash": "raise", "by": "llm"})
    desk.save(conn, "ideas", {"picks": [{"symbol": "086520", "name": "에코프로", "reason": "2차전지 반등"}], "pool": 12, "by": "llm"})
    seen = {}

    def pick(rows, text, n, ctx):
        seen.update(ctx=ctx, n=n)
        return screening.code_pick(rows, n)

    out = scopes.resolve(conn, SampleMarket(), {"scope": "autopilot", "intents": [], "focus": ["005380"],
                                                "focus_why": {"005380": "현대차(005380) 오늘 -5.0% · 보유 종목 급락"}}, 4, pick)
    rows = {r["symbol"]: r for r in out["screening"]["rows"]}
    assert "발굴" in rows["086520"]["sources"] and "리스크 경보" in rows["005380"]["sources"]
    assert out["candidates"][0]["symbol"] == "005380" and out["candidates"][0]["why"].startswith("리스크 경보 — 현대차(005380) 오늘 -5.0%")
    assert len(out["candidates"]) == 4 and "위험 회피 — 금리 급등" in seen["ctx"] and "에코프로 — 2차전지 반등" in seen["ctx"]
    assert any("발굴 목록 1종목" in n for n in out["notes"])


def test_debaters_see_the_research_brief(conn, at_open):
    from app.invest import agents
    desk.save(conn, "market", {"regime": "risk_on", "headline": "반도체 강세", "by": "llm"})
    s = agents.Session(conn=conn, scope={"scope": "symbol", "label": "x", "candidates": [
        {"symbol": "005930", "name": "삼성전자", "security_type": "STOCK", "held": 0}]}, run_id=None, provider=SampleMarket(),
        brief=desk.brief(conn, at_open_day()))
    assert "[리서치 데스크" in agents.system_prompt(agents.DEFAULT_ROSTER[0], s) and "반도체 강세" in s.brief


# ---- 필요할 때만 회의 ----
def test_agenda_meets_only_with_something_new(conn, pilot, monkeypatch):
    autopilot.save_config(conn, slots=["09:10", "11:00", "12:30", "15:00"])
    p = sample("09:12")
    assert autopilot.tick(conn, at("09:12"), p, agenda=desk.agenda)["note"] == "안건: 오늘 첫 회의"
    rec = autopilot.tick(conn, at("11:02"), p, agenda=desk.agenda)
    assert rec["status"] == "skipped" and rec["note"].startswith("새 안건이 없어 건너뛰었어요")
    desk.save(conn, "ideas", {"picks": [], "by": "code"}, now=at("11:30"))
    rec = autopilot.tick(conn, at("12:32"), p, agenda=desk.agenda)
    assert rec["status"] == "started" and rec["note"] == "안건: 새 발굴 목록" and pilot[-1]["agenda"] == "새 발굴 목록"
    rec = autopilot.tick(conn, at("15:02"), p, agenda=desk.agenda)
    assert rec["status"] == "started" and "마지막 회의" in rec["note"]


def test_agenda_rules(conn, pilot, monkeypatch):
    plan = {"date": "2026-10-13", "moved": {}, "extra": [], "log": [
        {"key": "fixed:09:10", "time": "09:10", "kind": "fixed", "status": "started", "at": "09:12:00"}]}
    entry = {"key": "fixed:12:30", "time": "12:30", "kind": "fixed"}
    assert desk.agenda(conn, at("12:30"), entry, plan)[0] == "skip"
    autopilot.add_intent(conn, "반도체 비중 줄여", now=at("10:00"))
    assert desk.agenda(conn, at("12:30"), entry, plan) == ("meet", "새 운용 지시")
    autopilot.save_config(conn, flexible=False)
    autopilot.delete_intent(conn, 1)
    assert desk.agenda(conn, at("12:30"), entry, plan) == ("meet", "정기 회의")
    autopilot.save_config(conn, flexible=True)
    desk.save(conn, "alert", {"text": "삼성전자 급락"}, now=at("11:00"))
    assert desk.agenda(conn, at("12:30"), entry, plan) == ("meet", "리스크 경보")
    monkeypatch.setattr(desk, "research_running", lambda c: {"id": 3, "name": "리서치"})
    assert desk.agenda(conn, at("12:30"), entry, plan)[0] == "wait"
    assert autopilot.tick(conn, at("09:12"), sample("09:12"), agenda=desk.agenda) is None   # 리서치를 기다린다
    assert not pilot


# ---- 리스크 감시 → 수시 회의 ----
def test_risk_watch_alerts_once_and_opens_adhoc_meeting(conn, broker, pilot, monkeypatch):
    broker.positions["000660"] = {"quantity": 2.0, "cost": 180_000.0}
    desk.save(conn, "ideas", {"picks": [{"symbol": "035720", "name": "카카오", "reason": "반등"}], "by": "code"}, now=at("08:30"))
    moves = {"000660": -0.052, "035720": 0.061}
    monkeypatch.setattr(desk_agents, "day_change", lambda p, s: moves.get(s))
    out = desk_agents.watch_and_act(conn, sample("10:00"), at("10:00"))
    assert {(a["symbol"], a["kind"]) for a in out["alerts"]} == {("000660", "drop"), ("035720", "breakout")}
    assert out["adhoc"] == 101 and pilot[-1]["kind"] == "risk" and set(pilot[-1]["focus"]) == {"000660", "035720"}
    assert pilot[-1]["focus"]["000660"].startswith("SK하이닉스(000660) 오늘 -5.2% · 보유 종목 급락")
    msg = agent_msgs(conn)[-1]
    assert msg["meta"]["agent"] == "risk" and msg["meta"]["desk"] == "alert" and "수시 회의를 요청할게요" in msg["text"]
    assert [m["text"] for m in assistant.messages(conn) if m["role"] == "event"][-1].startswith("리스크 경보로 수시 회의를 열었어요")
    assert len(desk.alerts(conn, at("10:00").date())) == 2
    assert desk_agents.watch_and_act(conn, sample("10:10"), at("10:10")) == {}            # 같은 경보는 하루 한 번
    sched = autopilot.status(conn, at("10:11"), sample("10:11"))["today"]["entries"]
    assert [(e["kind"], e["status"]) for e in sched if e["kind"] == "risk"] == [("risk", "started")]


def test_adhoc_waits_for_running_debate_and_has_daily_cap(conn, broker, pilot, monkeypatch):
    broker.positions["000660"] = {"quantity": 2.0, "cost": 180_000.0}
    monkeypatch.setattr(desk_agents, "day_change", lambda p, s: -0.06)
    monkeypatch.setattr(autopilot, "active_session", lambda c: {"id": 9})
    out = desk_agents.watch_and_act(conn, sample("10:00"), at("10:00"))
    assert out["alerts"] and "adhoc" not in out and desk.state(conn, at("10:00").date())["pending"]
    monkeypatch.setattr(autopilot, "active_session", lambda c: None)
    assert desk_agents.watch_and_act(conn, sample("10:10"), at("10:10"))["adhoc"] == 101         # 다음 감시 때 연다
    assert desk.state(conn, at("10:10").date())["pending"] is None
    for i in range(autopilot.MAX_ADHOC - 1):
        autopilot.run_adhoc(conn, {"005930": "x"}, now=at(f"11:{i:02d}"), provider=sample("11:00"))
    with pytest.raises(autopilot.AutopilotError) as e:
        autopilot.run_adhoc(conn, {"005930": "x"}, now=at("12:00"), provider=sample("12:00"))
    assert "수시 회의" in str(e.value)


# ---- 일정 ----
def test_desk_tick_schedule(conn, broker, monkeypatch):
    from app.invest import market
    clock = {"t": at("08:25")}
    monkeypatch.setattr(market, "now_kst", lambda: clock["t"])  # 리서치 작업이 남기는 날짜

    def now(hhmm):
        clock["t"] = at(hhmm)
        return clock["t"]

    assert desk_agents.tick(conn, now("08:30"), sample("08:30")) is None                     # 자동 운용 꺼짐
    autopilot.save_config(conn, enabled=True)
    assert desk_agents.tick(conn, now("08:10"), sample("08:10")) is None                     # 아침 리서치는 08:20부터
    assert desk_agents.tick(conn, now("08:30"), Holiday()) is None
    got = desk_agents.tick(conn, now("08:25"), sample("08:25"))
    day = at("08:25").date()
    assert got["research"] and desk.latest(conn, "ideas", day) and desk.state(conn, day)["research"][0]["trigger"] == "morning"
    assert desk_agents.tick(conn, now("08:40"), sample("08:40")) is None                     # 장 시작 전엔 감시 없음
    assert desk_agents.tick(conn, now("09:06"), sample("09:06")) is None                     # 감시함 (경보 없음)
    assert desk.state(conn, day)["last_watch"] == "09:06:00"
    monkeypatch.setattr(desk_agents, "moved_since", lambda c, p, now: "코스피가 08:25 분석 때보다 +1.8% 움직였어요")
    assert desk_agents.tick(conn, now("09:10"), sample("09:10")) is None                     # 감시 간격 10분
    got = desk_agents.tick(conn, now("09:30"), sample("09:30"))
    assert got["research"] and desk.state(conn, day)["research"][-1]["trigger"] == "market_move"
    assert desk_agents.tick(conn, now("09:45"), sample("09:45")) is None                     # 리서치 간격 60분


def test_moved_since_compares_index_levels(conn, at_open):
    p = SampleMarket()
    kospi = next(i for i in p.indicators() if i["symbol"] == "KOSPI")
    desk.save(conn, "market", {"indicators": [{**kospi, "price": kospi["price"] / 1.02}], "by": "code"}, now=at("08:20"))
    assert "+2.0%" in desk_agents.moved_since(conn, p, at("11:30"))
    desk.save(conn, "market", {"indicators": [kospi], "by": "code"}, now=at("10:00"))
    assert desk_agents.moved_since(conn, p, at("11:30")) is None


# ---- 비서·보고서 ----
def test_assistant_research_tools_and_flexible_setting(client, broker, at_open, monkeypatch):
    from app.core.db import connect
    c = connect()
    desk_agents.research(c, SampleMarket())
    c.close()
    started = []
    monkeypatch.setattr(desk, "start_run", lambda conn, kind, params, name="": started.append((kind, params, name)) or 55)
    fake = Fake([("run_research", {"note": "2차전지 쪽도"}), ("get_research", {}), ("update_schedule", {"flexible": False})],
                reply="리서치를 돌렸어요")
    monkeypatch.setattr(assistant, "LLM", fake)
    assert say(client, "시장 다시 분석해 주고 회의는 정해진 시각마다 꼭 해 줘").status_code == 201
    run_res, get_res, sched = fake.results
    assert run_res["ok"] and run_res["run_id"] == 55 and started[0][:2] == ("desk_research", {"trigger": "assistant", "note": "2차전지 쪽도"})
    assert get_res["market"]["regime"] in desk.REGIME_KO and get_res["team"]["scout"] == "탄디(종목 발굴 애널리스트)"
    assert sched["ok"] and sched["after"]["flexible"] is False
    texts = [m["text"] for m in thread(client)]
    assert "리서치를 시작했어요 — 시장 분석 → 섹터 탐색 → 종목 발굴" in texts and "설정 변경 · 회의 방식: 안건이 있을 때만 → 정기 시각마다" in texts
    assert "오늘 리서치: \n- 시장 올리비아(시장 전략가)" in fake.prompts[0]


def test_briefing_includes_research(conn, broker, at_open):
    desk_agents.research(conn, SampleMarket())
    snap = briefing.snapshot(conn, SampleMarket())
    v, sc = snap["market"]["view"], snap["market"]["sectors"]
    assert v["regime"] in desk.REGIME_KO.values() and v["by"] == "올리비아" and sc["overweight"] and sc["overweight"][0]["name"]
    text = briefing.to_text(snap)
    assert "- 시황 " in text and "올리비아: " in text and "- 섹터 " in text and "비중 확대 " in text


def test_flexible_setting_needs_pin(client, broker):
    r = client.put("/api/invest/autopilot", json={"flexible": False})
    assert r.status_code == 403
    r = client.put("/api/invest/autopilot", json={"flexible": False}, headers=PIN)
    assert r.status_code == 200 and r.json()["config"]["flexible"] is False


def test_desk_people_do_not_clash_with_roster():
    from app.invest import agents
    names = {p[0] for p in agents.PEOPLE.values()} | {p[0] for p in agents.NAME_POOL} | {assistant.PROFILE["name"]}
    assert not names & {p["name"] for p in desk.PEOPLE.values()}
    assert not set(agents.POKEMON.values()) & {p["pokemon"] for p in desk.PEOPLE.values()}


def test_ai_set_meetings_always_meet(conn, pilot):
    """AI 가 이유를 들어 더한 회의는 새 안건이 없어도 연다 (지난 회의가 '그때 다시 본다'고 미뤄 둔 일)."""
    autopilot.save_config(conn, slots=["09:10", "15:00"])
    p = sample("09:12")
    assert autopilot.tick(conn, at("09:12"), p, agenda=desk.agenda)["status"] == "started"
    res = autopilot.apply_proposal(conn, {"action": "add", "time": "10:30", "reason": "주식 비중이 목표 95%에 못 미쳐 남은 현금을 배분"},
                                   at("09:20"), ("09:05", "15:15"))
    assert res["accepted"]
    rec = autopilot.tick(conn, at("10:31"), sample("10:31"), agenda=desk.agenda)
    assert rec["status"] == "started" and rec["note"] == "안건: AI 가 정한 회의 — 주식 비중이 목표 95%에 못 미쳐 남은 현금을 배분"
    plan = {"date": "2026-10-13", "moved": {}, "extra": [], "log": [
        {"key": "fixed:09:10", "time": "09:10", "kind": "fixed", "status": "started", "at": "09:12:00"}]}
    moved = {"key": "fixed:12:30", "time": "11:40", "kind": "fixed", "reason": "체결 확인"}
    assert desk.agenda(conn, at("11:40"), moved, plan) == ("meet", "AI 가 정한 회의 — 체결 확인")
    assert "AI 가 정한 회의는 언제나 열립니다" in autopilot.PLAN_SYSTEM
    assert "회의 방식 안건이 있을 때만" in autopilot.plan_prompt(conn, at("11:00"), ("09:05", "15:15"), "주문 없음")


def test_morning_research_retries_after_failure(conn, broker, monkeypatch):
    from app.invest import market
    clock = {"t": at("08:25")}
    monkeypatch.setattr(market, "now_kst", lambda: clock["t"])
    autopilot.save_config(conn, enabled=True)
    calls = []

    def fake_start(c, trigger="manual", note="", now=None):  # 시작만 하고 결과는 남기지 못함 (서버 재시작으로 중단된 것처럼)
        st = desk.state(c, now.date())
        st["research"].append({"at": now.strftime("%H:%M:%S"), "run_id": len(calls) + 1, "trigger": trigger})
        desk.put_state(c, st)
        calls.append(now.strftime("%H:%M"))
        return len(calls)

    monkeypatch.setattr(desk, "start_research", fake_start)
    for hhmm in ("08:25", "08:30", "08:36", "08:50", "09:10"):
        clock["t"] = at(hhmm)
        desk_agents.tick(conn, at(hhmm), sample(hhmm))
    assert calls == ["08:25", "08:36", "08:50"]                          # 10분 뒤 다시, 하루 3번까지
    desk.save(conn, "ideas", {"picks": [], "by": "code"}, now=at("09:20"))
    monkeypatch.setattr(desk, "MORNING_TRIES", 9)
    clock["t"] = at("09:40")
    desk_agents.tick(conn, at("09:40"), sample("09:40"))
    assert len(calls) == 3                                                # 결과가 있으면 다시 하지 않는다
