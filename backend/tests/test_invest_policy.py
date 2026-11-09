"""운용 목표·자문 에이전트: 항목 형식 검사·기록, 대화 상담(코드 경로·LLM 경로) → 비서 반영(운용 목표·회의·안건), 회의 안건·취소(일정 담당·비서),
프롬프트에 운용 목표·안건, 보고서의 목표 대비·벤치마크."""

import re

import pytest

from app.invest import advisor, agents, assistant, autopilot, briefing, desk, policy, scopes, screening
from app.invest.marketdata.market import SampleMarket
from tests.test_invest import PIN, at_open  # noqa: F401 — 장중 시각 고정 fixture
from tests.test_invest_assistant import say, thread
from tests.test_invest_autopilot import at, conn, pilot  # noqa: F401 — fixture


# ---- 운용 목표 ----
def test_policy_clean_parse_and_save(conn):
    assert policy.clean("horizon", "장투로 갈래요") == "long" and policy.clean("horizon", "단기 (몇 주~3개월)") == "short"
    assert policy.clean("target_return", "연 12%") == 12.0 and policy.clean("benchmark", "코스피 200") == "KOSPI"
    assert policy.clean("objective", "벤치마크 초과 (지수보다 잘하기)") == "relative" and policy.clean("risk", "공격적으로") == "aggressive"
    assert policy.clean("prefer", "반도체, 2차전지·바이오") == ["반도체", "2차전지", "바이오"]
    assert policy.clean("target_return", "") is None and policy.parse("risk", "글쎄요") is None
    for field, bad in (("target_return", "200%"), ("benchmark", "나스닥"), ("nope", 1)):
        with pytest.raises(policy.PolicyError):
            policy.clean(field, bad)
    out = policy.save(conn, {"target_return": "10", "benchmark": "KOSPI", "horizon": "long"}, by="assistant", reason="상담")
    assert [d["label"] for d in out["diff"]] == ["목표 수익률", "벤치마크", "투자 기간"] and out["diff"][0]["after"] == "10% (1년)"
    with pytest.raises(policy.PolicyError):
        policy.save(conn, {"target_return": 12, "risk": "모름"}, by="assistant")          # 하나라도 틀리면 아무것도 안 바뀜
    assert policy.get(conn)["target_return"] == 10
    out = policy.save(conn, {"benchmark": None, "target_return": 12}, by="advisor")
    assert "benchmark" not in out["policy"] and policy.history(conn)[-1]["by"] == "advisor"
    assert policy.save(conn, {"target_return": 12}, by="x")["diff"] == []
    text = policy.text(policy.get(conn))
    assert "목표 수익률: 12% (1년)" in text and "투자 기간: 장기" in text
    assert policy.missing(policy.get(conn)) == ["objective", "benchmark", "turnover", "risk", "max_drawdown", "equity_target"]


# ---- 자문 상담 ----
def test_advice_code_path_interview_then_policy(client, broker, at_open):
    r = client.post("/api/invest/advice/start", json={"topics": ["objective", "target_return", "horizon"]})
    assert r.status_code == 201 and r.json()["open"] and r.json()["remaining"] == 3
    msgs = thread(client)
    assert msgs[-1]["meta"]["agent"] == "advisor" and msgs[-1]["meta"]["choices"][0].startswith("절대 수익")
    assert "에밀리아" in msgs[-1]["text"]
    say(client, "지수보다 잘하는 거요")                                                      # 자문 에이전트가 받는다 (비서 아님)
    say(client, "글쎄요 잘 모르겠어요")
    assert "잘 못 알아들었어요" in thread(client)[-1]["text"]
    say(client, "연 15% 정도")
    adv = client.get("/api/invest/assistant/messages").json()["advice"]
    assert adv["open"] and [t["done"] for t in adv["topics"]] == [True, True, False]
    say(client, "장투")                                                                       # 마지막 답 → 마치고 비서에게
    msgs = thread(client)
    texts = [m["text"] for m in msgs]
    assert "자문 상담 결과를 비서에게 넘겼어요" in texts and any(t.startswith("상담을 마칠게요") for t in texts)
    assert "설정 변경 · 운용 목표 목표 수익률: 없음 → 15% (1년)" in texts                    # 비서가 실패해 코드가 그대로 반영
    p = policy.get(conn_of(client))
    assert (p["objective"], p["target_return"], p["horizon"]) == ("relative", 15, "long")
    assert not client.get("/api/invest/assistant/messages").json()["advice"]["open"]
    assert all(m["role"] != "assistant" or "죄송" in m["text"] for m in msgs)               # 상담 중 말은 비서가 받지 않았다


def conn_of(client):
    from app.core.db import connect
    return connect()


def test_advice_stop_and_stop_words(client, broker, at_open):
    client.post("/api/invest/advice/start")
    say(client, "보수적")                                                                     # 첫 질문(목표 방식)에 맞지 않는 답
    assert client.post("/api/invest/advice/stop").status_code == 200
    assert "기록한 답이 없어서" in thread(client)[-1]["text"] and client.post("/api/invest/advice/stop").status_code == 409
    client.post("/api/invest/advice/start", json={"topics": ["risk", "turnover"]})
    say(client, "중립")
    say(client, "이제 됐어요, 그만")
    assert policy.get(conn_of(client)).get("risk") == "balanced" and "turnover" not in policy.get(conn_of(client))


class AdviceFake:
    """자문 에이전트 각본: 답마다 record_answer(틀린 값 한 번 포함) 후 다음 질문, 끝에 finish."""

    def __init__(self):
        self.prompts: list[str] = []
        self.results: list[dict] = []

    async def __call__(self, feature, system, prompt, schema, tools, model, max_turns):
        self.prompts.append(prompt)
        by = {t.name: t for t in tools}
        new = re.search(r"관리자의 새 답:\n(.*)", prompt, re.S)
        if not new:
            return {"reply": "안녕하세요! 투자로 이루고 싶은 게 뭐예요?", "choices": ["절대 수익", "벤치마크 초과"]}
        self.results.append(await by["record_answer"].handler({"field": "target_return", "value": "300%"}))
        self.results.append(await by["record_answer"].handler({"field": "target_return", "value": 12}))
        self.results.append(await by["record_answer"].handler({"field": "equity_target", "value": "70%"}))
        self.results.append(await by["get_portfolio"].handler({}))
        await by["finish"].handler({"summary": "연 12%, 주식 70%"})
        return {"reply": "정리했어요 — 비서에게 넘길게요", "choices": []}


class SecretaryFake:
    def __init__(self):
        self.prompts: list[str] = []
        self.results: list[dict] = []

    async def __call__(self, feature, system, prompt, schema, tools, model, max_turns):
        self.prompts.append(prompt)
        by = {t.name: t for t in tools}
        if "자문 상담 결과" in prompt:
            for name, args in (("update_policy", {"changes": {"target_return": 12, "equity_target": 70, "turnover": "low"},
                                                  "reason": "상담 답 + 장기라 매매는 가끔"}),
                               ("schedule_meeting", {"time": "12:00", "agenda": "주식 비중 30% → 목표 70% 분할 매수 계획", "symbols": ["005930"]}),
                               ("cancel_meeting", {"time": "15:00", "reason": "매매 빈도 '가끔'"}),
                               ("update_policy", {"changes": {"risk": "모름"}})):
                self.results.append(await by[name].handler(args))
            return {"reply": "운용 목표를 반영하고 12:00 회의를 잡았어요"}
        self.results.append(await by["start_advice"].handler({"topics": ["target_return", "equity_target"]}))
        return {"reply": "자문 에이전트와 상담을 열게요"}


def test_advice_llm_path_and_secretary_adjusts_meetings(client, broker, at_open, monkeypatch):
    c = conn_of(client)
    autopilot.save_config(c, enabled=True)
    adv, sec = AdviceFake(), SecretaryFake()
    monkeypatch.setattr(advisor, "LLM", adv)
    monkeypatch.setattr(assistant, "LLM", sec)
    say(client, "운용 목표를 다시 정하고 싶어")                                                # 비서가 상담을 연다 → 자문 에이전트 첫 질문
    msgs = thread(client)
    assert [m["role"] for m in msgs][-2:] == ["assistant", "agent"] and msgs[-1]["meta"]["choices"] == ["절대 수익", "벤치마크 초과"]
    assert sec.results[0]["ok"] and sec.results[0]["topics"] == ["목표 수익률", "주식 비중 목표"]
    say(client, "연 12%, 주식은 70% 정도")
    bad, ok1, ok2, pf = adv.results
    assert not bad["ok"] and "0~100" in bad["error"] and ok1["ok"] and ok2["value"] == 70
    assert "아직 묻지 않은 항목: target_return(목표 수익률), equity_target(주식 비중 목표)" in adv.prompts[1]
    assert "관리자의 새 답:\n연 12%, 주식은 70% 정도" in adv.prompts[1]
    up, meet, cancel, bad_up = sec.results[1:]
    assert up["ok"] and meet["ok"] and meet["note"].startswith("12:00에 판단을 하나 더했어요 — 안건: 주식 비중 30%")
    assert cancel["ok"] and not bad_up["ok"]
    assert "자문 상담 결과 (운용 목표에 반영하고 회의·안건을 맞추세요):" in sec.prompts[-1] and "- 목표 수익률(target_return): 12% (1년)" in sec.prompts[-1]
    p = policy.get(c)
    assert (p["target_return"], p["equity_target"], p["turnover"]) == (12, 70, "low") and p["updated_by"] == "assistant"
    texts = [m["text"] for m in thread(client)]
    assert "설정 변경 · 운용 목표 매매 빈도: 없음 → 가끔" in texts and texts[-1] == "운용 목표를 반영하고 12:00 회의를 잡았어요"
    entries = {e["time"]: e for e in autopilot.status(c)["today"]["entries"]}
    assert entries["12:00"]["agenda"].startswith("주식 비중") and entries["12:00"]["focus"] == ["005930"] and entries["12:00"]["by"] == "assistant"
    assert entries["15:00"]["status"] == "skipped" and entries["15:00"]["note"] == "비서가 취소: 매매 빈도 '가끔'"


# ---- 회의 안건·취소 ----
def test_ai_agenda_meeting_and_cancel(conn, pilot):
    win = ("09:05", "15:15")
    res = autopilot.apply_proposal(conn, {"action": "add", "time": "10:30", "reason": "현금 배분", "agenda": "남은 현금 70%까지 매수",
                                          "symbols": ["000660", "bad", "005930"]}, at("09:40"), win, run_id=7)
    assert res["accepted"] and res["agenda"] == "남은 현금 70%까지 매수"
    autopilot.tick(conn, at("09:12"), SampleMarket(clock=lambda: at("09:12")), agenda=desk.agenda)
    rec = autopilot.tick(conn, at("10:31"), SampleMarket(clock=lambda: at("10:31")), agenda=desk.agenda)
    assert rec["note"] == "안건: AI 가 정한 회의 — 남은 현금 70%까지 매수"
    assert pilot[-1]["agenda"] == "AI 가 정한 회의 — 남은 현금 70%까지 매수" and set(pilot[-1]["focus"]) == {"000660", "005930"}
    res = autopilot.apply_proposal(conn, {"action": "cancel", "time": "", "reason": "장기 투자라 오늘은 충분"}, at("11:00"), win)
    assert res["accepted"] and res["time"] == "12:30"
    st = {e["time"]: e for e in autopilot.status(conn, at("11:00"), SampleMarket(clock=lambda: at("11:00")))["today"]["entries"]}
    assert st["12:30"]["status"] == "skipped" and st["12:30"]["note"] == "AI가 취소: 장기 투자라 오늘은 충분"
    assert not autopilot.apply_proposal(conn, {"action": "cancel", "time": "13:13", "reason": "x"}, at("11:00"), win)["accepted"]
    for i in range(autopilot.MAX_ASSISTANT):
        assert autopilot.apply_proposal(conn, {"action": "add", "time": f"1{3 + i // 2}:{'00' if i % 2 == 0 else '30'}", "agenda": "a"},
                                        at("11:00"), win, by="assistant")["accepted"]
    assert "비서가 정할 수 있는 회의" in autopilot.apply_proposal(conn, {"action": "add", "time": "11:40", "agenda": "a"}, at("11:00"), win,
                                                               by="assistant")["note"]
    assert "cancel" in autopilot.PLAN_SCHEMA["properties"]["action"]["enum"] and "agenda" in autopilot.PLAN_SCHEMA["properties"]


def test_prompts_carry_policy_and_agenda(conn, broker, at_open):
    policy.save(conn, {"objective": "relative", "benchmark": "KOSPI", "equity_target": 70, "max_drawdown": 15, "risk": "balanced"},
                by="assistant")
    goal = policy.text(policy.get(conn))
    s = agents.Session(conn=conn, scope={"scope": "symbol", "label": "x", "candidates": [
        {"symbol": "005930", "name": "삼성전자", "security_type": "STOCK", "held": 0}]}, run_id=None, provider=SampleMarket(),
        policy=goal, agenda="주식 비중 목표까지 분할 매수")
    text = agents.system_prompt(agents.DEFAULT_ROSTER[0], s)
    assert "[운용 목표" in text and "벤치마크: 코스피" in text and "[이번 회의 안건 — 먼저 다루세요]\n주식 비중 목표까지 분할 매수" in text
    assert "고점 대비 15%에 가까워지면" in goal
    seen = {}

    def pick(rows, txt, n, ctx):
        seen["ctx"] = ctx
        return screening.code_pick(rows, n)

    scopes.resolve(conn, SampleMarket(), {"scope": "autopilot", "intents": [], "agenda": "반도체 비중 점검"}, 3, pick)
    assert "운용 목표:\n- 목표 방식: 벤치마크 초과" in seen["ctx"] and "이번 회의 안건: 반도체 비중 점검" in seen["ctx"]
    assert "운용 목표:" in autopilot.plan_prompt(conn, at("11:00"), ("09:05", "15:15"), "주문 없음")


def test_report_checks_goal_and_benchmark(client, broker, at_open):
    c = conn_of(client)
    briefing.set_baseline(c, [{"date": "2026-10-01", "amount": 1_000_000, "note": "처음"}])
    policy.save(c, {"benchmark": "KOSPI", "equity_target": 70, "target_return": 10}, by="assistant")
    snap = briefing.snapshot(c, SampleMarket())
    g = snap["policy"]
    assert g["benchmark"]["symbol"] == "KOSPI" and g["benchmark"]["change"] is not None
    assert g["benchmark"]["excess"] == round(snap["performance"]["baseline"]["return_rate"] - g["benchmark"]["change"], 4)
    assert g["equity"]["target"] == 70 and g["return"]["target_so_far"] == round(0.10 * g["return"]["days"] / 365, 4)
    text = briefing.to_text(snap)
    assert "[운용 목표]" not in text and "목표 대비: 주식 비중" in text and "코스피" in text     # 목표 항목은 빼고 목표 대비만
    view = client.get("/api/invest/policy").json()
    assert view["policy"]["benchmark"] == "KOSPI" and "objective" in view["missing"] and len(view["fields"]) == len(policy.FIELDS)
    assert client.get("/api/invest/agents").json()["desk"]["advisor"]["name"] == "에밀리아"
    assert PIN
