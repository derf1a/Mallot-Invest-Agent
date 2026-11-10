"""구조 그래프 — 에이전트 · 코드 단계 · 사람 · 외부 자료가 어떻게 이어져 있는지 흐름(flow)마다 노드 · 선으로.

노드 kind: human(관리자) · agent · code(LLM 없는 코드 단계) · store(DB 에 남는 것) · data(외부 API).
선 kind: control(차례) · talk(에이전트끼리 · 사람과 말) · data(도구로 자료 읽기) · order(주문).
노드의 col · row 는 화면 배치 칸이다 (왼쪽에서 오른쪽으로 흐름). 분석 에이전트 줄은 지금 명단(켜진 에이전트)으로 그린다.
"""

import sqlite3

from app.graph import catalog
from app.invest.analysis import exits
from app.invest.broker import trading
from app.invest.debate import agents, scopes, stream, track
from app.invest.ops import autopilot, desk_agents
from app.invest.advice import advisor, assistant
from app.system import agent as system_agent
from app.system import github, issues

DATA = {"toss_market": "토스 시세 · 섹터", "toss_account": "토스 계좌", "news_rss": "뉴스 RSS", "dart": "OpenDART 재무",
        "trading": "주문 검사"}


def _n(id_: str, label: str, kind: str, col: float, row: float, sub: str = "", agent: str | None = None, obj=None) -> dict:
    return {"id": id_, "label": label, "sub": sub, "kind": kind, "col": col, "row": row, "agent": agent,
            "ref": catalog.ref(obj) if obj is not None else None}


def _e(source: str, target: str, label: str = "", kind: str = "control") -> dict:
    return {"source": source, "target": target, "label": label, "kind": kind}


def _analysts(conn: sqlite3.Connection) -> list[dict]:
    return [a for a in agents.get_roster(conn) if a["enabled"] and not a["executor"]]


def _executor(conn: sqlite3.Connection) -> dict | None:
    return next((a for a in agents.get_roster(conn) if a["enabled"] and a["executor"]), None)


def _sources(names: list[str]) -> set[str]:
    return {catalog.TOOL_SOURCES[t] for t in names if t in catalog.TOOL_SOURCES and catalog.TOOL_SOURCES[t] != "trading"}


def _data_nodes(col0: float, row: float, used: set[str]) -> list[dict]:
    keys = [k for k in ("toss_market", "news_rss", "dart", "toss_account") if k in used]
    return [_n(k, DATA[k], "data", col0 + i * 1.2, row) for i, k in enumerate(keys)]


def meeting(conn: sqlite3.Connection) -> dict:
    an, ex = _analysts(conn), _executor(conn)
    nodes = [_n("admin", "관리자", "human", 0, 0, "첫 메시지 · 채팅"),
             _n("scope", "범위 → 후보", "code", 0, 1.3, "단일 · 섹터 · 전체 · 시장 스캔", obj=scopes.resolve),
             _n("exits", "매도 점검", "code", 0, 2.6, "보유 종목 신호 (LLM 없음)", obj=exits.check),
             _n("desk", "리서치 요약", "store", 0, 3.9, "오늘 시장 · 섹터 · 발굴")]
    edges = [_e("admin", a["key"], "의견", "talk") for a in an[:1]]
    for i, a in enumerate(an):
        nodes.append(_n(a["key"], a["title"], "agent", 1.6, i * 1.1, a.get("name", ""), agent=a["key"]))
        edges += [_e("scope", a["key"], "후보"), _e("desk", a["key"], "참고", "data")]
        if i + 1 < len(an):
            edges.append(_e(a["key"], an[i + 1]["key"], "라운드 2+ 반박 · 동의", "talk"))
        edges.append(_e(a["key"], "gate", "입장"))
    edges.append(_e("exits", an[0]["key"] if an else "gate", "보유 줄 아래", "data"))
    nodes.append(_n("gate", "게이트", "code", 3, 1.5, "종목마다 만장일치 / 과반", obj=agents.session_gate))
    if ex:
        nodes += [_n(ex["key"], ex["title"], "agent", 4.2, 1.5, ex.get("name", ""), agent=ex["key"]),
                  _n("tool_check", "place_order 도구", "code", 5.4, 0.3, f"세션당 {agents.MAX_ORDERS}건 · 열린 종목만",
                     obj=agents._handlers),
                  _n("trading", "주문 검사", "code", 5.4, 1.5, "한도 · 호가 · 잔고 · 비중", obj=trading.place_order),
                  _n("approval", "승인 카드", "human", 5.4, 2.7, "승인 생략이 꺼져 있으면")]
        edges += [_e("gate", ex["key"], "열린 종목"), _e(ex["key"], "tool_check", "주문", "order"),
                  _e("tool_check", "trading", "", "order"), _e("trading", "approval", "pending_approval", "order"),
                  _e("approval", "toss_order", "제출", "order")]
        nodes.append(_n("toss_order", "토스 실계좌 주문", "data", 5.4, 3.9))
    nodes += [_n("minutes", "회의록", "agent", 4.2, 3.0, "비서가 정리", agent="assistant"),
              _n("scheduler", "일정 담당", "agent", 4.2, 4.2, "다음 회의 시각 · 안건", agent="scheduler", obj=autopilot.plan_next)]
    edges += [_e("gate", "minutes", "끝나면"), _e("minutes", "scheduler", "")]
    used = set().union(*(_sources(a["tools"]) for a in an)) if an else set()
    data = _data_nodes(1.0, max(len(an), 4) * 1.1 + 0.6, used)
    nodes += data
    for d in data:
        for a in an:
            if d["id"] in _sources(a["tools"]):
                edges.append(_e(a["key"], d["id"], "도구", "data"))
    return {"key": "meeting", "title": "투자 회의 (토론 세션)", "nodes": nodes, "edges": edges,
            "summary": "라운드 1 독립 의견 → 라운드 2+ 설득 · 반박 → 종목마다 게이트(코드) → 열린 종목만 트레이더 → 주문 검사 두 겹 → 승인 → 토스.",
            "refs": [catalog.ref(agents.run_session)]}


def continuous(conn: sqlite3.Connection) -> dict:
    an, ex = _analysts(conn), _executor(conn)
    nodes = [_n("triggers", "계기", "code", 0, 0, "관리자 말 · 리스크 경보 · 급변 · 매도 신호 · 점검", obj=stream.Stream),
             _n("speakers", f"말할 {stream.SPEAKERS}명 고르기", "code", 0, 1.4, "계기에 맞는 · 오래 말하지 않은 순"),
             _n("admin", "관리자", "human", 0, 2.8, "채널에 말하기")]
    edges = [_e("triggers", "speakers"), _e("admin", "triggers", "말", "talk")]
    for i, a in enumerate(an):
        nodes.append(_n(a["key"], a["title"], "agent", 1.6, i * 1.1, a.get("name", ""), agent=a["key"]))
        edges += [_e("speakers", a["key"], ""), _e(a["key"], "board", "입장 · 동의/반박")]
    nodes += [_n("board", "입장판", "store", 3, 0.6, "에이전트 · 종목별 최신 입장, 2시간"),
              _n("weights", "의견 가중치", "code", 3, 2.0, "확신 × 지난 성과 배수", obj=track.weight),
              _n("gate", "가중 게이트", "code", 4.2, 1.2, "주요 의견 1.5+ · 반대 1.0 이하"),
              _n("poll", "필수 의견", "code", 4.2, 2.6, "찬반 없는 분석 에이전트 전원에게")]
    edges += [_e("board", "gate"), _e("weights", "gate", "무게", "data"), _e("gate", "poll", "주문 안건"),
              _e("poll", an[0]["key"] if an else "gate", "다시 묻기", "talk")]
    if ex:
        nodes += [_n(ex["key"], ex["title"], "agent", 5.4, 1.2, ex.get("name", ""), agent=ex["key"]),
                  _n("trading", "주문 검사", "code", 5.4, 2.6, "한도 · 30분 같은 방향 1건 · 하루 6건", obj=trading.place_order),
                  _n("toss_order", "토스 실계좌 주문", "data", 5.4, 4.0, "승인 없이 제출")]
        edges += [_e("poll", ex["key"], "최종 판단 통과"), _e(ex["key"], "trading", "주문", "order"),
                  _e("trading", "toss_order", "", "order"), _e(ex["key"], "triggers", "트레이더 제안", "talk")]
    nodes.append(_n("research", "리서치 담당", "agent", 3, 3.6, "'이름: 부탁' 에 답 · 볼 종목 올리기", agent="scout"))
    edges.append(_e(an[0]["key"] if an else "board", "research", "부탁", "talk"))
    edges.append(_e("research", "triggers", "볼 종목", "talk"))
    return {"key": "continuous", "title": "상시 운용 채널", "nodes": nodes, "edges": edges,
            "summary": "정기 회의 없이 장 내내 계기마다 3명이 말하고, 입장판 + 의견 가중치로 게이트를 열면 필수 의견을 받은 뒤 트레이더가 승인 없이 주문.",
            "refs": [catalog.ref(stream.Stream)]}


def research_desk(conn: sqlite3.Connection) -> dict:
    nodes = [_n("toss_market", DATA["toss_market"], "data", 0, 0), _n("news_rss", DATA["news_rss"], "data", 0, 1.2),
             _n("dart", DATA["dart"], "data", 0, 2.4),
             _n("strategist", "시장 전략가", "agent", 1.4, 0, "국면 · 주목/피할 테마", agent="strategist"),
             _n("sector", "섹터 애널리스트", "agent", 2.8, 0, "비중 확대 · 축소 섹터", agent="sector"),
             _n("valuation", "가치 스크린", "code", 2.8, 2.4, "PER · PBR · ROE · S-RIM 점수"),
             _n("scout", "종목 발굴 애널리스트", "agent", 4.2, 1.0, "발굴 목록 8개", agent="scout"),
             _n("ideas", "발굴 목록", "store", 5.6, 1.6, "invest_desk"),
             _n("portfolio", "포트폴리오 매니저", "agent", 5.6, 0.2, "회의 후보 고르기", agent="portfolio"),
             _n("risk", "리스크 감시", "code", 4.2, 3.0, "보유 −4% · +6%, 발굴 +5% — 10분마다", obj=desk_agents.research),
             _n("alert", "경보 → 수시 회의 · 채널 계기", "store", 5.6, 3.0),
             _n("meeting", "투자 회의 / 상시 운용", "code", 7.0, 1.6)]
    edges = [_e("toss_market", "strategist", "지수 · 랭킹", "data"), _e("news_rss", "strategist", "시장 뉴스", "data"),
             _e("strategist", "sector", "오늘 시장 분석", "talk"), _e("toss_market", "sector", "섹터 등락표", "data"),
             _e("sector", "scout", "확대 섹터", "talk"), _e("dart", "valuation", "재무", "data"), _e("valuation", "scout", "가치 상위"),
             _e("scout", "ideas"), _e("ideas", "portfolio", "+0.8 가점", "data"), _e("portfolio", "meeting", "후보"),
             _e("toss_market", "risk", "등락", "data"), _e("risk", "alert"), _e("alert", "meeting", "꼭 볼 종목")]
    return {"key": "desk", "title": "운용 데스크 (리서치 · 리스크 감시)", "nodes": nodes, "edges": edges,
            "summary": "장 전 시장 분석 → 섹터 탐색 → 종목 발굴 → 회의 후보, 장 중 10분마다 리스크 감시 → 경보는 수시 회의 · 채널 계기.",
            "refs": [catalog.ref(desk_agents.research)]}


def assistant_flow(conn: sqlite3.Connection) -> dict:
    an = _analysts(conn)
    nodes = [_n("admin", "관리자", "human", 0, 1.2, "채팅"),
             _n("assistant", "투자 비서", "agent", 1.6, 1.2, assistant.PROFILE.get("name", ""), agent="assistant",
                obj=assistant.build_tools),
             _n("advisor", "자문 에이전트", "agent", 1.6, 3.2, advisor.PROFILE.get("name", ""), agent="advisor", obj=advisor._tools),
             _n("policy", "운용 목표", "store", 3.2, 3.2, "형식 검사 뒤 저장"),
             _n("intents", "운용 지시", "store", 3.2, -0.2, "다음 판단에 들어감"),
             _n("schedule", "오늘 회의 일정", "store", 3.2, 0.9, "schedule_meeting · cancel_meeting"),
             _n("meeting", "즉시 판단 (start_now)", "code", 3.2, 2.0, "토론 또는 채널에 관리자 말로")]
    edges = [_e("admin", "assistant", "말", "talk"), _e("assistant", "admin", "답 · 보고서", "talk"),
             _e("assistant", "intents", "add · update · remove"), _e("assistant", "schedule", ""), _e("assistant", "meeting", ""),
             _e("assistant", "advisor", "start_advice"), _e("admin", "advisor", "상담 답", "talk"),
             _e("advisor", "assistant", "상담 결과", "talk"), _e("assistant", "policy", "update_policy")]
    for i, a in enumerate(an):
        nodes.append(_n(a["key"], a["title"], "agent", 4.8, i * 0.9, "ask_agent", agent=a["key"]))
        edges.append(_e("assistant", a["key"], "ask_agent", "talk"))
    return {"key": "assistant", "title": "투자 비서 · 자문 에이전트", "nodes": nodes, "edges": edges,
            "summary": "관리자의 말에 비서가 답하고, 다른 에이전트에게 묻고, 운용 지시 · 회의 일정 · 운용 목표를 정리한다. 주문 도구는 없다.",
            "refs": [catalog.ref(assistant.build_tools)]}


def system_flow(conn: sqlite3.Connection) -> dict:
    an = _analysts(conn)
    nodes = [_n("reports", "시스템 요청", "store", 1.6, 1.2, "같은 말은 한 줄로 셈"),
             _n("system", "시스템 관리자", "agent", 3.2, 1.2, issues.PROFILE["name"], agent=system_agent.KEY, obj=system_agent.triage),
             _n("issues", "시스템 이슈", "store", 4.8, 0.4, "이슈 · 이어진 요청 · 기록"),
             _n("answers", "답 · 버림", "store", 4.8, 2.0, "다른 에이전트 프롬프트에 (brief)"),
             _n("github", f"GitHub {github.repo()}", "data", 6.4, 0.4, f"{github.TOKEN_ENV} 가 있을 때만"),
             _n("assistant", "투자 비서", "agent", 0, 2.4, assistant.PROFILE.get("name", ""), agent="assistant")]
    edges = [_e("assistant", "reports", "report_system_issue"), _e("reports", "system", "대기 요청"),
             _e("system", "issues", "issue · link"), _e("system", "answers", "answer · ignore"),
             _e("issues", "github", "올리기 (scrub)", "data"), _e("github", "issues", "닫힘 · 다시 열림", "data")]
    for i, a in enumerate(an):
        nodes.append(_n(a["key"], a["title"], "agent", 0, i * 0.5 - 0.6, "'시스템:' 문단", agent=a["key"]))
        edges.append(_e(a["key"], "reports", "", "talk"))
    return {"key": "system", "title": "시스템 관리자", "nodes": nodes, "edges": edges,
            "summary": "에이전트들이 알린 시스템 문제를 시스템 관리자가 이슈로 묶거나 답하고, 토큰이 있으면 GitHub 에 올리고 닫혔는지 읽어 온다.",
            "refs": [catalog.ref(system_agent.run)]}


FLOWS = {"meeting": meeting, "continuous": continuous, "desk": research_desk, "assistant": assistant_flow, "system": system_flow}


def flow(conn: sqlite3.Connection, key: str) -> dict | None:
    build = FLOWS.get(key)
    if build is None:
        return None
    out = build(conn)
    ids = {n["id"] for n in out["nodes"]}
    out["edges"] = [e for e in out["edges"] if e["source"] in ids and e["target"] in ids]
    out["mermaid"] = to_mermaid(out)
    return out


def flows(conn: sqlite3.Connection) -> list[dict]:
    return [{k: f[k] for k in ("key", "title", "summary")} for f in (flow(conn, key) for key in FLOWS) if f]


_SHAPES = {"human": ("([", "])"), "agent": ("[", "]"), "code": ("[[", "]]"), "store": ("[(", ")]"), "data": ("{{", "}}")}
_ARROWS = {"control": "-->", "talk": "-.->", "data": "-.->", "order": "==>"}


def to_mermaid(f: dict) -> str:
    """mermaid flowchart 글 (README · GitHub 에 그대로 붙일 수 있게)."""
    def esc(s: str) -> str:
        return s.replace('"', "'")
    lines = ["flowchart LR"]
    for n in f["nodes"]:
        a, b = _SHAPES[n["kind"]]
        label = esc(n["label"]) + (f"<br/><small>{esc(n['sub'])}</small>" if n["sub"] else "")
        lines.append(f'  {n["id"]}{a}"{label}"{b}')
    for e in f["edges"]:
        arrow = _ARROWS[e["kind"]]
        lines.append(f'  {e["source"]} {arrow}|"{esc(e["label"])}"| {e["target"]}' if e["label"]
                     else f"  {e['source']} {arrow} {e['target']}")
    return "\n".join(lines)
