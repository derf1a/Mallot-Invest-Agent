"""투자 비서 — AI Trading 채팅방에서 관리자(사용자)가 하는 말에 답하는 에이전트. 토론이 진행 중이 아닐 때 입력창의 말은 비서에게 간다.

비서가 하는 일 (도구로):
1) 답하기 — 시세·내 투자현황·뉴스·시장·한도(분석 에이전트와 같은 읽기 도구)와 오늘 판단 일정·최근 판단으로 확인해 짧게 답한다.
   전문 의견이 필요하면 ask_agent 로 분석 에이전트(차티스트 등)에게 묻는다 — 그 에이전트가 자기 성향·도구로 답하고, 그 답은
   채팅방에 그 에이전트의 말풍선으로 남는다.
2) 운용 지시 정리 — 앞으로의 매매 방향(섹터 주목·공격적/보수적·종목 비중 등)을 운용 지시 목록(autopilot.intents)에 더하거나
   고치거나 지워 짧고 모순 없이 유지한다. 정기 판단(자동 운용 일배치)이 이 목록을 쓴다. 다음 판단에만 쓸 지시는 once(이번만).
3) 즉시 지시 — '지금·바로' 사고팔거나 판단하라는 말이면 start_now 로 바로 판단을 시작한다(autopilot.start_now: 자동 운용이 켜져
   있으면 과반 게이트·승인 없이, 꺼져 있으면 만장일치·관리자 승인). 그 지시는 토론의 첫 관리자 메시지가 된다.
4) 보고 — 보고·리포트를 부탁하면 make_report 로 한 페이지 보고서(briefing.snapshot: 보유 종목·현황·회의 요약·장내 이슈 — 관리자가
   계속 볼 것만, 숫자는 모두 코드)를 만들고 비서는 짧은 의견(comment)만 붙인다. 채팅방에 보고서 카드로 남고 이미지로 저장할 수 있다.
5) 설정 — 말로 바꾸는 운영 설정: 회의 시각·하루 회의 수·AI 추가 회의 수·자동 운용 켜기/끄기(update_schedule → autopilot),
   토론 라운드·후보 수·모델(update_debate → config.agent_options), 보고(아침 브리핑) 시각·요일·매월 날짜·휴장일(update_briefing →
   morning), 에이전트 참여(set_agent → 명단), 운용 수익률의 기준 원금(set_baseline → briefing: 다시 정하기·추가 입금·출금). 검사는 각 모듈 그대로, 바뀔 때마다 '설정 변경: 전 → 후' 줄을 남긴다. 주문 한도·승인
   생략은 실제 돈에 바로 닿아 채팅으로 바꾸지 않는다(설정 > 투자).
6) 리서치 — 운용 데스크(시장 전략가·섹터 애널리스트·종목 발굴 애널리스트)의 오늘 결과를 get_research 로 전하고, 다시 보라는 말이면
   run_research 로 지금 리서치를 시작한다(desk.start_research — 주문과 무관).
7) 운용 목표 — 운용 목표(policy: 목표 방식·수익률·벤치마크·기간·매매 빈도·위험·손실 한도·주식 비중 등)를 보고(get_policy) 고친다
   (update_policy). 목표를 정하거나 다시 보자는 말이면 자문 에이전트 상담을 연다(start_advice — 그 뒤 입력은 자문 에이전트에게).
   상담이 끝나면 자문 에이전트가 정리한 답이 넘어오고(apply_advice), 비서가 운용 목표에 반영한 뒤 회의 시각·안건·운용 지시를 맞춘다.
8) 회의 일정 — 오늘 회의를 안건과 꼭 볼 종목을 붙여 더하거나(schedule_meeting, 하루 autopilot.MAX_ASSISTANT 번) 취소한다
   (cancel_meeting). 정기 시각은 update_schedule.
비서는 주문 도구가 없다 — 주문은 언제나 토론 → 게이트 → 트레이더 → 코드 검사를 거친다.

기록: invest_assistant_messages (user·assistant·agent·event). 자문 상담 중이면 관리자 말은 자문 에이전트(advisor.turn)가 받는다. 답은 백그라운드 스레드가 한 번에 한 메시지씩 만든다(채팅방이 1~2초마다
이어 받고, 만드는 중이면 '입력 중'). 테스트는 SYNC = True 로 바로 만든다.
"""

import asyncio
import json
import logging
import sqlite3
import threading
from collections.abc import Awaitable, Callable

from app.core.db import connect
from app.invest import agents, autopilot, briefing, desk, market, policy, scopes
from app.invest.broker import config
from app.invest.market import MarketError
from app.invest.broker.toss import TossError
from app.llm.client import LLMError, ToolDef, agentic
from app.system import issues

log = logging.getLogger("uvicorn.error")

ROLES = ("user", "assistant", "agent", "event")
TITLE = "비서"
KEY = "assistant"
PROFILE = {"key": KEY, "title": TITLE, "name": "서윤", "full_name": "박서윤 (Park Seo-yun)", "country": "한국",
           "pokemon": 113}  # 프로필 그림 럭키(돕는 역할)
MARKET_TOOLS = ("get_quote", "get_technicals", "get_valuation", "search_news", "get_market_overview", "get_portfolio", "get_trading_limits")
HISTORY = 12      # 프롬프트에 넣는 최근 대화 수
MAX_TURNS = 10
AGENT_TURNS = 8
SYNC = False      # 테스트: 답을 같은 스레드에서 바로 만든다

_lock = threading.Lock()       # 비서는 한 번에 한 메시지씩 (들어온 순서대로)
_pending = 0
_pending_lock = threading.Lock()

REPLY_SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]}
ANSWER_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}

SYSTEM = f"""당신은 Mallot 의 투자 비서 {PROFILE['name']}({PROFILE['full_name']})입니다. 관리자(사용자)가 AI Trading 채팅방에서 하는 말에 답합니다.
분석 에이전트들은 다국적 팀입니다 — 관리자에게 전할 때는 '이름(직함)'으로 부르세요(예: 켄지(차티스트)).
할 일
1) 답하기: 질문이면 도구(현재가·기술 지표·뉴스·시장·내 투자현황·주문 한도, 오늘 판단 일정·최근 판단)로 확인해 답하세요.
   종목·전략에 대한 전문 의견이 필요하면 ask_agent 로 분석 에이전트에게 물어 그 답을 요약해 전하세요(한 메시지에 1~2명까지).
2) 운용 지시 정리: 앞으로의 매매 방향(섹터 주목, 공격적·보수적, 특정 종목을 늘리거나 줄이기, 피할 종목 등)이면 list_intents 로
   지금 목록을 보고 add_intent·update_intent·remove_intent 로 정리하세요. 목록은 짧고 서로 모순 없게 — 같은 주제는 하나로 합치고,
   새 말이 옛 지시와 부딪히면 새 말을 따르세요. 다음 판단 한 번에만 쓸 말이면 once=true. 정기 판단(매일 정해진 시각의 자동 판단)이
   이 목록으로 후보 종목과 성향을 정합니다. 종목·섹터는 정식 이름으로 적으세요(예: 하이닉스 → SK하이닉스).
3) 즉시 지시: '지금', '바로' 사거나 팔거나 판단하라는 말이면 start_now 로 바로 판단을 시작하세요(instruction 에 그 지시를 그대로).
   장 시간이 아니거나 막히면 이유를 알리고, 원하면 다음 판단에 반영되도록 once 지시로 남기세요.
4) 보고: 보고·리포트·정리해 달라고 하면 make_report 로 한 페이지 보고서를 만드세요. 숫자는 코드가 채우니 comment 에는 도구 결과를 보고
   2~3문장 의견(보유 종목에서 가장 눈여겨볼 점, 최근 회의 결론, 보유 종목에 닿는 장내 이슈 — 설정·일정 이야기는 빼고)만 쓰고,
   reply 는 '보고서를 만들었어요'처럼 짧게.
5) 설정 변경: 회의를 언제·하루 몇 번 할지, AI 가 더할 회의 수, 자동 운용 켜기/끄기(update_schedule), 토론 라운드·후보 종목 수·모델
   (update_debate), 보고(아침 브리핑) 시각·요일·매월 날짜·휴장일에도 보낼지(update_briefing), 에이전트를 회의에서 빼거나 넣기(set_agent)를
   말로 바꿀 수 있습니다. 먼저 get_settings 로 지금 값을 보고 바꾸세요. '하루 N번'이면 자동 판단 시간(보통 09:05~15:15) 안에서 장 시작
   직후·장 중·마감 전을 살려 고르게 시각을 정하세요. 요일은 0=월 … 6=일. 자동 운용 켜기는 관리자가 분명히 켜 달라고 할 때만 하세요.
   주문 한도·승인 생략은 바꿀 수 없으니 설정 > 투자를 안내하세요. 바꾼 뒤 reply 에 전후를 짧게 밝히세요.
   운용 수익률은 기준 원금(처음 넣은 돈 + 추가 입금 − 출금) 대비 토스 계좌 총액입니다. '돈을 더 넣었다·뺐다'면 set_baseline mode=add 로
   (뺀 돈은 음수), '기준을 ○○원으로 다시'면 mode=reset 으로 남기세요.
   '회의는 필요할 때만 / 정해진 시각마다 꼭'은 update_schedule 의 flexible(true = 새 안건이 있을 때만).
   운용 방식은 update_schedule 의 mode — continuous(상시 운용: 정기 회의 없이 장 내내 에이전트들이 대화하고 트레이더가 언제든 주문, 기본) /
   meetings(정해 둔 시각에 회의). 상시 운용 중에는 start_now·schedule_meeting 이 채널에 관리자 말·안건으로 바로 들어갑니다.
6) 리서치: 운용 데스크는 시장 전략가 {desk.PEOPLE['strategist']['name']}·섹터 애널리스트 {desk.PEOPLE['sector']['name']}·종목 발굴 애널리스트
   {desk.PEOPLE['scout']['name']}입니다(장 전에 시장 분석 → 섹터 탐색 → 종목 발굴, 회의는 그 발굴 목록과 보유로 안건을 정함). 시장·섹터·
   발굴 종목을 물으면 get_research 로 오늘 결과를 전하고, 다시 분석하라거나 특정 섹터를 찾아보라면 run_research(note 에 그 요청)로 시작하세요.
7) 운용 목표: 모든 에이전트가 판단 기준으로 쓰는 투자 정책입니다(get_policy). 관리자가 목표를 정하자·바꾸자·물어봐 달라고 하거나 운용 목표가
   비어 있는데 투자 방향을 이야기하면 start_advice 로 자문 에이전트 {desk.ADVISOR['name']} 상담을 여세요(그다음부터 관리자 말은 자문 에이전트가 받음).
   관리자가 값을 분명히 말하면(예: "목표 수익률 연 12%로") update_policy 로 바로 고쳐도 됩니다.
   자문 상담 결과가 넘어오면: 답을 운용 목표에 반영하고(update_policy — 서로 안 맞는 값은 관리자 뜻을 살려 조정하고 이유를 reason 에),
   목표에 맞게 회의를 조정하세요 — 매매 빈도·투자 기간에 맞춰 정기 회의 시각·수(update_schedule), 오늘 필요한 회의와 안건
   (schedule_meeting: 예 '주식 비중 30% → 목표 70%까지 단계적 매수 계획'), 필요 없는 회의 취소(cancel_meeting), 목표와 부딪히는 운용 지시
   정리(update_intent·remove_intent). 그리고 reply 에 무엇을 왜 바꿨는지 짧게 알리세요.
8) 회의 일정·안건: 당신은 회의를 자유롭게 정할 수 있습니다 — 관리자 말이나 상황(목표와 지금 비중 차이, 시장 급변, 보류한 주문)에 따라
   오늘 회의를 안건·꼭 볼 종목과 함께 더하거나 취소하세요. 다만 회의마다 비용이 들고 잦은 매매는 실수를 늘리니 이유가 있을 때만.
9) 시스템 문제: 도구 오류·데이터 불일치·기록 이상·앱 동작처럼 개발에 관한 일은 시스템 관리자 {issues.PROFILE['name']}가 맡습니다. 관리자에게 확인을 요청하지 말고, 보고서 comment 와
   아침 의견에도 쓰지 마세요. 새로 발견하면 report_system_issue 로 넘기고, 관리자가 직접 물으면 get_system_issues 로 처리 상황을 전하세요.
규칙
- 당신은 주문하지 않습니다. 주문은 언제나 에이전트 토론 → 게이트 → 트레이더 → 코드의 한도 검사를 거칩니다.
- 도구 결과에 없는 숫자·사실을 지어내지 마세요. 수익을 장담하지 마세요.
- 한 일(지시 추가·수정·삭제, 판단 시작, 누구에게 물었는지)을 reply 에 짧게 밝히세요.
- 한국어, 카카오톡처럼 짧고 친근하게(보통 2~5문장). 응답은 JSON 스키마({{"reply": ...}})를 따르세요."""

AgentLlm = Callable[..., Awaitable[dict]]


async def _default_llm(feature: str, system: str, prompt: str, schema: dict, tools: list[ToolDef],
                       model: str | None, max_turns: int) -> dict:
    return await agentic(feature, prompt, schema, tools=tools, model=model, system_prompt=system, max_turns=max_turns)


LLM: AgentLlm = _default_llm  # 테스트에서 가짜로 바꾼다


# ---- 기록 ----
def _row(r: sqlite3.Row) -> dict:
    try:
        meta = json.loads(r["meta"])
    except ValueError:
        meta = {}
    return {"id": r["id"], "created_at": r["created_at"], "role": r["role"], "text": r["text"], "meta": meta}


def add(conn: sqlite3.Connection, role: str, text: str, **meta) -> dict:
    cur = conn.execute("INSERT INTO invest_assistant_messages (role, text, meta) VALUES (?, ?, ?)",
                       (role, text, json.dumps(meta, ensure_ascii=False, default=str)))
    conn.commit()
    return _row(conn.execute("SELECT * FROM invest_assistant_messages WHERE id = ?", (cur.lastrowid,)).fetchone())


def messages(conn: sqlite3.Connection, after: int = 0, limit: int = 300) -> list[dict]:
    rows = conn.execute("SELECT * FROM (SELECT * FROM invest_assistant_messages WHERE id > ? ORDER BY id DESC LIMIT ?) "
                        "ORDER BY id", (after, limit)).fetchall()
    return [_row(r) for r in rows]


def pending() -> bool:
    return _pending > 0


def post(conn: sqlite3.Connection, text: str) -> dict:
    """관리자 말을 남기고 비서 답을 만든다(백그라운드). → 남긴 메시지"""
    global _pending
    msg = add(conn, "user", text.strip()[:1000])
    with _pending_lock:
        _pending += 1
    if SYNC:
        _process(msg["id"], conn)
    else:
        threading.Thread(target=_process, args=(msg["id"],), name="assistant", daemon=True).start()
    return msg


def background(task: Callable[[sqlite3.Connection], object], conn: sqlite3.Connection | None = None) -> None:
    """비서 대화 스레드에서 한 가지 일(자문 상담 첫 질문·상담 끝내기 등)을 한다 — 하는 동안 '입력 중', 대화 순서를 지키려고 같은 잠금."""
    global _pending
    with _pending_lock:
        _pending += 1

    def run(c: sqlite3.Connection | None) -> None:
        global _pending
        own = c is None
        c = c or connect()
        try:
            with _lock:
                task(c)
        except Exception:  # noqa: BLE001
            log.exception("비서 대화 작업 중 오류")
        finally:
            with _pending_lock:
                _pending -= 1
            if own:
                c.close()

    if SYNC:
        run(conn)
    else:
        threading.Thread(target=run, args=(None,), name="assistant", daemon=True).start()


def _process(msg_id: int, conn: sqlite3.Connection | None = None) -> None:
    global _pending
    own = conn is None
    conn = conn or connect()
    try:
        with _lock:
            from app.invest import advisor  # advisor 가 이 모듈(add·apply_advice)을 불러서 늦게
            if advisor.state(conn)["open"]:  # 자문 상담 중 — 관리자 말은 자문 에이전트에게
                try:
                    advisor.turn(conn, msg_id)
                except Exception as e:  # noqa: BLE001
                    log.exception("자문 에이전트 응답 중 오류")
                    add(conn, "agent", "죄송해요, 지금은 답을 만들지 못했어요 — 다시 말해 주세요.", agent=advisor.KEY,
                        title=advisor.PROFILE["title"], name=advisor.PROFILE["name"], pokemon=advisor.PROFILE["pokemon"], error=str(e)[:300])
                return
            try:
                reply, used = asyncio.run(respond(conn, msg_id))
                add(conn, "assistant", reply, tools=used)
            except Exception as e:  # noqa: BLE001 — 어떤 오류든 채팅방에 답이 없는 채로 두지 않는다
                if not isinstance(e, (LLMError, TypeError, ValueError)):
                    log.exception("투자 비서 응답 중 오류")
                add(conn, "assistant", "죄송해요, 지금은 답을 만들지 못했어요 — 잠시 뒤 다시 말해 주세요.", error=str(e)[:300])
            advisor.opening(conn)  # 비서가 방금 상담을 열었으면 자문 에이전트의 첫 질문
    finally:
        with _pending_lock:
            _pending -= 1
        if own:
            conn.close()


def apply_advice(conn: sqlite3.Connection, draft: dict, summary: str) -> None:
    """자문 상담 결과를 비서에게 넘긴다 — 비서가 운용 목표에 반영하고 회의·안건·운용 지시를 맞춘 뒤 관리자에게 알린다.
    비서가 실패하거나 운용 목표를 고치지 않으면 코드가 상담 답을 그대로 반영한다(관리자 답이 사라지지 않게)."""
    lines = "\n".join(f"- {policy.FIELDS[k]['label']}({k}): {policy.show(k, v) if v != [] else '없음'}" for k, v in draft.items())
    ev = add(conn, "event", "자문 상담 결과를 비서에게 넘겼어요", kind="advice_done", draft=draft)
    message = f"자문 에이전트가 관리자와 상담해 정리한 운용 목표 답:\n{lines}\n\n요약: {summary}"
    used: list[str] = []
    try:
        reply, used = asyncio.run(respond(conn, ev["id"], header="자문 상담 결과 (운용 목표에 반영하고 회의·안건을 맞추세요)", message=message))
        add(conn, "assistant", reply, tools=used)
    except Exception as e:  # noqa: BLE001 — 비서가 실패해도 관리자 답은 반영한다
        if not isinstance(e, (LLMError, TypeError, ValueError)):
            log.exception("자문 결과 반영 중 오류")
    if "update_policy" not in used:
        try:
            out = policy.save(conn, {k: (v if v != [] else None) for k, v in draft.items()}, by="advisor", reason="자문 상담 답 그대로")
        except policy.PolicyError as e:
            add(conn, "event", f"운용 목표를 반영하지 못했어요: {e}", kind="setting", what="운용 목표")
            return
        for d in out["diff"]:
            add(conn, "event", f"설정 변경 · 운용 목표 {d['label']}: {d['before']} → {d['after']}", kind="setting", what=d["label"])


# ---- 프롬프트 ----
def _context(conn: sqlite3.Connection, msg_id: int, header: str = "관리자의 새 메시지", message: str | None = None) -> str:
    now = market.now_kst()
    st = autopilot.status(conn, now)
    cfg = st["config"]
    lines = [f"지금 {now.strftime('%Y-%m-%d %H:%M')} (한국 시각)"]
    lines.append(f"자동 운용: {'켜짐' if cfg['enabled'] else '꺼짐'} · 고정 판단 {' · '.join(cfg['slots'])}"
                 + (f" · 다음 판단 {st['next']['time']}" if st["next"] else "")
                 + (f" · 자동 판단 시간 {st['window'][0]}~{st['window'][1]}" if st["window"] else f" · {st['window_note'] or ''}")
                 + (f" · 주의: {st['blocked']}" if st["blocked"] else ""))
    ints = st["intents"]
    lines.append("운용 지시: " + ("없음" if not ints else "\n" + "\n".join(
        f"- [{i['id']}] {i['text']}{' (이번만)' if i.get('once') else ''}" for i in ints)))
    runs = conn.execute("SELECT id, name, status, summary_json FROM runs WHERE kind = 'agent_action' ORDER BY id DESC LIMIT 3").fetchall()
    if runs:
        out = []
        for r in runs:
            try:
                s = json.loads(r["summary_json"] or "{}")
            except ValueError:
                s = {}
            orders = ", ".join(f"{o['name']} {o['side']} {o['quantity']:g}주 {o['status']}" for o in s.get("orders") or []) or "주문 없음"
            out.append(f"- #{r['id']} {r['name']} · {r['status']} · 게이트 {s.get('gate', '-')} · {orders}")
        lines.append("최근 판단:\n" + "\n".join(out))
    research = desk.brief(conn, now.date())
    lines.append("오늘 리서치: " + (f"\n{research}" if research else "아직 없음"))
    pol = policy.get(conn)
    lines.append("운용 목표: " + (f"\n{policy.text(pol)}" if policy.text(pol) else "아직 없음 — 자문 상담(start_advice)을 권하세요")
                 + (f"\n(아직 안 정한 항목: {', '.join(policy.FIELDS[k]['label'] for k in policy.missing(pol))})" if policy.missing(pol) else ""))
    agendas = [f"{e['time']} {e.get('agenda') or e.get('reason') or ''}".strip() for e in st["today"]["entries"]
               if e["status"] == "pending" and (e.get("agenda") or e.get("reason"))]
    if agendas:
        lines.append("오늘 안건이 정해진 회의: " + " · ".join(agendas))
    system = issues.brief(conn)
    if system:
        lines.append(system.rstrip())
    roster = [a for a in agents.get_roster(conn) if a["enabled"] and not a["executor"]]
    lines.append("물어볼 수 있는 분석 에이전트: " + ", ".join(f"{a['name']}({a['title']}, key={a['key']})" for a in roster))
    history = conn.execute("SELECT * FROM (SELECT * FROM invest_assistant_messages WHERE id < ? ORDER BY id DESC LIMIT ?) ORDER BY id",
                           (msg_id, HISTORY)).fetchall()
    if history:
        def who(r: dict) -> str:
            return ({"user": "나", "assistant": f"{PROFILE['name']}({TITLE})", "event": "기록"}.get(r["role"])
                    or f"{r['meta'].get('name', '')}({r['meta'].get('title', '에이전트')})")
        lines.append("최근 대화:\n" + "\n".join(f"{who(r)}: {r['text'][:300]}" for r in map(_row, history)))
    text = message if message is not None else conn.execute(
        "SELECT text FROM invest_assistant_messages WHERE id = ?", (msg_id,)).fetchone()["text"]
    return "\n".join(lines) + f"\n\n{header}:\n{text}"


# ---- 도구 ----
def _tool(name: str, desc: str, props: dict, required: list[str], fn) -> ToolDef:
    return ToolDef(name, desc, {"type": "object", "properties": props, "required": required}, fn,
                   read_only=name in ("list_intents", "get_schedule", "get_settings", "ask_agent", "make_report", "get_research",
                                      "get_policy"))


def build_tools(conn: sqlite3.Connection, s: agents.Session, used: list[str], model: str | None) -> list[ToolDef]:
    def note(name: str, ok: bool = True) -> None:
        used.append(name if ok else f"{name}(실패)")

    async def list_intents(_: dict) -> dict:
        note("list_intents")
        return {"intents": autopilot.intents(conn)}

    async def add_intent(a: dict) -> dict:
        try:
            items = autopilot.add_intent(conn, str(a.get("text") or ""), once=bool(a.get("once")))
        except autopilot.AutopilotError as e:
            note("add_intent", False)
            return {"ok": False, "error": str(e)}
        new = items[-1]
        note("add_intent")
        add(conn, "event", f"운용 지시 추가{' (이번만)' if new.get('once') else ''}: {new['text']}", kind="intent_added", intent_id=new["id"])
        return {"ok": True, "intent": new}

    async def update_intent(a: dict) -> dict:
        try:
            iid = int(a.get("id"))
            items = autopilot.update_intent(conn, iid, str(a.get("text") or ""),
                                            once=a.get("once") if isinstance(a.get("once"), bool) else None)
        except (autopilot.AutopilotError, TypeError, ValueError) as e:
            note("update_intent", False)
            return {"ok": False, "error": str(e)}
        hit = next(i for i in items if i["id"] == iid)
        note("update_intent")
        add(conn, "event", f"운용 지시 고침: {hit['text']}", kind="intent_updated", intent_id=iid)
        return {"ok": True, "intent": hit}

    async def remove_intent(a: dict) -> dict:
        try:
            iid = int(a.get("id"))
            gone = next((i for i in autopilot.intents(conn) if i["id"] == iid), None)
            autopilot.delete_intent(conn, iid)
        except (autopilot.AutopilotError, TypeError, ValueError) as e:
            note("remove_intent", False)
            return {"ok": False, "error": str(e)}
        note("remove_intent")
        add(conn, "event", f"운용 지시 지움: {gone['text'] if gone else iid}", kind="intent_removed", intent_id=iid)
        return {"ok": True}

    async def get_schedule(_: dict) -> dict:
        note("get_schedule")
        st = autopilot.status(conn)
        return {k: st[k] for k in ("config", "today", "window", "window_note", "next", "blocked")}

    async def start_now(a: dict) -> dict:
        instruction = str(a.get("instruction") or "").strip()
        if not instruction:
            return {"ok": False, "error": "instruction 이 비었습니다"}
        try:
            out = autopilot.start_now(conn, instruction)
        except autopilot.AutopilotError as e:
            note("start_now", False)
            return {"ok": False, "error": str(e)}
        note("start_now")
        mode = "자동 운용 판단(과반 게이트·승인 없이 주문)" if out["autopilot"] else "토론(만장일치·주문은 관리자 승인)"
        add(conn, "event", f"지금 판단을 시작했어요 — {mode}", kind="session_started", run_id=out["run_id"], autopilot=out["autopilot"])
        return {"ok": True, **out, "mode": mode}

    async def ask_agent(a: dict) -> dict:
        who = str(a.get("agent") or "").strip()
        question = str(a.get("question") or "").strip()
        roster = [x for x in agents.get_roster(conn) if x["enabled"] and not x["executor"]]
        agent = next((x for x in roster if who in (x["key"], x["title"], x["name"])), None)
        if agent is None or not question:
            note("ask_agent", False)
            return {"ok": False, "error": "분석 에이전트(key·직함·이름)와 question 이 필요합니다 — "
                                          + ", ".join(f"{x['name']} {x['title']}({x['key']})" for x in roster)}
        sub = agents.Session(conn=conn, scope=s.scope, run_id=None, provider=s.provider)
        sub.titles[agent["key"]] = agent["title"]
        prompt = (f"관리자가 비서를 통해 묻습니다:\n{question}\n\n당신의 성향과 도구로 필요한 자료를 확인한 뒤 answer 에 짧게(3~6문장) "
                  "답하세요. 토론이 아니므로 views 는 내지 않습니다. 도구 결과에 없는 숫자는 쓰지 마세요.")
        try:
            raw = await LLM("invest_assistant", agents.system_prompt(agent, sub), prompt, ANSWER_SCHEMA,
                            agents.build_tools(sub, agent["tools"], agent["key"]), model, AGENT_TURNS)
        except LLMError as e:
            note(f"ask_agent:{agent['key']}", False)
            return {"ok": False, "error": f"{agent['title']} 응답 실패: {e}"}
        answer = str((raw or {}).get("answer") or "").strip() or "(답이 비었어요)"
        note(f"ask_agent:{agent['key']}")
        add(conn, "agent", answer, agent=agent["key"], title=agent["title"], name=agent["name"],
            tools=[t["tool"] for t in sub.tool_log])
        return {"ok": True, "agent": f"{agent['name']}({agent['title']})", "answer": answer}

    async def report_system_issue(a: dict) -> dict:
        text = str(a.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "text 가 필요합니다"}
        issues.report(conn, text, PROFILE["title"], "assistant")
        note("report_system_issue")
        return {"ok": True, "note": f"시스템 관리자 {issues.PROFILE['name']}에게 넘겼어요"}

    async def get_system_issues(_: dict) -> dict:
        note("get_system_issues")
        return {"counts": issues.counts(conn), "open": [{k: x[k] for k in ("number", "title", "reports", "created_at", "url")} for x in issues.listing(conn, "open", 20)],
                "resolved": [{k: x[k] for k in ("number", "title", "resolution", "resolved_at")} for x in issues.listing(conn, "resolved", 10) if x["by"] != "GitHub"]}

    async def make_report(a: dict) -> dict:
        data = briefing.snapshot(conn, s.provider)
        data["comment"] = issues.strip_notes(str(a.get("comment") or ""))[:600]
        note("make_report")
        add(conn, "event", "한 페이지 보고서를 만들었어요", kind="report", report=data, body=briefing.to_text(data))
        pf = data["performance"]["portfolio"]
        return {"ok": True, "warnings": data["warnings"], "holdings": data["holdings"],
                "portfolio": {k: pf[k] for k in ("total_assets_krw", "cash_ratio", "pnl_krw", "return_rate")} if pf
                else data["performance"]["portfolio_note"], "meetings": data["meetings"], "market": data["market"]}

    def changed(what: str, before: object, after: object) -> None:
        if before != after:
            add(conn, "event", f"설정 변경 · {what}: {before} → {after}", kind="setting", what=what)

    async def get_settings(_: dict) -> dict:
        from app.invest import morning  # morning 이 이 모듈(add·PROFILE)을 불러서 늦게
        note("get_settings")
        st = autopilot.status(conn)
        return {"autopilot": st["config"], "auto_judgment_window": st["window"] or ["09:05", "15:15"],
                "debate": config.agent_options(conn), "model_hint": "비우면 기본 LLM, claude-… 또는 local",
                "briefing": {k: v for k, v in morning.get_config(conn).items() if k != "last"},
                "weekday_codes": "0=월 1=화 2=수 3=목 4=금 5=토 6=일",
                "agents": [{"key": a["key"], "name": a["name"], "title": a["title"], "enabled": a["enabled"], "executor": a["executor"]}
                           for a in agents.get_roster(conn)],
                "baseline": briefing.get_baseline(conn),
                "not_by_chat": "주문 한도·승인 생략·전체 주문 중지는 설정 > 투자"}

    async def update_schedule(a: dict) -> dict:
        before = autopilot.get_config(conn)
        enabled = a.get("enabled") if isinstance(a.get("enabled"), bool) else None
        if enabled and not config.toss_configured():
            return {"ok": False, "error": "토스증권 API 키가 없어 자동 운용을 켤 수 없어요 — 설정 > 투자에서 입력하세요"}
        try:
            slots = [str(x).strip() for x in a["slots"]] if isinstance(a.get("slots"), list) else None
            extra = int(a["max_extra"]) if a.get("max_extra") is not None else None
            flexible = a.get("flexible") if isinstance(a.get("flexible"), bool) else None
            mode = str(a["mode"]) if a.get("mode") else None
            after = autopilot.save_config(conn, enabled=enabled, slots=slots, max_extra=extra, flexible=flexible, mode=mode)
        except (autopilot.AutopilotError, TypeError, ValueError) as e:
            note("update_schedule", False)
            return {"ok": False, "error": str(e)}
        note("update_schedule")
        changed("자동 운용", "켜짐" if before["enabled"] else "꺼짐", "켜짐" if after["enabled"] else "꺼짐")
        changed(f"정기 회의(하루 {len(after['slots'])}번)", " · ".join(before["slots"]), " · ".join(after["slots"]))
        changed("AI 가 더할 회의(하루)", f"{before['max_extra']}번", f"{after['max_extra']}번")
        flex = lambda c: "안건이 있을 때만" if c["flexible"] else "정기 시각마다"  # noqa: E731
        changed("회의 방식", flex(before), flex(after))
        changed("운용 방식", autopilot.MODES[before["mode"]].split(" —")[0], autopilot.MODES[after["mode"]].split(" —")[0])
        return {"ok": True, "before": before, "after": after}

    async def get_research(_: dict) -> dict:
        note("get_research")
        st = desk.status(conn)
        return {k: st[k] for k in ("market", "sector", "ideas", "alerts", "running", "research")} | {
            "process": [{k: x[k] for k in ("title", "at", "text")} for x in st["process"]],
            "team": {k: f"{v['name']}({v['title']})" for k, v in desk.PEOPLE.items()}}

    async def run_research(a: dict) -> dict:
        try:
            run_id = desk.start_research(conn, "assistant", str(a.get("note") or "").strip()[:300])
        except desk.DeskError as e:
            note("run_research", False)
            return {"ok": False, "error": str(e)}
        note("run_research")
        add(conn, "event", "리서치를 시작했어요 — 시장 분석 → 섹터 탐색 → 종목 발굴", kind="research_started", run_id=run_id)
        return {"ok": True, "run_id": run_id, "note": "몇 분 안에 세 에이전트가 채팅방에 결과를 남겨요"}

    async def update_debate(a: dict) -> dict:
        before = config.agent_options(conn)
        opts = {k: a[k] for k in ("rounds", "max_candidates", "model") if a.get(k) is not None}
        try:
            config.save_agent_options(conn, opts)
        except config.ConfigError as e:
            note("update_debate", False)
            return {"ok": False, "error": str(e)}
        after = config.agent_options(conn)
        note("update_debate")
        changed("토론 라운드", before["rounds"], after["rounds"])
        changed("후보 종목 수", before["max_candidates"], after["max_candidates"])
        changed("모델", before["model"] or "기본 LLM", after["model"] or "기본 LLM")
        return {"ok": True, "before": before, "after": after}

    async def update_briefing(a: dict) -> dict:
        from app.invest import morning
        before = morning.get_config(conn)
        try:
            after = morning.save_config(
                conn, enabled=a.get("enabled") if isinstance(a.get("enabled"), bool) else None,
                time=str(a["time"]).strip() if a.get("time") else None,
                weekdays=[int(x) for x in a["weekdays"]] if isinstance(a.get("weekdays"), list) else None,
                month_days=[int(x) for x in a["month_days"]] if isinstance(a.get("month_days"), list) else None,
                skip_holidays=a.get("skip_holidays") if isinstance(a.get("skip_holidays"), bool) else None)
        except (morning.MorningError, TypeError, ValueError) as e:
            note("update_briefing", False)
            return {"ok": False, "error": str(e)}
        note("update_briefing")
        changed("보고(아침 브리핑)", ("" if before["enabled"] else "꺼짐 · ") + before["summary"],
                ("" if after["enabled"] else "꺼짐 · ") + after["summary"])
        return {"ok": True, "before": before, "after": after}

    async def set_agent(a: dict) -> dict:
        who = str(a.get("agent") or "").strip()
        roster = agents.get_roster(conn)
        hit = next((x for x in roster if who in (x["key"], x["title"], x["name"])), None)
        if hit is None or not isinstance(a.get("enabled"), bool):
            note("set_agent", False)
            return {"ok": False, "error": "에이전트(key·직함·이름)와 enabled(true/false)가 필요해요 — "
                                          + ", ".join(f"{x['name']} {x['title']}" for x in roster)}
        hit["enabled"] = a["enabled"]
        try:
            agents.save_roster(conn, roster)
        except agents.AgentError as e:
            note("set_agent", False)
            return {"ok": False, "error": str(e)}
        note("set_agent")
        changed(f"{hit['name']}({hit['title']}) 회의 참여", "빠짐" if a["enabled"] else "참여", "참여" if a["enabled"] else "빠짐")
        return {"ok": True, "agent": f"{hit['name']}({hit['title']})", "enabled": a["enabled"]}

    async def set_baseline(a: dict) -> dict:
        before = briefing.get_baseline(conn)
        try:
            amount = int(a.get("amount"))
            day = str(a.get("date") or "") or market.now_kst().date().isoformat()
            memo = str(a.get("note") or "")
            if a.get("mode") == "reset":
                after = briefing.set_baseline(conn, [{"date": day, "amount": amount, "note": memo or "기준 원금"}])
            else:
                after = briefing.add_contribution(conn, amount, day, memo or ("추가 입금" if amount > 0 else "출금"))
        except (briefing.BaselineError, TypeError, ValueError) as e:
            note("set_baseline", False)
            return {"ok": False, "error": str(e)}
        note("set_baseline")
        show = lambda b: f"{b['amount']:,}원({b['since'][5:]}부터)" if b["amount"] else "없음"  # noqa: E731
        changed("기준 원금", show(before), show(after))
        return {"ok": True, "before": before, "after": after}

    async def get_policy(_: dict) -> dict:
        note("get_policy")
        v = policy.view(conn)
        return {"policy": {k: policy.show(k, v["policy"][k]) for k in policy.FIELDS if k in v["policy"]},
                "missing": [policy.FIELDS[k]["label"] for k in v["missing"]], "history": v["history"],
                "formats": {k: (list(s["choices"]) if s["type"] == "enum" else s["type"]) for k, s in policy.FIELDS.items()}}

    async def update_policy(a: dict) -> dict:
        changes = a.get("changes") if isinstance(a.get("changes"), dict) else {}
        try:
            out = policy.save(conn, changes, by="assistant", reason=str(a.get("reason") or ""))
        except policy.PolicyError as e:
            note("update_policy", False)
            return {"ok": False, "error": str(e)}
        note("update_policy")
        for d in out["diff"]:
            changed(f"운용 목표 {d['label']}", d["before"], d["after"])
        return {"ok": True, "changed": [f"{d['label']}: {d['before']} → {d['after']}" for d in out["diff"]] or "바뀐 것 없음"}

    async def start_advice(a: dict) -> dict:
        from app.invest import advisor
        topics = [str(t) for t in a["topics"]] if isinstance(a.get("topics"), list) else None
        st = advisor.start(conn, by="assistant", topics=topics, note=str(a.get("note") or ""))
        note("start_advice")
        return {"ok": True, "topics": [policy.FIELDS[t]["label"] for t in st["topics"]],
                "note": f"이 답 뒤에 자문 에이전트 {advisor.PROFILE['name']}가 첫 질문을 해요 — reply 에서 상담을 시작한다고 알리세요"}

    def _window():
        try:
            return autopilot.trading_window(s.provider)
        except (MarketError, TossError):
            return None

    async def schedule_meeting(a: dict) -> dict:
        cfg = autopilot.get_config(conn)
        if cfg["enabled"] and cfg["mode"] == "continuous":  # 상시 운용: 회의 대신 채널에 안건을 바로 올린다
            syms = ", ".join(str(x) for x in a.get("symbols") or [])
            posted = autopilot.post_to_stream(conn, f"[비서 안건] {a.get('agenda') or ''}" + (f" (종목 {syms})" if syms else ""))
            note("schedule_meeting", bool(posted))
            return ({"ok": True, "note": f"상시 운용 중이라 회의 대신 채널(#{posted})에 바로 올렸어요"} if posted
                    else {"ok": False, "error": "상시 운용 채널이 아직 열려 있지 않아요(장 시간 밖) — start_now 로 지금 열 수 있어요"})
        if not cfg["enabled"]:
            note("schedule_meeting", False)
            return {"ok": False, "error": "자동 운용이 꺼져 있어 정해 둔 회의가 열리지 않아요 — 지금 토론은 start_now"}
        res = autopilot.apply_proposal(conn, {"action": "add", "time": a.get("time"), "agenda": a.get("agenda"),
                                              "reason": a.get("agenda"), "symbols": a.get("symbols") or []},
                                       market.now_kst(), _window(), by="assistant")
        note("schedule_meeting", res["accepted"])
        if res["accepted"]:
            add(conn, "event", f"회의 일정 · {res['note']}", kind="setting", what="회의")
        return {"ok": res["accepted"], "note": res["note"]}

    async def cancel_meeting(a: dict) -> dict:
        res = autopilot.apply_proposal(conn, {"action": "cancel", "time": a.get("time") or "", "reason": a.get("reason")},
                                       market.now_kst(), _window(), by="assistant")
        note("cancel_meeting", res["accepted"])
        if res["accepted"]:
            add(conn, "event", f"회의 일정 · {res['note']} — {res['reason'] or ''}".rstrip(" —"), kind="setting", what="회의")
        return {"ok": res["accepted"], "note": res["note"]}

    sid = {"type": "integer"}
    hhmm = {"type": "string", "description": "HH:MM (한국 시각)"}
    own = [
        _tool("list_intents", "지금 운용 지시 목록(id·text·once).", {}, [], list_intents),
        _tool("add_intent", "운용 지시를 하나 더한다. once=true 면 다음 자동 판단 한 번에만 쓰고 지운다.",
              {"text": {"type": "string"}, "once": {"type": "boolean"}}, ["text"], add_intent),
        _tool("update_intent", "운용 지시를 고친다(합치거나 바뀐 방향으로).",
              {"id": sid, "text": {"type": "string"}, "once": {"type": "boolean"}}, ["id", "text"], update_intent),
        _tool("remove_intent", "운용 지시를 지운다(취소·더는 맞지 않음).", {"id": sid}, ["id"], remove_intent),
        _tool("get_schedule", "자동 운용 켜짐 여부·고정 시각·오늘 판단 일정과 상태·자동 판단 시간·막힌 이유.", {}, [], get_schedule),
        _tool("start_now", "지금 바로 판단(에이전트 토론)을 시작한다. instruction = 관리자의 즉시 지시 그대로. "
              "자동 운용이 켜져 있으면 과반 게이트·승인 없이 주문까지, 꺼져 있으면 만장일치·관리자 승인.",
              {"instruction": {"type": "string"}}, ["instruction"], start_now),
        _tool("get_settings", "지금 운영 설정 — 자동 운용(켜짐·정기 회의 시각·AI 추가 회의 수), 토론(라운드·후보 수·모델), 보고(아침 브리핑 "
              "시각·요일·매월 날짜·휴장일), 에이전트 참여.", {}, [], get_settings),
        _tool("update_schedule", "정기 회의 시각(slots: 1~6개, 그 수가 하루 회의 수)·AI 가 더할 회의 수(max_extra 0~6)·자동 운용 켜기/끄기(enabled)·"
              "회의 방식(flexible: true = 새 안건이 있을 때만, false = 정기 시각마다 꼭)·운용 방식(mode: continuous 상시 운용 / meetings 정기 회의).",
              {"slots": {"type": "array", "items": hhmm}, "max_extra": {"type": "integer"}, "enabled": {"type": "boolean"},
               "flexible": {"type": "boolean"}, "mode": {"type": "string", "enum": list(autopilot.MODES)}}, [], update_schedule),
        _tool("get_research", "오늘 운용 데스크 리서치 — 시장 분석(국면·시황·주목/피할 테마), 섹터(비중 확대·축소와 이유), 발굴 목록(종목·이유·점수), "
              "리스크 경보, 진행 중인 리서치, 단계별 마지막 시각.", {}, [], get_research),
        _tool("run_research", "지금 리서치를 시작한다(시장 분석 → 섹터 탐색 → 종목 발굴, 몇 분). note = 이번에 더 볼 것(예: '2차전지 쪽도').",
              {"note": {"type": "string"}}, [], run_research),
        _tool("update_debate", "토론 라운드(rounds 1~3)·후보 종목 수(max_candidates 2~10)·모델(model: 비우면 기본, claude-… 또는 local).",
              {"rounds": {"type": "integer"}, "max_candidates": {"type": "integer"}, "model": {"type": "string"}}, [], update_debate),
        _tool("update_briefing", "보고(아침 브리핑) — 켜기(enabled)·시각(time 05:00~12:00)·요일(weekdays 0=월~6=일)·매월 날짜(month_days, "
              "빈 배열이면 날짜 제한 없음)·휴장일 건너뛰기(skip_holidays).",
              {"enabled": {"type": "boolean"}, "time": hhmm, "weekdays": {"type": "array", "items": {"type": "integer"}},
               "month_days": {"type": "array", "items": {"type": "integer"}}, "skip_holidays": {"type": "boolean"}}, [], update_briefing),
        _tool("set_agent", "분석 에이전트를 회의에서 빼거나(enabled=false) 다시 넣는다. 실행 담당(트레이더)은 뺄 수 없다.",
              {"agent": {"type": "string", "description": "key·직함·이름"}, "enabled": {"type": "boolean"}}, ["agent", "enabled"], set_agent),
        _tool("set_baseline", "운용 수익률의 기준 원금 — mode=reset 은 이 금액으로 다시 정하기, mode=add 는 추가 입금(+)·출금(−) 하나 더하기. "
              "date 는 YYYY-MM-DD(비우면 오늘).",
              {"amount": {"type": "integer"}, "mode": {"type": "string", "enum": ["reset", "add"]}, "date": {"type": "string"},
               "note": {"type": "string"}}, ["amount", "mode"], set_baseline),
        _tool("get_policy", "운용 목표(투자 정책) — 항목별 지금 값, 아직 안 정한 항목, 최근 바뀐 기록, 항목 값 형식.", {}, [], get_policy),
        _tool("update_policy", "운용 목표를 고친다. changes = {항목: 값} — objective(absolute·relative·growth·income·preserve), "
              "target_return(연 %), benchmark(none·KOSPI·KOSDAQ), horizon(short·mid·long), turnover(low·mid·high), "
              "risk(conservative·balanced·aggressive), max_drawdown(%), equity_target(%), max_position(%), prefer·avoid(글 목록), notes. "
              "값을 null 로 주면 지운다. reason = 왜.",
              {"changes": {"type": "object"}, "reason": {"type": "string"}}, ["changes"], update_policy),
        _tool("start_advice", "자문 에이전트 상담을 연다 — 이 답 뒤로 관리자 말은 자문 에이전트가 받아 운용 목표를 하나씩 묻는다. "
              "topics 로 물을 항목만 고를 수 있다(비우면 전부).",
              {"topics": {"type": "array", "items": {"type": "string", "enum": list(policy.FIELDS)}}, "note": {"type": "string"}},
              [], start_advice),
        _tool("schedule_meeting", f"오늘 회의를 하나 더한다 — time(HH:MM, 자동 판단 시간 안, {autopilot.ASSIST_LEAD_MIN}분 뒤부터, 다른 회의와 "
              f"{autopilot.MIN_GAP_MIN}분 간격)·agenda(그 회의 안건, 에이전트들이 먼저 다룸)·symbols(꼭 볼 6자리 종목 코드). 하루 "
              f"{autopilot.MAX_ASSISTANT}번. 자동 운용이 켜져 있어야 열린다.",
              {"time": hhmm, "agenda": {"type": "string"}, "symbols": {"type": "array", "items": {"type": "string"}}},
              ["time", "agenda"], schedule_meeting),
        _tool("cancel_meeting", "오늘 남은 회의를 취소한다 — time(HH:MM, 비우면 다음 회의)·reason.",
              {"time": hhmm, "reason": {"type": "string"}}, ["reason"], cancel_meeting),
        _tool("make_report", "한 페이지 보고서(보유 종목·현황 · 최근 회의 요약 · 요즘 장내 이슈)를 만들어 채팅방에 남긴다. "
              "숫자는 코드가 채운다 — comment 에 2~3문장 의견만.", {"comment": {"type": "string"}}, [], make_report),
        _tool("report_system_issue", "시스템·개발 문제를 시스템 관리자에게 넘긴다 — 관리자에게 확인을 요청하는 대신 쓴다.", {"text": {"type": "string"}}, ["text"], report_system_issue),
        _tool("get_system_issues", "시스템 관리자가 맡은 이슈의 처리 상황 (열림·해결).", {}, [], get_system_issues),
        _tool("ask_agent", "분석 에이전트에게 묻는다 — 그 에이전트가 자기 성향·도구로 답한다(채팅방에 그 에이전트 말풍선으로 남음).",
              {"agent": {"type": "string", "description": "에이전트 key·직함·이름 중 하나"}, "question": {"type": "string"}},
              ["agent", "question"], ask_agent),
    ]
    return own + agents.build_tools(s, list(MARKET_TOOLS), KEY)


async def respond(conn: sqlite3.Connection, msg_id: int, header: str = "관리자의 새 메시지",
                  message: str | None = None) -> tuple[str, list[str]]:
    """→ (비서 답, 쓴 도구 이름들). 지시 정리·판단 시작·다른 에이전트 답은 도구가 바로 기록한다.
    header·message: 관리자 말 대신 넘겨받은 일(자문 상담 결과)을 줄 때."""
    p = market.provider()
    text = message if message is not None else conn.execute(
        "SELECT text FROM invest_assistant_messages WHERE id = ?", (msg_id,)).fetchone()["text"]
    try:  # 말에 나온 종목을 도구의 기본 후보로 (없으면 도구에 symbol 을 넣어야 함)
        cands = scopes.intent_symbols(conn, p, text)
    except (MarketError, TossError):
        cands = []
    s = agents.Session(conn=conn, scope={"scope": "assistant", "label": "비서", "candidates": [{**c, "held": 0} for c in cands]},
                       run_id=None, provider=p)
    s.titles[KEY] = TITLE
    used: list[str] = []
    model = config.agent_options(conn)["model"] or None
    raw = await LLM("invest_assistant", SYSTEM, _context(conn, msg_id, header, message), REPLY_SCHEMA, build_tools(conn, s, used, model),
                    model, MAX_TURNS)
    reply = str((raw or {}).get("reply") or "").strip()
    if not reply:
        raise ValueError("빈 답")
    return reply, used + [t["tool"] for t in s.tool_log]
