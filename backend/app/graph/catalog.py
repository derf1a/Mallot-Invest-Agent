"""에이전트 카탈로그 — 에이전트마다 지금 코드 · 설정 그대로의 프롬프트 · 도구 · 규칙 · LLM 경로 · 연결.

프롬프트 · 규칙 · 도구 설명은 모듈의 실제 값을 쓴다 (코드를 고치면 여기도 바뀐다). 템플릿 프롬프트는 예시 세션(가짜 후보 ·
보유 · 관리자 말)으로 그려서 LLM 이 실제로 받는 모양 그대로 보여 준다. LLM 경로는 app.llm.routes.resolve 로 지금 값을 계산한다.
"""

import inspect
import sqlite3
from pathlib import Path

from app.invest.advice import advisor, assistant
from app.invest.analysis import exits, screening
from app.invest.broker import config, trading
from app.invest.debate import agents, minutes, stream
from app.invest.ops import autopilot, desk, desk_agents
from app.llm import routes
from app.system import agent as system_agent
from app.system import issues

BACKEND = Path(__file__).resolve().parents[2]

EXAMPLE_SCOPE = {
    "scope": "portfolio", "label": "예시 범위 (보유 + 관심)",
    "candidates": [{"symbol": "005930", "name": "삼성전자", "security_type": "STOCK", "held": 10.0},
                   {"symbol": "069500", "name": "KODEX 200", "security_type": "ETF", "held": 0.0}],
}
EXAMPLE_NOTE = {"ts": "10:05", "text": "(예시) 삼성전자 비중이 이미 크니 추가 매수는 신중하게 봐 주세요"}
EXAMPLE_HOLD = {"symbol": "005930", "name": "삼성전자", "quantity": 10, "avg_price": 72_000, "last_price": 66_500,
                "pnl_rate": 66_500 / 72_000 - 1, "weight": 0.18}
EXAMPLE_HOLD_METRICS = {"change_5d": -0.031, "change_20d": -0.064, "rsi": 41, "trend": 0, "drawdown": -0.09, "ma20": 68_000}

# 도구 → 어디서 자료를 가져오나 (구조 그래프의 데이터 선)
TOOL_SOURCES = {
    "get_quote": "toss_market", "get_price_history": "toss_market", "get_technicals": "toss_market",
    "get_orderbook": "toss_market", "get_intraday": "toss_market", "get_investor_flow": "toss_market",
    "get_market_overview": "toss_market", "get_company_info": "toss_market", "get_sector": "toss_market",
    "search_news": "news_rss", "get_news_report": "news_rss", "get_valuation": "dart", "get_disclosures": "dart",
    "get_figure_news": "news_rss",
    "get_portfolio": "toss_account", "get_account": "toss_account", "get_buying_power": "toss_account",
    "get_sellable_quantity": "toss_account", "get_order_limits": "toss_account", "place_order": "trading",
}


def ref(obj) -> dict:
    """함수 · 클래스의 파일:줄 (backend 기준)."""
    try:
        path = Path(inspect.getsourcefile(obj) or "")
        line = inspect.getsourcelines(obj)[1]
        return {"path": path.relative_to(BACKEND).as_posix(), "line": line, "label": getattr(obj, "__qualname__", str(obj))}
    except (TypeError, OSError, ValueError):
        return {"path": "", "line": 0, "label": str(obj)}


def example_session(conn: sqlite3.Connection, gate_mode: str = "unanimous") -> agents.Session:
    return agents.Session(conn=conn, scope=EXAMPLE_SCOPE, run_id=None, provider=None, user_notes=lambda: [EXAMPLE_NOTE],
                          gate_mode=gate_mode,
                          holdings={"005930": exits.check(EXAMPLE_HOLD, EXAMPLE_HOLD_METRICS, exits.position_limit(conn))})


def _tool(name: str, info: dict | None = None, *, description: str | None = None, schema: dict | None = None,
          read_only: bool | None = None) -> dict:
    info = info or agents.TOOL_INFO.get(name, {})
    return {"name": name, "title": info.get("title", name), "description": description or info.get("desc", ""),
            "schema": schema if schema is not None else info.get("schema", {}),
            "read_only": read_only if read_only is not None else info.get("read_only", name != agents.EXECUTOR_TOOL),
            "source": TOOL_SOURCES.get(name, "app")}


def _tooldefs(defs) -> list[dict]:
    return [_tool(t.name, description=t.description, schema=t.input_schema, read_only=t.read_only) for t in defs]


def _route(feature: str, agent: str, model: str | None = None) -> dict:
    return routes.resolve(feature, model or None, agent=agent).as_dict()


def _person(p: dict) -> dict:
    return {k: p.get(k) for k in ("key", "title", "name", "full_name", "country")}


def debate_agents(conn: sqlite3.Connection) -> list[dict]:
    """토론 명단 (분석 에이전트 + 트레이더)."""
    roster = agents.get_roster(conn)
    opts = config.agent_options(conn)
    s = example_session(conn)
    analysts = [a for a in roster if a["enabled"] and not a["executor"]]
    executor = next((a for a in roster if a["enabled"] and a["executor"]), None)
    out = []
    for a in roster:
        rules = [{"title": "공통 규칙", "text": agents.COMMON_RULES, "ref": ref(agents.system_prompt)},
                 {"title": "게이트 — 회의(만장일치)", "text": agents.GATE_RULE["unanimous"], "ref": ref(agents.unanimity_gate)},
                 {"title": "게이트 — 자동 운용(과반)", "text": agents.GATE_RULE["supermajority"], "ref": ref(agents.supermajority_gate)}]
        if a["executor"]:
            rules += [{"title": "매도 수량", "text": agents.SELL_SIZING, "ref": ref(agents.trader_prompt)},
                      {"title": "주문 도구 검사", "ref": ref(agents._handlers),
                       "text": f"게이트가 닫힌 종목 · 반대 방향 거절, 종목당 1건, 세션당 {agents.MAX_ORDERS}건, "
                               f"종목당 시도 {agents.MAX_ATTEMPTS_PER_SYMBOL}회"},
                      {"title": "주문 검사 (모든 주문이 지나는 한 곳)", "ref": ref(trading.place_order),
                       "text": "전체 중지 → 주문 조건(토스 키 · PIN) → 국내 종목 → 정수 수량 · 호가 단위 → 건당 한도 → 하루 한도 → "
                               "매수 가능 금액 / 판매 가능 수량 → 매수 후 종목 비중"}]
            talks = [{"to": "trading", "why": "place_order — 열린 종목만, 코드 검사 두 겹"},
                     {"to": "admin", "why": "승인 카드 (승인 생략이 꺼져 있으면)"}]
            hears = [{"from": x["key"], "why": "최종 라운드 의견"} for x in analysts] + [{"from": "gate", "why": "열린 종목 · 방향"}]
        else:
            rules.insert(1, {"title": "보유 종목 매도 타이밍", "text": agents.EXIT_RULES, "ref": ref(exits.check)})
            talks = [{"to": x["key"], "why": "라운드 2+ 반박 · 동의 (responses)"} for x in analysts if x["key"] != a["key"]]
            talks += [{"to": "admin", "why": f"responses to=\"{agents.ADMIN}\""}, {"to": "gate", "why": "후보마다 입장 (views)"}]
            hears = [{"from": x["key"], "why": "직전 라운드 발언"} for x in analysts if x["key"] != a["key"]]
            hears += [{"from": "admin", "why": "첫 메시지 · 토론 중 채팅"}, {"from": "desk", "why": "오늘 리서치 요약"}]
        extra_prompts = []
        if a["key"] == "portfolio":
            extra_prompts.append({"title": "회의 후보 고르기", "text": screening.PICK_SYSTEM, "ref": ref(screening.llm_pick)})
        out.append({
            **_person(a), "group": "debate", "role": "executor" if a["executor"] else "analyst", "enabled": a["enabled"],
            "persona": a["persona"], "feature": "agent_action", "route": _route("agent_action", a["key"], opts["model"]),
            "system_prompt": agents.system_prompt(a, s), "extra_prompts": extra_prompts,
            "tools": [_tool(t) for t in a["tools"]], "rules": rules, "talks_to": talks, "hears_from": hears,
            "is_default": any(d["key"] == a["key"] and d["persona"] == a["persona"] and d["tools"] == a["tools"]
                              for d in agents.DEFAULT_ROSTER),
            "refs": [ref(agents.run_session), ref(stream.Stream)],
        })
    if executor is None:
        for x in out:
            x["warning"] = "켜진 실행 담당(트레이더)이 없어요"
    return out


def desk_people(conn: sqlite3.Connection) -> list[dict]:
    """운용 데스크 리서치 담당 (토론 명단과 따로)."""
    prompts = {"strategist": desk_agents.MARKET_SYSTEM, "sector": desk_agents.SECTOR_SYSTEM, "scout": desk_agents.SCOUT_SYSTEM}
    nxt = {"strategist": ("sector", "오늘 시장 분석 → 섹터 탐색"), "sector": ("scout", "비중 확대 섹터 → 종목 발굴"),
           "scout": ("portfolio", "발굴 목록 → 회의 후보 (+0.8 가점)")}
    out = []
    for key, prompt in prompts.items():
        p = desk.PEOPLE[key]
        to, why = nxt[key]
        out.append({
            **_person(p), "group": "desk", "role": "research", "enabled": True, "persona": "", "feature": "invest_desk",
            "route": _route("invest_desk", key), "system_prompt": prompt, "extra_prompts": [
                {"title": "상시 운용 채널에서 부탁에 답할 때", "text": stream.research_system(p), "ref": ref(stream.research_system)}],
            "tools": [_tool(t) for t in stream.RESEARCH_TOOLS],
            "rules": [{"title": "숫자는 코드, LLM 은 판단 · 이유만", "ref": ref(desk_agents.research),
                       "text": "실패하면 단계마다 코드가 정한다 (예: 시장 국면은 코스피 ±0.8%)"}],
            "talks_to": [{"to": to, "why": why}, {"to": "debate", "why": "리서치 요약이 모든 토론 에이전트의 시스템 프롬프트에"}],
            "hears_from": [{"from": "debate", "why": "상시 운용 발언의 '이름: 부탁'"}],
            "refs": [ref(desk_agents.research)],
        })
    return out


def helpers(conn: sqlite3.Connection) -> list[dict]:
    """관리자와 대화하거나 일정 · 회의록을 맡는 쪽."""
    opts = config.agent_options(conn)
    s = example_session(conn)
    a_tools = _tooldefs(assistant.build_tools(conn, s, [], opts["model"] or None))
    v_tools = _tooldefs(advisor._tools(conn, s, {}))
    roster = [a["key"] for a in agents.get_roster(conn) if a["enabled"] and not a["executor"]]
    return [
        {**_person(assistant.PROFILE), "group": "helper", "role": "assistant", "enabled": True, "persona": "",
         "feature": "invest_assistant", "route": _route("invest_assistant", assistant.KEY, opts["model"]),
         "system_prompt": assistant.SYSTEM, "extra_prompts": [
             {"title": "회의록 쓰기", "text": minutes.SYSTEM, "ref": ref(minutes.write)}],
         "tools": a_tools,
         "rules": [{"title": "주문 도구 없음", "text": "비서는 주문하지 않는다. 즉시 지시(start_now)는 토론을 열고, 주문은 토론의 트레이더만.",
                    "ref": ref(assistant.build_tools)},
                   {"title": "설정 변경은 각 모듈의 검사 그대로", "text": "주문 한도 · 승인 생략 · 주문 재개는 채팅으로 못 바꾼다. "
                    "전체 주문 중지는 켜기(halt_trading)만 한다.",
                    "ref": ref(assistant.build_tools)}],
         "talks_to": [{"to": "admin", "why": "채팅 답 · 보고서"}] + [{"to": k, "why": "ask_agent"} for k in roster]
                     + [{"to": "advisor", "why": "start_advice"}, {"to": "scheduler", "why": "schedule_meeting · cancel_meeting"},
                        {"to": "debate", "why": "start_now (즉시 판단)"}],
         "hears_from": [{"from": "admin", "why": "채팅"}, {"from": "advisor", "why": "상담 결과 (apply_advice)"}],
         "refs": [ref(assistant.build_tools)]},
        {**_person(advisor.PROFILE), "group": "helper", "role": "advisor", "enabled": True, "persona": "",
         "feature": "invest_assistant", "route": _route("invest_assistant", advisor.KEY, opts["model"]),
         "system_prompt": advisor.SYSTEM, "extra_prompts": [], "tools": v_tools,
         "rules": [{"title": "형식 검사", "text": "답은 record_answer 가 policy.clean 으로 검사한 값만 초안에 남는다.",
                    "ref": ref(advisor._tools)}],
         "talks_to": [{"to": "admin", "why": "질문 · 누르는 답"}, {"to": "assistant", "why": "상담이 끝나면 넘김"}],
         "hears_from": [{"from": "admin", "why": "상담 중 채팅"}], "refs": [ref(advisor._tools)]},
        {"key": "scheduler", "title": "일정 담당", "name": "", "full_name": "", "country": "", "group": "helper",
         "role": "scheduler", "enabled": True, "persona": "", "feature": "agent_action",
         "route": _route("agent_action", "scheduler", opts["model"]), "system_prompt": autopilot.PLAN_SYSTEM,
         "extra_prompts": [], "tools": [],
         "rules": [{"title": "코드가 검사", "ref": ref(autopilot.apply_proposal),
                    "text": "자동 판단 시간 안, 10분 뒤부터, 다른 판단과 20분 간격, 하루 추가 횟수"}],
         "talks_to": [{"to": "debate", "why": "다음 회의 시각 · 안건 · 꼭 볼 종목"}],
         "hears_from": [{"from": "debate", "why": "방금 끝난 판단 요약"}], "refs": [ref(autopilot.plan_next)]},
        {**_person(issues.PROFILE), "group": "helper", "role": "system", "enabled": True, "persona": "",
         "feature": system_agent.FEATURE, "route": _route(system_agent.FEATURE, system_agent.KEY, opts["model"]),
         "system_prompt": system_agent.SYSTEM, "extra_prompts": [], "tools": [],
         "rules": [{"title": "코드가 검사", "ref": ref(system_agent.apply),
                    "text": "대기 중인 요청 · 열린 이슈만, 한 요청은 한 번만. LLM 이 실패하면 그대로 두고 다음에 다시."},
                   {"title": "공개 저장소", "ref": ref(system_agent.publish),
                    "text": "GitHub 에 올리기 전에 금액 · 수량 · 계좌 형식을 지운다 (issues.scrub). 토큰이 없으면 앱 안에서만 정리."}],
         "talks_to": [{"to": "github", "why": "이슈 올리기 · 닫혔는지 읽기"}],
         "hears_from": [{"from": "debate", "why": "'시스템:' 문단 · 회의록 system_notes"}, {"from": "assistant", "why": "report_system_issue"}],
         "refs": [ref(system_agent.run)]},
    ]


def all_agents(conn: sqlite3.Connection) -> list[dict]:
    return debate_agents(conn) + desk_people(conn) + helpers(conn)


def one(conn: sqlite3.Connection, key: str) -> dict | None:
    return next((a for a in all_agents(conn) if a["key"] == key), None)
