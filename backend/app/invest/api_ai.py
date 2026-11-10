"""투자 탭 — 뉴스, LLM 리포트(실험실 news_report 실행), 에이전트 명단·토론 설정, 에이전틱 매매 세션(실험실 agent_action 실행),
자동 운용(운용 지시·판단 일정·켜기/끄기, autopilot.py)."""

import json
import sqlite3
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.db import data_dir, get_conn
from app.invest.advice import advisor, assistant
from app.invest.analysis import policy
from app.invest.broker import config
from app.invest.broker.trading import order_dict
from app.invest.debate import agents, scopes
from app.invest.deps import Pin, require_pin
from app.invest.jobs.agent_action import post_user_message, read_messages
from app.invest.jobs.news_report import resolve_targets
from app.invest.marketdata import market, news
from app.invest.ops import autopilot, briefing, desk, morning
from app.runs.runner import JobError, load_run, run_dict, start_run
from app.system import issues

router = APIRouter(prefix="/api/invest", tags=["invest"])
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def _run(conn: sqlite3.Connection, run_id: int) -> dict:
    run = load_run(conn, run_id)
    if run is None:
        raise HTTPException(404, "실행을 찾을 수 없습니다")
    return run


# ---- 뉴스 ----
@router.get("/news")
def list_news(conn: Conn, scope: Literal["holdings", "watchlist", "market", "symbol"] = "market",
              symbol: str | None = None, name: str | None = None, refresh: bool = False,
              limit: Annotated[int, Query(ge=1, le=100)] = 40) -> dict:
    """뉴스 목록. refresh 면 피드를 다시 받는다 (15분 캐시는 그대로 적용 — 강제 새로고침은 force)."""
    warnings: list[str] = []
    if scope == "symbol":
        if not symbol:
            raise HTTPException(400, "symbol 이 필요합니다")
        targets, include_market = [{"symbol": symbol, "name": name or symbol}], False
    else:
        try:
            targets, include_market = resolve_targets(conn, {"holdings": "holdings", "watchlist": "watchlist",
                                                              "market": "market"}[scope], [], 15, warn=warnings.append)
        except JobError as e:
            raise HTTPException(400, str(e)) from None
    symbols = [t["symbol"] for t in targets]
    have = news.recent(conn, symbols=symbols, market=include_market, limit=1)
    if refresh or not have:
        news.collect(conn, targets, include_market, per_query=12, force=refresh, on_error=warnings.append)
    items = news.recent(conn, symbols=symbols, market=include_market, limit=limit) if (symbols or include_market) else []
    return {"scope": scope, "targets": targets, "items": items, "warnings": warnings}


# ---- LLM 리포트 ----
class ReportIn(BaseModel):
    scope: Literal["holdings", "watchlist", "market", "all", "symbols"] = "all"
    symbols: list[str] = Field(default_factory=list)
    model: str = ""


def _runs(conn: sqlite3.Connection, kind: str, limit: int) -> list[dict]:
    rows = conn.execute("SELECT r.*, NULL AS dataset_name, NULL AS parent_name FROM runs r WHERE kind = ? "
                        "ORDER BY id DESC LIMIT ?", (kind, limit)).fetchall()
    return [run_dict(r) for r in rows]


def _artifact(run: dict, filename: str) -> dict | None:
    if not run.get("artifact_path"):
        return None
    try:
        return json.loads((data_dir() / run["artifact_path"] / filename).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@router.get("/reports")
def list_reports(conn: Conn, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[dict]:
    return _runs(conn, "news_report", limit)


@router.post("/reports", status_code=201)
def create_report(body: ReportIn, conn: Conn) -> dict:
    try:
        run_id = start_run(conn, "news_report", body.model_dump(), name=f"뉴스 리포트 · {body.scope}")
    except JobError as e:
        raise HTTPException(400, str(e)) from None
    return _run(conn, run_id)


@router.get("/reports/{run_id}")
def read_report(run_id: int, conn: Conn) -> dict:
    run = _run(conn, run_id)
    if run["kind"] != "news_report":
        raise HTTPException(404, "리포트가 아닙니다")
    return {**run, "report": _artifact(run, "report.json")}


# ---- 에이전트 ----
@router.get("/agents")
def read_agents(conn: Conn) -> dict:
    return {"roster": agents.get_roster(conn), "tools": agents.tool_catalog(), "defaults": agents.validate_roster(agents.DEFAULT_ROSTER),
            "options": config.agent_options(conn), "assistant": assistant.PROFILE,
            "desk": {"people": [{**desk.person(k), "role": v["role"]} for k, v in desk.PEOPLE.items()], "duties": desk.DUTIES,
                     "advisor": desk.ADVISOR, "system": issues.PROFILE}}


class OptionsIn(BaseModel):
    rounds: int | None = Field(default=None, ge=1, le=3)
    model: str | None = Field(default=None, max_length=64)
    max_candidates: int | None = Field(default=None, ge=2, le=10)


@router.put("/agents/options")
def save_agent_options(body: OptionsIn, conn: Conn, pin: Pin = None) -> dict:
    """토론 설정 (실험실) — 라운드·모델·후보 종목 수."""
    require_pin(pin, required=False)
    try:
        config.save_agent_options(conn, body.model_dump())
    except config.ConfigError as e:
        raise HTTPException(400, str(e)) from None
    return read_agents(conn)


class RosterIn(BaseModel):
    roster: list[dict[str, Any]]


@router.put("/agents")
def save_agents(body: RosterIn, conn: Conn, pin: Pin = None) -> dict:
    require_pin(pin, required=False)
    try:
        agents.save_roster(conn, body.roster)
    except agents.AgentError as e:
        raise HTTPException(400, str(e)) from None
    return read_agents(conn)


@router.post("/agents/reset")
def reset_agents(conn: Conn, pin: Pin = None) -> dict:
    require_pin(pin, required=False)
    agents.reset_roster(conn)
    return read_agents(conn)


class SessionIn(BaseModel):
    scope: Literal["symbol", "sector", "portfolio", "scan"] = "symbol"
    symbol: str = Field(default="", max_length=20)
    sector_id: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=500)  # 첫 메시지 (관리자 의견)
    rounds: int | None = Field(default=None, ge=1, le=3)  # 비우면 실험실 설정
    model: str | None = Field(default=None, max_length=64)


@router.get("/sectors")
def list_sectors() -> list[dict]:
    """섹터 고르기 — 토스증권 섹터 대분류·중분류."""
    try:
        return scopes.sectors(market.provider())
    except scopes.ScopeError as e:
        raise HTTPException(e.status, str(e)) from None


@router.get("/agent-scope")
def scope_preview(conn: Conn, scope: Literal["symbol", "sector", "portfolio", "scan"] = "symbol", symbol: str = "",
                  sector_id: str = "") -> dict:
    """토론을 시작하기 전에 어떤 후보 종목으로 토론할지 미리 보기."""
    try:
        return scopes.resolve(conn, market.provider(), {"scope": scope, "symbol": symbol, "sector_id": sector_id},
                              config.agent_options(conn)["max_candidates"])
    except scopes.ScopeError as e:
        raise HTTPException(e.status, str(e)) from None


def _session_orders(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    return [order_dict(r) for r in conn.execute("SELECT * FROM invest_orders WHERE run_id = ? ORDER BY id", (run_id,))]


@router.get("/agent-sessions")
def list_sessions(conn: Conn, symbol: str | None = None, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[dict]:
    runs = _runs(conn, "agent_action", 200 if symbol else limit)
    if symbol:
        runs = [r for r in runs if r["params"].get("symbol") == symbol][:limit]
    ids = [r["id"] for r in runs]
    now: dict[int, list[dict]] = {}
    if ids:  # 요약은 세션이 끝날 때의 상태 — 승인·거절 뒤 현재 주문 상태를 붙인다
        rows = conn.execute("SELECT id, run_id, symbol, name, side, quantity, status FROM invest_orders "
                            f"WHERE run_id IN ({','.join('?' * len(ids))}) ORDER BY id", ids)
        for o in rows:
            now.setdefault(o["run_id"], []).append({k: o[k] for k in ("id", "symbol", "name", "side", "quantity", "status")})
    for r in runs:
        r["orders_now"] = now.get(r["id"], [])
    return runs


@router.get("/agent-sessions/active")
def active_session(conn: Conn) -> dict | None:
    """진행 중인 세션 (한 번에 하나). 없으면 null."""
    return autopilot.active_session(conn)


@router.post("/agent-sessions", status_code=201)
def create_session(body: SessionIn, conn: Conn, pin: Pin = None) -> dict:
    if not config.toss_configured():
        raise HTTPException(409, "토스증권 API 키가 없어 에이전트가 계좌를 볼 수 없습니다 — 설정 > 투자에서 입력하세요")
    require_pin(pin)  # 트레이더가 실계좌 주문(기본은 승인 대기)을 만들 수 있다
    if body.scope == "symbol" and not body.symbol.strip():
        raise HTTPException(400, "종목을 골라 주세요")
    if body.scope == "sector" and not body.sector_id.strip():
        raise HTTPException(400, "섹터를 골라 주세요")
    label = {"symbol": body.symbol, "sector": f"섹터 {body.sector_id}", "portfolio": "전체", "scan": "시장 스캔"}[body.scope]
    if body.scope == "sector":  # 섹터 목록은 하루 캐시 — 진행 중 표시에 이름을 쓴다
        try:
            label = "섹터 " + next(x["name"] for x in scopes.sectors(market.provider()) if x["id"] == body.sector_id)
        except (StopIteration, scopes.ScopeError):
            pass
    with autopilot.SESSION_LOCK:  # 동시에 두 세션이 시작되지 않게 (확인 → 시작 사이, 자동 운용 시작과도)
        running = autopilot.active_session(conn)
        if running:
            raise HTTPException(409, {"message": f"이미 진행 중인 토론이 있습니다 (#{running['id']}) — 중지한 뒤 다시 시작하세요",
                                      "code": "session_running", "run_id": running["id"]})
        try:
            run_id = start_run(conn, "agent_action", body.model_dump(), name=f"에이전틱 매매 · {label}")
        except JobError as e:
            raise HTTPException(400, str(e)) from None
    return _run(conn, run_id)


# ---- 자동 운용 ----
class AutopilotIn(BaseModel):
    enabled: bool | None = None
    slots: list[str] | None = Field(default=None, max_length=autopilot.MAX_SLOTS)
    max_extra: int | None = Field(default=None, ge=0, le=autopilot.MAX_EXTRA)
    flexible: bool | None = None
    mode: Literal["continuous", "meetings"] | None = None


@router.get("/autopilot")
def read_autopilot(conn: Conn) -> dict:
    """자동 운용 설정·운용 지시·오늘 판단 일정(상태)·다음 판단."""
    return autopilot.status(conn)


@router.get("/autopilot/switch")
def read_autopilot_switch(conn: Conn) -> dict:
    """사이드바 표시용 — 자동 운용 켜짐 여부 · 운용 방식만 (장 운영을 묻지 않아 가볍다)."""
    cfg = autopilot.get_config(conn)
    return {"enabled": cfg["enabled"], "mode": cfg["mode"]}


@router.put("/autopilot")
def save_autopilot(body: AutopilotIn, conn: Conn, pin: Pin = None) -> dict:
    """켜기·끄기는 PIN 없이 .env 의 MIA_AUTOPILOT 을 고친다. 시각·방식 바꾸기는 주문 PIN."""
    if body.enabled and not config.toss_configured():
        raise HTTPException(409, "토스증권 API 키가 없어 자동 운용을 켤 수 없습니다 — 설정 > 투자에서 입력하세요")
    if any(v is not None for v in (body.slots, body.max_extra, body.flexible, body.mode)):
        require_pin(pin)
    try:
        autopilot.save_config(conn, enabled=body.enabled, slots=body.slots, max_extra=body.max_extra, flexible=body.flexible,
                              mode=body.mode)
    except autopilot.AutopilotError as e:
        raise HTTPException(e.status, str(e)) from None
    return autopilot.status(conn)


class IntentIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


@router.post("/autopilot/intents", status_code=201)
def add_intent(body: IntentIn, conn: Conn, pin: Pin = None) -> dict:
    """운용 지시 남기기 — 다음 자동 판단부터 후보 종목·섹터와 에이전트 프롬프트에 들어간다."""
    require_pin(pin)
    try:
        autopilot.add_intent(conn, body.text)
    except autopilot.AutopilotError as e:
        raise HTTPException(e.status, str(e)) from None
    return autopilot.status(conn)


@router.delete("/autopilot/intents/{intent_id}")
def delete_intent(intent_id: int, conn: Conn, pin: Pin = None) -> dict:
    require_pin(pin)
    try:
        autopilot.delete_intent(conn, intent_id)
    except autopilot.AutopilotError as e:
        raise HTTPException(e.status, str(e)) from None
    return autopilot.status(conn)


@router.get("/autopilot/preview")
def preview_autopilot(conn: Conn) -> dict:
    """지금 운용 지시·보유로 자동 판단하면 볼 후보 종목."""
    try:
        return scopes.resolve(conn, market.provider(), {"scope": "autopilot", "intents": [i["text"] for i in autopilot.intents(conn)]},
                              config.agent_options(conn)["max_candidates"])
    except scopes.ScopeError as e:
        raise HTTPException(e.status, str(e)) from None


@router.post("/autopilot/run", status_code=201)
def run_autopilot_now(conn: Conn, pin: Pin = None) -> dict:
    """지금 한 번 자동 판단 (일정과 같은 검사 — 켜져 있고, 주문 가능하고, 자동 판단 시간 안일 때)."""
    require_pin(pin)
    try:
        run_id = autopilot.run_now(conn)
    except autopilot.AutopilotError as e:
        raise HTTPException(e.status, str(e)) from None
    return _run(conn, run_id)


@router.post("/autopilot/resume", status_code=201)
def resume_autopilot(conn: Conn) -> dict:
    """멈춘 상시 운용 채널 다시 열기 — 켜기·끄기처럼 PIN 없이 (관리자만, 손님은 guard 가 막는다)."""
    try:
        run_id = autopilot.resume_stream(conn)
    except autopilot.AutopilotError as e:
        raise HTTPException(e.status, str(e)) from None
    return _run(conn, run_id)


# ---- 수익률 · 보고서 ----
@router.get("/performance")
def read_performance(conn: Conn) -> dict:
    """현황 서랍의 수익률 — 내 투자현황 평가 손익·수익률과 AI 주문 성과 (토스에 못 닿으면 portfolio_note)."""
    return briefing.performance(conn)


class ContributionIn(BaseModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    amount: int = Field(ge=-10_000_000_000, le=10_000_000_000)  # 추가 입금 +, 출금 −
    note: str = Field(default="", max_length=60)


class BaselineIn(BaseModel):
    contributions: list[ContributionIn] = Field(max_length=briefing.MAX_CONTRIBUTIONS)


@router.get("/baseline")
def read_baseline(conn: Conn) -> dict:
    """운용 수익률의 기준 원금 (넣은 돈 목록·합계·시작일)."""
    return briefing.get_baseline(conn)


@router.put("/baseline")
def save_baseline(body: BaselineIn, conn: Conn) -> dict:
    """기준 원금 목록을 통째로 바꾼다 (비우면 기준 없음). 돈이 움직이지 않는 기록이라 PIN 없이."""
    try:
        return briefing.set_baseline(conn, [c.model_dump() for c in body.contributions])
    except briefing.BaselineError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/baseline/contributions", status_code=201)
def add_contribution(body: ContributionIn, conn: Conn) -> dict:
    """추가 입금(+)·출금(−) 하나를 더한다."""
    try:
        return briefing.add_contribution(conn, body.amount, body.date, body.note)
    except briefing.BaselineError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/briefing")
def read_briefing(conn: Conn) -> dict:
    """한 페이지 보고서 (비서 의견 없이) + 글 버전."""
    data = briefing.snapshot(conn)
    return {**data, "text": briefing.to_text(data)}


class MorningIn(BaseModel):
    enabled: bool | None = None
    time: str | None = Field(default=None, max_length=5)
    weekdays: list[int] | None = Field(default=None, max_length=7)     # 0=월 … 6=일
    month_days: list[int] | None = Field(default=None, max_length=31)  # 비우면 날짜 제한 없음
    skip_holidays: bool | None = None
    close_enabled: bool | None = None                                   # 장 마감 보고
    close_time: str | None = Field(default=None, max_length=5)


@router.get("/morning-briefing")
def read_morning(conn: Conn) -> dict:
    """아침 브리핑 설정 (켜기·시각·마지막으로 보낸 날)."""
    return morning.get_config(conn)


@router.put("/morning-briefing")
def save_morning(body: MorningIn, conn: Conn) -> dict:
    """주문과 상관없는 설정이라 PIN 없이."""
    try:
        return morning.save_config(conn, enabled=body.enabled, time=body.time, weekdays=body.weekdays,
                                   month_days=body.month_days, skip_holidays=body.skip_holidays,
                                   close_enabled=body.close_enabled, close_time=body.close_time)
    except morning.MorningError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/morning-briefing/send", status_code=201)
def send_morning(conn: Conn, close: bool = False) -> dict:
    """지금 아침 브리핑(?close=true 면 장 마감 보고) 받아 보기 (채팅방에 보고서 카드로)."""
    return morning.send(conn, close=close)


# ---- 운용 데스크 ----
@router.get("/desk")
def read_desk(conn: Conn) -> dict:
    """운용 프로세스 — 리서치 담당·단계별 마지막 결과(시장 국면·확대 섹터·발굴 목록)·리스크 경보·진행 중인 리서치."""
    return desk.status(conn)


class DeskConfigIn(BaseModel):
    research: bool | None = None
    prep_min: int | None = None
    watch: bool | None = None
    watch_min: int | None = None
    drop_pct: float | None = None
    jump_pct: float | None = None
    breakout_pct: float | None = None
    adhoc: bool | None = None
    refresh: bool | None = None


@router.put("/desk/config")
def save_desk_config(body: DeskConfigIn, conn: Conn) -> dict:
    """데스크 설정 — 장 전 리서치 · 리스크 감시 간격 · 급락 · 급등 기준 · 수시 회의 · 급변 리서치.
    주문은 그대로 place_order 가 검사해 PIN 없이 (비서도 채팅으로 고친다)."""
    try:
        desk.save_config(conn, body.model_dump(exclude_none=True))
    except desk.DeskError as e:
        raise HTTPException(e.status, str(e)) from None
    return desk.status(conn)


class ResearchIn(BaseModel):
    note: str = Field(default="", max_length=300)


@router.post("/desk/research", status_code=201)
def run_research(conn: Conn, body: ResearchIn | None = None) -> dict:
    """지금 리서치(시장 분석 → 섹터 탐색 → 종목 발굴). 주문과 무관해 PIN 없이."""
    try:
        return {"id": desk.start_research(conn, "manual", (body.note if body else "").strip())}
    except desk.DeskError as e:
        raise HTTPException(e.status, str(e)) from None


# ---- 운용 목표 · 자문 상담 ----
@router.get("/policy")
def read_policy(conn: Conn) -> dict:
    """운용 목표 — 항목별 값·고를 수 있는 값, 빠진 필수 항목, 최근 바뀐 기록. 고치기는 대화로(자문 에이전트 → 비서)."""
    return policy.view(conn)


class AdviceIn(BaseModel):
    topics: list[str] | None = None


@router.post("/advice/start", status_code=201)
def start_advice(conn: Conn, body: AdviceIn | None = None) -> dict:
    """자문 상담 열기 — 첫 질문은 비서 대화 스레드에서(채팅방이 '입력 중'으로 이어 받음). 묻기만 해서 PIN 없이."""
    advisor.start(conn, by="manual", topics=body.topics if body else None)
    assistant.background(advisor.opening, conn)
    return advisor.status(conn)


@router.post("/advice/stop")
def stop_advice(conn: Conn) -> dict:
    """상담 끝내기 — 그때까지의 답을 비서에게 넘긴다(비서가 운용 목표에 반영). 답을 반영하는 건 비서 말과 같아 PIN 은 채팅에서 이미 받음."""
    if not advisor.state(conn)["open"]:
        raise HTTPException(409, "상담 중이 아니에요")
    assistant.background(advisor.stop, conn)
    return advisor.status(conn)


# ---- 투자 비서 ----
class AssistantIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@router.get("/assistant/messages")
def assistant_messages(conn: Conn, after: int = 0) -> dict:
    """비서 대화 (after 번 이후) + 답을 만드는 중인지."""
    return {"messages": assistant.messages(conn, after), "pending": assistant.pending(), "advice": advisor.status(conn)}


@router.post("/assistant/messages", status_code=201)
def post_assistant(body: AssistantIn, conn: Conn, pin: Pin = None) -> dict:
    """비서에게 말하기 — 답은 백그라운드에서. 비서가 운용 지시를 고치고 판단을 시작할 수 있어 주문 PIN."""
    require_pin(pin)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "내용을 입력하세요")
    return {"message": assistant.post(conn, text), "pending": assistant.pending()}


@router.post("/agent-sessions/{run_id}/stop")
def stop_session(run_id: int, conn: Conn) -> dict:
    """중지 요청 — 작업이 1초 안에 진행 중인 LLM 호출을 끊고 '취소'로 끝난다. 중지는 PIN 없이 (더 안전해지는 방향)."""
    run = _run(conn, run_id)
    if run["kind"] != "agent_action":
        raise HTTPException(404, "에이전트 세션이 아닙니다")
    if run["status"] in ("queued", "running"):
        conn.execute("UPDATE runs SET cancel_requested = 1 WHERE id = ?", (run_id,))
        conn.commit()
    return _run(conn, run_id)


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


@router.post("/agent-sessions/{run_id}/chat", status_code=201)
def post_chat(run_id: int, body: ChatIn, conn: Conn, pin: Pin = None) -> dict:
    """토론 중 관리자 의견 — 다음 라운드(또는 트레이더)부터 에이전트 프롬프트에 들어간다."""
    require_pin(pin)  # 에이전트(→ 주문 제안)에 영향을 주므로 세션 시작과 같은 PIN
    run = _run(conn, run_id)
    if run["kind"] != "agent_action":
        raise HTTPException(404, "에이전트 세션이 아닙니다")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "내용을 입력하세요")
    line = post_user_message(run_id, text) if run["status"] == "running" else None
    if line is None:
        raise HTTPException(409, "진행 중인 토론이 아니에요 — 다음 토론을 시작할 때 첫 메시지로 남겨 주세요")
    return line


@router.get("/agent-sessions/{run_id}/messages")
def session_messages(run_id: int, conn: Conn, after: int = 0) -> dict:
    """채팅방: after 번 이후 메시지 + 실행 상태 + 이 세션의 주문(승인 카드)."""
    run = _run(conn, run_id)
    if run["kind"] != "agent_action":
        raise HTTPException(404, "에이전트 세션이 아닙니다")
    from app.core.db import data_dir
    messages = read_messages(data_dir() / run["artifact_path"], after) if run.get("artifact_path") else []
    return {"run": run, "messages": messages, "orders": _session_orders(conn, run_id)}


@router.get("/agent-sessions/{run_id}")
def read_session(run_id: int, conn: Conn) -> dict:
    run = _run(conn, run_id)
    if run["kind"] != "agent_action":
        raise HTTPException(404, "에이전트 세션이 아닙니다")
    return {**run, "session": _artifact(run, "session.json"), "orders": _session_orders(conn, run_id)}
