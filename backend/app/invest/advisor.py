"""자문 에이전트 — 관리자와 대화하며 운용 목표(policy.py)를 정한다. 한 번에 한두 가지를 묻고(고를 수 있는 답 choices 와 함께),
답을 항목별로 기록해(record_answer — policy.clean 으로 검사) 초안(draft)을 만든다. 끝나면(finish, 또는 관리자가 상담 끝내기) 비서에게
넘긴다 — 비서가 초안을 보고 운용 목표를 고치고, 회의 시각·안건·운용 지시를 맞춘다(assistant.apply_advice).

- 상담 중(settings invest_advice open)에는 AI Trading 채팅 입력이 비서 대신 자문 에이전트에게 간다(assistant._process 가 나눔).
- 시작: 화면(운용 목표 '자문 에이전트와 상담')·비서(start_advice — 비서 답 뒤에 첫 질문)·API.
- LLM(기능 invest_assistant)이 실패하면 코드가 묻는다: 지금 묻는 항목의 답을 글에서 찾고(policy.parse) 다음 빠진 항목을 정해진 질문
  (QUESTIONS)으로. 숫자·고르는 값만 받아도 상담이 끝까지 간다. 대화가 MAX_TURNS 번을 넘으면 그때까지로 마친다.
- 투자 권유가 아니라 목표를 정리하는 대화 — 수익을 장담하지 않고, 목표 수익률과 손실 허용 한도가 맞지 않으면 짚는다.
"""

import asyncio
import json
import logging
import sqlite3
from collections.abc import Awaitable, Callable

from app.invest import agents, assistant, config, desk, market, policy
from app.llm.client import LLMError, ToolDef, agentic

log = logging.getLogger("uvicorn.error")

PROFILE = {k: v for k, v in desk.ADVISOR.items() if k != "role"}
KEY = PROFILE["key"]
S_ADVICE = "invest_advice"
TOPICS = policy.REQUIRED + ["max_position", "prefer", "avoid"]
MAX_TURNS = 20      # 관리자 답이 이만큼 넘으면 그때까지로 마친다
LLM_TURNS = 6
HISTORY = 16
STOP_WORDS = ("그만", "끝내", "됐어", "이제 됐", "여기까지", "마칠게", "마치자")

# LLM 없이 물을 때 — 항목마다 질문과 고를 수 있는 답
QUESTIONS: dict[str, tuple[str, list[str]]] = {
    "objective": ("투자로 가장 이루고 싶은 게 뭐예요? 운용 방식의 기준이 돼요.",
                  ["절대 수익 (시장과 상관없이 플러스)", "벤치마크 초과 (지수보다 잘하기)", "장기 성장", "배당·현금 흐름", "원금 보존 우선"]),
    "target_return": ("1년에 몇 % 정도를 목표로 할까요? 높을수록 손실 가능성도 커져요.", ["연 5%", "연 10%", "연 15%", "연 20% 이상"]),
    "benchmark": ("성과를 지수와 비교할까요? 비교한다면 어떤 지수로 할까요?", ["코스피", "코스닥", "없음"]),
    "horizon": ("이 돈은 얼마나 오래 굴릴 생각이에요? 장투·단투 여부예요.", ["단기 (몇 주~3개월)", "중기 (3개월~1년)", "장기 (1년 이상)"]),
    "turnover": ("얼마나 자주 사고팔았으면 해요? 회의 횟수도 이걸로 맞춰요.", ["가끔 (주 1회 이하)", "보통 (하루 한두 번)", "자주 (하루 여러 번)"]),
    "risk": ("오르내림을 얼마나 견딜 수 있어요?", ["보수적", "중립", "공격적"]),
    "max_drawdown": ("고점 대비 몇 %까지 빠져도 괜찮아요? 그 근처면 에이전트가 위험을 줄여요.", ["5%", "10%", "20%", "30%"]),
    "equity_target": ("돈 중 몇 %를 주식에 두고 나머지는 현금으로 둘까요?", ["50%", "70%", "90%", "95%"]),
    "max_position": ("한 종목에 최대 몇 %까지 담아도 돼요? (주문 한도보다 낮을 때만 의미가 있어요)", ["10%", "20%", "30%"]),
    "prefer": ("특히 담고 싶은 섹터나 테마가 있어요? 쉼표로 여러 개 적어도 돼요.", ["반도체", "2차전지", "바이오", "없어요"]),
    "avoid": ("피하고 싶은 섹터·종목이 있어요?", ["없어요"]),
}
NONE_WORDS = ("없어", "없음", "딱히", "상관없")

REPLY_SCHEMA = {
    "type": "object",
    "properties": {"reply": {"type": "string"}, "choices": {"type": "array", "items": {"type": "string"}}},
    "required": ["reply", "choices"],
}
_FORMATS = "\n".join(
    f"- {k}({s['label']}): " + (" / ".join(f"{c}={v.split(' —')[0]}" for c, v in s["choices"].items()) if s["type"] == "enum"
                                else f"숫자 {s['min']}~{s['max']} {s['unit']}" if s["type"] == "number"
                                else "글 목록" if s["type"] == "list" else "글")
    for k, s in policy.FIELDS.items())
SYSTEM = f"""당신은 Mallot 의 투자 자문 에이전트 {PROFILE['name']}({PROFILE['full_name']}, {PROFILE['country']})입니다. 관리자(사용자)와 대화하며
운용 목표(투자 정책)를 함께 정합니다. 정한 목표는 비서가 반영하고, 모든 에이전트(리서치·토론·트레이더·일정)가 판단 기준으로 씁니다.
- 한 번에 한두 가지만 쉽게 물으세요. 고를 수 있는 답을 choices 에 2~5개 주세요(관리자가 눌러서 답함, 직접 적어도 됨).
- 관리자의 답을 들으면 record_answer 로 그 항목을 기록하세요. 값 형식:
{_FORMATS}
- 이미 기록한 항목은 다시 묻지 말고, 답이 애매하면 되물으세요. 지금 운용 목표·투자현황(get_portfolio)을 보고 구체적으로 물으세요
  (예: 지금 주식 비중이 30%인데 목표는 몇 %로 할까요?). 지금 값이 있으면 그대로 둘지 물어도 됩니다.
- 목표 수익률·위험 성향·손실 허용 한도·투자 기간이 서로 맞지 않으면(예: 연 30% 목표인데 손실은 5% 이하만) 짚고 다시 정하게 하세요.
- 상담 항목을 다 기록했거나 관리자가 그만하자고 하면 finish(summary)로 마치세요 — reply 에는 정리한 목표와 '비서에게 넘길게요'.
- 투자 권유가 아니라 목표를 정리하는 대화입니다. 수익을 장담하지 마세요. 한국어, 카카오톡처럼 짧고 친근하게.
- 응답은 JSON 스키마({{"reply": ..., "choices": [...]}})를 따르세요."""

AdviceLlm = Callable[..., Awaitable[dict]]


async def _default_llm(feature: str, system: str, prompt: str, schema: dict, tools: list[ToolDef],
                       model: str | None, max_turns: int) -> dict:
    return await agentic(feature, prompt, schema, tools=tools, model=model, system_prompt=system, max_turns=max_turns)


LLM: AdviceLlm = _default_llm  # 테스트에서 가짜로 바꾼다


# ---- 상담 상태 ----
def _get(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (S_ADVICE,)).fetchone()
    try:
        raw = json.loads(row["value"]) if row else {}
    except ValueError:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def _put(conn: sqlite3.Connection, st: dict) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (S_ADVICE, json.dumps(st, ensure_ascii=False)))
    conn.commit()


def state(conn: sqlite3.Connection) -> dict:
    st = _get(conn)
    return {"open": bool(st.get("open")), "started_at": st.get("started_at"), "by": st.get("by"),
            "topics": [t for t in st.get("topics") or [] if t in policy.FIELDS], "draft": dict(st.get("draft") or {}),
            "asking": st.get("asking"), "turns": int(st.get("turns") or 0), "waiting": bool(st.get("waiting")),
            "note": st.get("note") or ""}


def status(conn: sqlite3.Connection) -> dict:
    """화면 — 상담 중인지, 항목마다 기록했는지."""
    st = state(conn)
    return {"open": st["open"], "started_at": st["started_at"], "turns": st["turns"],
            "topics": [{"key": t, "label": policy.FIELDS[t]["label"], "done": t in st["draft"]} for t in st["topics"]],
            "remaining": len([t for t in st["topics"] if t not in st["draft"]]), "profile": PROFILE}


def remaining(st: dict) -> list[str]:
    return [t for t in st["topics"] if t not in st["draft"]]


class AdviceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def start(conn: sqlite3.Connection, *, by: str = "manual", topics: list[str] | None = None, note: str = "") -> dict:
    """상담을 연다(첫 질문은 opening — 화면이 부르면 바로, 비서가 열면 비서 답 뒤에). 이미 상담 중이면 그대로."""
    st = state(conn)
    if st["open"]:
        return st
    chosen = [t for t in (topics or []) if t in policy.FIELDS] or list(TOPICS)
    _put(conn, {"open": True, "started_at": market.now_kst().strftime("%Y-%m-%d %H:%M"), "by": by, "topics": chosen,
                "draft": {}, "asking": None, "turns": 0, "waiting": True, "note": note.strip()[:300]})
    return state(conn)


def _say(conn: sqlite3.Connection, text: str, choices: list[str] | None = None, **meta) -> dict:
    return assistant.add(conn, "agent", text, agent=KEY, title=PROFILE["title"], name=PROFILE["name"], pokemon=PROFILE["pokemon"],
                         choices=[c for c in (choices or []) if c][:6], advice=True, **meta)


def _question(field: str, current: object) -> tuple[str, list[str]]:
    q, choices = QUESTIONS.get(field, (f"{policy.FIELDS[field]['label']}은(는) 어떻게 할까요?", []))
    if current is not None:
        q += f" (지금은 {policy.show(field, current)} — 그대로 두려면 '그대로'라고 해 주세요)"
        choices = ["그대로"] + choices
    return q, choices


def _ask_next(conn: sqlite3.Connection, st: dict, prefix: str = "") -> dict:
    """코드가 다음 빠진 항목을 묻는다. 남은 항목이 없으면 마친다."""
    rest = remaining(st)
    if not rest:
        return _finish(conn, st, "", by=KEY)
    field = rest[0]
    q, choices = _question(field, policy.get(conn).get(field))
    st["asking"] = field
    _put(conn, {**_get(conn), "asking": field, "waiting": False})
    return _say(conn, (prefix + "\n" if prefix else "") + q, choices, by="code")


# ---- 대화 ----
def _context(conn: sqlite3.Connection, st: dict, new: str | None) -> str:
    p = policy.get(conn)
    lines = [f"지금 운용 목표:\n{policy.text(p) or '아직 없음'}",
             "이번 상담에서 기록한 답(초안):\n" + ("\n".join(f"- {policy.FIELDS[k]['label']}({k}): {policy.show(k, v)}"
                                                  for k, v in st["draft"].items()) or "아직 없음"),
             "아직 묻지 않은 항목: " + (", ".join(f"{k}({policy.FIELDS[k]['label']})" for k in remaining(st)) or "없음 — finish 로 마치세요")]
    if st["note"]:
        lines.append(f"상담을 연 이유: {st['note']}")
    rows = conn.execute("SELECT * FROM (SELECT * FROM invest_assistant_messages ORDER BY id DESC LIMIT ?) ORDER BY id",
                        (HISTORY,)).fetchall()
    talk = []
    for r in rows:
        m = assistant._row(r)
        if m["created_at"] < (st["started_at"] or "")[:16]:
            continue
        who = "관리자" if m["role"] == "user" else PROFILE["name"] if m["meta"].get("agent") == KEY else None
        if who:
            talk.append(f"{who}: {m['text'][:300]}")
    if talk:
        lines.append("이번 상담 대화:\n" + "\n".join(talk))
    lines.append(f"\n관리자의 새 답:\n{new}" if new is not None else "\n상담을 시작합니다. 짧게 인사하고 첫 질문을 하세요.")
    return "\n\n".join(lines)


def _tools(conn: sqlite3.Connection, s: agents.Session, done: dict) -> list[ToolDef]:
    async def record_answer(a: dict) -> dict:
        field = str(a.get("field") or "").strip()
        try:
            value = policy.clean(field, a.get("value"))
        except policy.PolicyError as e:
            return {"ok": False, "error": str(e)}
        st = _get(conn)
        draft = dict(st.get("draft") or {})
        draft[field] = value
        _put(conn, {**st, "draft": draft})
        return {"ok": True, "field": field, "value": value, "shown": policy.show(field, value),
                "remaining": remaining(state(conn))}

    async def finish(a: dict) -> dict:
        done["summary"] = str(a.get("summary") or "").strip()[:600]
        return {"ok": True, "note": "상담을 마쳐요 — reply 에 정리와 '비서에게 넘길게요'를 쓰세요"}

    fields = {"type": "string", "enum": list(policy.FIELDS)}
    own = [ToolDef("record_answer", "관리자의 답을 운용 목표 항목에 기록한다(형식은 시스템 프롬프트). 틀리면 error.",
                   {"type": "object", "properties": {"field": fields, "value": {}, "note": {"type": "string"}},
                    "required": ["field", "value"]}, record_answer),
           ToolDef("finish", "상담을 마친다 — 비서가 이 초안으로 운용 목표를 고치고 회의·안건을 맞춘다.",
                   {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}, finish)]
    return own + agents.build_tools(s, ["get_portfolio"], KEY)


def _llm_turn(conn: sqlite3.Connection, st: dict, new: str | None) -> tuple[dict, dict]:
    s = agents.Session(conn=conn, scope={"scope": "advice", "label": "자문", "candidates": []}, run_id=None, provider=market.provider())
    s.titles[KEY] = PROFILE["title"]
    done: dict = {}
    model = config.agent_options(conn)["model"] or None
    raw = asyncio.run(LLM("invest_assistant", SYSTEM, _context(conn, st, new), REPLY_SCHEMA, _tools(conn, s, done), model, LLM_TURNS))
    reply = str((raw or {}).get("reply") or "").strip()
    if not reply:
        raise ValueError("빈 답")
    choices = [str(c).strip()[:40] for c in (raw or {}).get("choices") or [] if str(c).strip()]
    return {"reply": reply, "choices": choices}, done


def opening(conn: sqlite3.Connection) -> dict | None:
    """상담을 열고 아직 묻지 않았으면 첫 질문 (LLM, 실패하면 코드)."""
    st = state(conn)
    if not st["open"] or not st["waiting"]:
        return None
    _put(conn, {**_get(conn), "waiting": False})
    try:
        out, _done = _llm_turn(conn, st, None)
    except (LLMError, TypeError, ValueError) as e:
        log.info("자문 에이전트 첫 질문을 코드로: %s", e)
        return _ask_next(conn, state(conn), f"안녕하세요, 자문 에이전트 {PROFILE['name']}예요. 운용 목표를 같이 정해 볼게요 — "
                         f"{len(st['topics'])}가지만 여쭤볼게요.")
    return _say(conn, out["reply"], out["choices"], by="assistant")


def turn(conn: sqlite3.Connection, msg_id: int) -> dict:
    """관리자 답 하나에 대한 자문 에이전트의 다음 말. 마치면 비서에게 넘긴다."""
    st = state(conn)
    text = conn.execute("SELECT text FROM invest_assistant_messages WHERE id = ?", (msg_id,)).fetchone()["text"]
    st["turns"] += 1
    _put(conn, {**_get(conn), "turns": st["turns"], "waiting": False})
    if any(w in text for w in STOP_WORDS):
        return _finish(conn, state(conn), "", by="user")
    try:
        out, done = _llm_turn(conn, st, text)
    except (LLMError, TypeError, ValueError) as e:
        log.info("자문 에이전트 답을 코드로: %s", e)
        return _code_turn(conn, state(conn), text)
    msg = _say(conn, out["reply"], [] if "summary" in done else out["choices"], by="assistant")
    if "summary" in done or st["turns"] >= MAX_TURNS:
        _finish(conn, state(conn), done.get("summary", ""), by=KEY, said=True)
    return msg


def _code_turn(conn: sqlite3.Connection, st: dict, text: str) -> dict:
    field = st["asking"]
    if field:
        cur = policy.get(conn).get(field)
        if "그대로" in text and cur is not None:
            value = cur
        elif policy.FIELDS[field]["type"] == "list" and any(w in text for w in NONE_WORDS):
            value = []
        else:
            value = policy.parse(field, text)
        if value is None:
            q, choices = _question(field, cur)
            return _say(conn, f"제가 잘 못 알아들었어요. {q}", choices, by="code")
        draft = {**st["draft"], field: value}
        _put(conn, {**_get(conn), "draft": draft})
        st = state(conn)
    if st["turns"] >= MAX_TURNS:
        return _finish(conn, st, "", by=KEY)
    return _ask_next(conn, st, "알겠어요.")


def _summary(draft: dict) -> str:
    return "\n".join(f"- {policy.FIELDS[k]['label']}: {policy.show(k, v) if v != [] else '없음'}" for k, v in draft.items()) or "기록한 답이 없어요"


def _finish(conn: sqlite3.Connection, st: dict, summary: str, *, by: str, said: bool = False) -> dict:
    """상담을 닫고 비서에게 넘긴다(기록한 답이 없으면 그냥 닫음)."""
    _put(conn, {**_get(conn), "open": False, "waiting": False, "asking": None})
    draft = st["draft"]
    msg = None
    if not said:
        msg = _say(conn, (f"상담을 마칠게요. 정리하면:\n{_summary(draft)}\n비서에게 넘길게요." if draft
                          else "상담을 마칠게요. 기록한 답이 없어서 운용 목표는 그대로예요."), [], by="code")
    if draft:
        assistant.apply_advice(conn, draft, summary or _summary(draft))
    return msg or {"closed": True}


def stop(conn: sqlite3.Connection) -> dict:
    """관리자가 '상담 끝내기' — 그때까지 기록한 답으로 마친다."""
    st = state(conn)
    if not st["open"]:
        raise AdviceError("상담 중이 아니에요", 409)
    return _finish(conn, st, "", by="user")
