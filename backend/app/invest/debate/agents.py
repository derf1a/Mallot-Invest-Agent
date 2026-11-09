"""멀티 에이전트 에이전틱 매매 — 성향(프롬프트)과 행동(도구)이 다른 에이전트들이 토론하고, 실행 담당 한 명만 주문한다.

토론 범위(scopes.py): 단일 종목 / 섹터(구성 주식 + 관련 ETF) / 전체(보유 + 관심). 범위가 정한 후보 종목마다 입장을 낸다.

흐름
1) 라운드 1 (독립): 분석 에이전트들이 서로의 의견을 보지 않고 동시에, 각자의 도구로 자료를 조회해 후보마다 입장을 낸다.
   입장 = buy(매수) / sell(매도) / none(의견 없음·관망).
2) 라운드 2+ (설득): 직전 라운드의 다른 에이전트 의견을 보고, 필요하면 도구로 사실을 다시 확인한 뒤 반박·설득하고
   입장을 유지하거나 바꾼다. 새 근거 없이 다수에 맞추는 동조(sycophancy)를 막으려고 변경 이유를 반드시 쓰게 한다
   (Huang et al. 2026, arXiv 2609.29701 — 토론의 주된 실패 양상이 동조 수렴).
   관리자(사용자)가 토론 중 채팅으로 남긴 의견은 다음 라운드(또는 트레이더) 프롬프트에 들어가고, 에이전트가 답한다.
3) 게이트 (코드, 종목마다): 기본(unanimous)은 그 종목에 대한 모든 분석 에이전트의 최종 입장이 한 방향(그 방향 + 의견 없음)일
   때만 aligned. 반대 의견이 하나라도 있으면 conflict, 모두 의견 없음이면 no_view, 에이전트 호출이 실패했으면 모든 종목 incomplete.
   자동 운용(autopilot.py)은 supermajority — 응답한 분석 에이전트의 과반이 그 방향이고 반대는 20% 이하(5명이면 1명까지)일 때
   aligned. 응답 실패는 의견 없음처럼 센다(과반을 채우지 못하게 작용).
   보유하지 않은 종목의 매도 쪽은 not_held 로 닫는다 — 안 가진 종목의 sell 은 '사지 말자'라 주문 안건이 아니다(close_unheld_sells).
   보유 종목: 후보에 가진 종목이 있으면 코드가 꺼낸 매도 점검(exits.py — 평단·손익·비중·지표·신호)이 그 종목 줄 아래에 붙고, 분석 에이전트는
   보유 종목마다 매도 타이밍을 따진다(EXIT_RULES — 지금 팔지·일부만 팔지, 들고 가면 언제 팔지).
4) 실행 (aligned 종목이 있을 때만): 트레이더(유일하게 place_order 도구를 가진 에이전트)가 계좌·한도·호가를 확인하고
   열린 종목을 그 방향으로 주문하거나(수량은 스스로 정함) 행동하지 않는다. place_order 도구가 게이트와 다른 종목·방향,
   같은 종목 두 번째 주문, 세션당 최대 건수를 거절하고, 그 뒤에 trading.place_order 가 한도·잔고·비중을 다시 검사한다.

참고: TradingAgents (Xiao et al., arXiv 2412.20138) — 역할 분담 애널리스트 → 토론 → 트레이더 → 리스크 통제 구조.
이 기능은 수익을 보장하지 않으며, 의사결정 근거를 펼쳐 보이기 위한 실험이다.
"""

import asyncio
import json
import re
import sqlite3
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.invest.analysis import exits, valuation
from app.invest.broker import config, portfolio, trading
from app.invest.broker.toss import TossError
from app.invest.broker.trading import TradeError
from app.invest.marketdata import indicators, market, news
from app.invest.marketdata.market import MarketError
from app.llm.client import LLMError, ToolDef, agentic

STANCES = ("buy", "sell", "none")
STANCE_KO = {"buy": "매수", "sell": "매도", "none": "의견 없음", "mixed": "종목별"}
EXECUTOR_TOOL = "place_order"
ROSTER_KEY = "invest_agent_roster"
MAX_ORDERS = 3             # 세션당 주문 건수 상한
INTRADAY_BARS = 600   # 분봉 수 — 시간외까지 받아 정규장만 추려도 그날 시가가 남게
REPORT_DAYS = 7       # 이보다 오래된 뉴스 리포트는 도구가 내주지 않는다 (수동으로만 만들어져 판단 자료로 낡음)
MAX_ATTEMPTS_PER_SYMBOL = 2
ADMIN = "관리자"
GATE_MODES = ("unanimous", "supermajority")
MAX_OPPOSE_SHARE = 0.2     # supermajority: 반대는 응답한 분석 에이전트의 이 비율까지 (내림 — 5명이면 1명, 4명이면 0명)
GATE_RULE = {
    "unanimous": "한 종목에 대한 모든 에이전트의 최종 입장이 한 방향(그 방향 + 의견 없음)일 때만 그 종목 주문이 나갈 수 있습니다.",
    "supermajority": f"한 종목에 대한 에이전트의 과반이 같은 방향이고 반대가 {MAX_OPPOSE_SHARE:.0%} 이하(극소수)일 때만 그 종목 주문이 "
                     "나갈 수 있습니다.",
}


class AgentError(Exception):
    pass


class ToolInputError(Exception):
    """도구 인자가 잘못됨 (에이전트가 읽고 고치도록 오류로 돌려준다)."""


# ---- 세션 ----
@dataclass
class Session:
    """한 번의 토론 세션 상태 — 도구 핸들러가 공유한다. scope = scopes.resolve() 결과."""
    conn: sqlite3.Connection
    scope: dict
    run_id: int | None
    provider: Any  # SampleMarket | TossMarket
    gate: dict = field(default_factory=lambda: {"status": "pending", "symbols": {}, "aligned": []})
    order_attempts: dict[str, int] = field(default_factory=dict)
    orders: dict[str, dict] = field(default_factory=dict)       # 종목 → 만들어진 주문
    order_errors: dict[str, str] = field(default_factory=dict)  # 종목 → 마지막 거절 사유
    tool_log: list[dict] = field(default_factory=list)
    titles: dict[str, str] = field(default_factory=dict)  # 에이전트 키 → 이름 (채팅 표시용)
    on_event: Callable[[dict], None] | None = None          # 채팅방으로 흘려보낼 이벤트
    user_notes: Callable[[], list[dict]] | None = None      # 관리자(사용자)가 채팅으로 남긴 의견 [{ts, text}]
    gate_mode: str = "unanimous"                            # GATE_MODES — 자동 운용은 supermajority
    auto_submit: bool = False                               # 트레이더 주문을 승인 없이 제출 (자동 운용)
    brief: str = ""                                         # 오늘 운용 데스크 리서치 요약 (시장 국면·섹터·발굴 이유·경보)
    policy: str = ""                                        # 운용 목표 (관리자가 자문 에이전트와 정한 투자 정책) 글
    agenda: str = ""                                        # 이번 회의 안건 (일정 담당·비서·리스크 경보가 정함)
    system: str = ""                                        # 시스템 관리자가 맡은 이슈 (issues.brief) — 다시 요청하지 않게
    max_orders: int = MAX_ORDERS                            # 이 세션에서 낼 수 있는 주문 수 (상시 운용은 하루 남은 건수까지)
    holdings: dict[str, dict] = field(default_factory=dict)  # 후보 중 보유 종목의 매도 점검 (exits.checks) — 종목 → 사실·신호

    @property
    def candidates(self) -> list[dict]:
        return self.scope["candidates"]

    @property
    def symbols(self) -> list[str]:
        return [c["symbol"] for c in self.candidates]

    @property
    def label(self) -> str:
        return self.scope.get("label") or ", ".join(self.symbols)

    def name_of(self, symbol: str) -> str:
        return next((c["name"] for c in self.candidates if c["symbol"] == symbol), symbol)

    def notes(self) -> list[dict]:
        return list(self.user_notes()) if self.user_notes else []

    def emit(self, event: dict) -> None:
        if self.on_event:
            self.on_event(event)


def _r(v: float | None, nd: int = 2) -> float | None:
    return None if v is None else round(v, nd)


# ---- 도구 ----
_SYM = {"symbol": {"type": "string", "description": "종목 코드 (후보가 하나면 생략 가능)"}}


def _schema(**props) -> dict:
    return {"type": "object", "properties": {**_SYM, **props}}


TOOL_INFO: dict[str, dict] = {
    "get_quote": {"title": "현재가", "desc": "현재가·전일 대비·오늘 시고저·거래량·최근 범위 고저·상하한가를 조회한다.",
                  "schema": _schema()},
    "get_price_history": {"title": "일봉 기록", "desc": "최근 일봉(날짜·종가·거래량)을 최대 120일까지 조회한다.",
                          "schema": _schema(days={"type": "integer", "minimum": 5, "maximum": 120})},
    "get_technicals": {"title": "기술 지표", "desc": "이동평균(5·20·60·120일), RSI14, MACD, 20일 변동성, 기간 수익률, 거래량 비율, 기간 고저(일봉 고가·저가). "
                                               "일봉이라 현재가가 조금 늦을 수 있어요 — 지금 가격은 get_quote 기준.",
                       "schema": _schema()},
    "get_intraday": {"title": "분봉 흐름", "desc": "오늘(또는 직전 거래일) 정규장 분봉 요약 — 시가·고가·저가·현재가·VWAP·최근 30분 흐름.",
                     "schema": _schema()},
    "get_orderbook": {"title": "호가", "desc": "매도·매수 호가 상위 5단계와 잔량, 매수/매도 잔량 비율.", "schema": _schema()},
    "get_investor_flow": {"title": "투자자별 매매", "desc": "최근 거래일 개인·외국인·기관 순매수 수량과 합계 (국내 종목).",
                          "schema": _schema(days={"type": "integer", "minimum": 1, "maximum": 20})},
    "get_company_info": {"title": "종목 정보", "desc": "시장, 종목 유형(주식·ETF), 상장일, 발행주식수, 시가총액, 거래정지 여부.",
                         "schema": _schema()},
    "get_valuation": {"title": "가치평가", "desc": "공시 재무 기준 가치 점수(0~100)·등급, PER·PBR·ROE·영업이익률·성장률·부채비율, 업종 중앙값 대비, S-RIM 적정가치 "
                                               "대비 여유, 항목별 점수와 근거 보고서. 재무가 없으면 available=false 와 이유.",
                      "schema": _schema()},
    "compare_candidates": {"title": "후보 비교", "desc": "후보 종목 전체를 한 번에 비교 — 현재가·등락률·5/20/60일 수익률·RSI·변동성·"
                                                    "내 보유 수량. 여러 종목 토론에서 먼저 쓰면 좋다.",
                           "schema": {"type": "object", "properties": {}}},
    "get_sector_overview": {"title": "섹터 개요", "desc": "섹터 토론일 때 섹터 요약·기간별 등락률·시가총액 상위 구성 종목·관련 ETF.",
                            "schema": {"type": "object", "properties": {}}},
    "search_news": {"title": "뉴스 검색", "desc": "종목(또는 query)의 최근 뉴스 제목·출처·시각 (Google 뉴스 RSS, 본문 없음).",
                    "schema": _schema(query={"type": "string"})},
    "get_latest_report": {"title": "최근 AI 리포트", "desc": "가장 최근 LLM 뉴스 리포트에서 후보 종목·시장 부분.",
                          "schema": {"type": "object", "properties": {}}},
    "get_market_overview": {"title": "시장 지표", "desc": "코스피·코스닥·국채 금리, 장 운영 여부, 상승·하락 상위 종목.",
                            "schema": {"type": "object", "properties": {}}},
    "get_portfolio": {"title": "내 투자현황", "desc": "내 전체 자산 — 토스 실계좌 보유(국내·해외), 원화·달러 예수금, 다른 계좌 보유(수기), "
                                                  "종목별 비중·손익률, 현금 비중, 집중도(상위 1·3종목, HHI), 후보 종목 보유 비중.",
                      "schema": {"type": "object", "properties": {}}},
    "get_trading_limits": {"title": "주문 한도", "desc": "건당·하루 한도, 오늘 사용액, 매수 가능 금액, 후보 종목별 판매 가능 수량, 종목 비중 한도.",
                           "schema": {"type": "object", "properties": {}}},
    EXECUTOR_TOOL: {"title": "주문 실행", "desc": "게이트가 열린 종목만 그 방향으로 주문한다. 종목당 1건, 세션당 최대 3건. "
                                                 "symbol, side=buy|sell, quantity=정수 주, order_type=market|limit, limit 이면 price.",
                    "read_only": False,
                    "schema": {"type": "object", "properties": {
                        **_SYM,
                        "side": {"type": "string", "enum": ["buy", "sell"]},
                        "quantity": {"type": "integer", "minimum": 1},
                        "order_type": {"type": "string", "enum": ["market", "limit"]},
                        "price": {"type": "number"},
                        "reason": {"type": "string"}}, "required": ["side", "quantity", "order_type", "reason"]}},
}


def tool_catalog() -> list[dict]:
    return [{"name": k, "title": v["title"], "description": v["desc"], "executor_only": k == EXECUTOR_TOOL}
            for k, v in TOOL_INFO.items()]


def _handlers(s: Session) -> dict[str, Callable[[dict], Awaitable[object]]]:
    p = s.provider

    def pick(a: dict) -> str:
        sym = str(a.get("symbol") or "").strip()
        if sym:
            return sym
        if len(s.symbols) == 1:
            return s.symbols[0]
        raise ToolInputError("symbol 을 지정하세요 — 후보: " + ", ".join(f"{c['name']}({c['symbol']})" for c in s.candidates))

    async def get_quote(a: dict) -> dict:
        snap = market.snapshot(p, pick(a))
        keys = ("symbol", "name", "price", "currency", "prev_close", "change_rate", "open", "high", "low", "volume",
                "avg_volume_20", "range_high", "range_low", "range_bars", "upper_limit", "lower_limit", "tick")
        return {**{k: snap.get(k) for k in keys}, "data_source": _source(p)}

    async def get_price_history(a: dict) -> dict:
        days = max(5, min(int(a.get("days") or 60), 120))
        sym = pick(a)
        cs = p.candles(sym, "1d", days)
        return {"symbol": sym, "bars": [[c["t"], c["c"], c["v"]] for c in cs], "columns": ["date", "close", "volume"],
                "data_source": _source(p)}

    async def get_technicals(a: dict) -> dict:
        sym = pick(a)
        cs = p.candles(sym, "1d", 200)
        out = indicators.summary([c["c"] for c in cs], [c["v"] for c in cs])
        if cs:  # 기간 고저는 종가가 아니라 일봉 고가·저가로 — get_quote 의 range_high·range_low 와 같은 값
            out.update(high_in_range=max(c["h"] for c in cs), low_in_range=min(c["l"] for c in cs))
        ma = {k: out.get(k) for k in ("ma5", "ma20", "ma60")}
        if all(ma.values()):
            out["ma_alignment"] = ("정배열" if ma["ma5"] > ma["ma20"] > ma["ma60"] else
                                   "역배열" if ma["ma5"] < ma["ma20"] < ma["ma60"] else "혼조")
        return {"symbol": sym, **out, "data_source": _source(p)}

    async def get_intraday(a: dict) -> dict:
        sym = pick(a)
        ms = market.session_bars(p.candles(sym, "1m", INTRADAY_BARS))
        if not ms:
            return {"symbol": sym, "bars": 0}
        vol = sum(m["v"] for m in ms) or 1
        vwap = sum(m["c"] * m["v"] for m in ms) / vol
        tail = ms[-30:]
        return {"symbol": sym, "day_open": ms[0]["o"], "high": max(m["h"] for m in ms), "low": min(m["l"] for m in ms),
                "last": ms[-1]["c"], "vwap": _r(vwap), "bars": len(ms), "from": ms[0]["t"], "to": ms[-1]["t"],
                "last_30m_change": _r(tail[-1]["c"] / tail[0]["o"] - 1, 4) if tail[0]["o"] else None,
                "data_source": _source(p)}

    async def get_orderbook(a: dict) -> dict:
        sym = pick(a)
        b = p.orderbook(sym)
        asks, bids = b["asks"][:5], b["bids"][:5]
        ask_v, bid_v = sum(x["volume"] or 0 for x in asks), sum(x["volume"] or 0 for x in bids)
        return {"symbol": sym, "asks": asks, "bids": bids,
                "bid_ask_volume_ratio": _r(bid_v / ask_v) if ask_v else None, "data_source": _source(p)}

    async def get_investor_flow(a: dict) -> dict:
        sym = pick(a)
        rows = p.investor_trading(sym, max(1, min(int(a.get("days") or 10), 20)))
        total = {k: sum(r[k] or 0 for r in rows) for k in ("individual", "foreigner", "institution")}
        return {"symbol": sym, "days": rows, "net_total": total, "unit": "주(순매수 수량)", "data_source": _source(p)}

    async def get_company_info(a: dict) -> dict:
        snap = market.snapshot(p, pick(a))
        keys = ("symbol", "name", "market", "security_type", "list_date", "shares_outstanding", "market_cap", "suspended",
                "leverage", "currency")
        return {**{k: snap.get(k) for k in keys}, "data_source": _source(p)}

    async def get_valuation(a: dict) -> dict:
        return {**valuation.report(s.conn, p, pick(a)), "data_source": "OpenDART 공시 재무 + 토스 시가총액"}

    async def compare_candidates(_: dict) -> dict:
        quotes = p.quotes(s.symbols)
        rows = []
        for c in s.candidates:
            try:
                cs = p.candles(c["symbol"], "1d", 130)
                summ = indicators.summary([x["c"] for x in cs], [x["v"] for x in cs])
                prev = cs[-2]["c"] if len(cs) >= 2 else None
                last = (quotes.get(c["symbol"]) or {}).get("price") or summ.get("last_close")
                rows.append({"symbol": c["symbol"], "name": c["name"], "type": c.get("security_type") or "",
                             "price": last, "change_rate": _r(last / prev - 1, 4) if last and prev else None,
                             "return_5d": summ.get("return_5d"), "return_20d": summ.get("return_20d"),
                             "return_60d": summ.get("return_60d"), "rsi14": summ.get("rsi14"),
                             "volatility_20d": summ.get("volatility_20d_annualized"), "held_quantity": c.get("held", 0)})
            except MarketError as e:
                rows.append({"symbol": c["symbol"], "name": c["name"], "error": str(e)})
        return {"scope": s.label, "candidates": rows, "data_source": _source(p)}

    async def get_sector_overview(_: dict) -> dict:
        sec = s.scope.get("sector")
        if not sec:
            return {"available": False, "note": "섹터 토론이 아닙니다"}
        detail = p.sector(sec["id"])
        return {"available": True, "name": detail["name"], "summary": detail.get("summary"),
                "change_rates": detail.get("change_rates"), "top_stocks": detail["stocks"][:10], "etfs": detail["etfs"][:5],
                "data_source": _source(p)}

    async def search_news(a: dict) -> dict:
        q = str(a.get("query") or "").strip()[:60]
        sym = None
        if not q:
            sym = pick(a)
            q = news.symbol_query(s.name_of(sym))
        try:
            items = news.fetch(s.conn, q, sym, 10)
        except news.NewsError as e:
            return {"error": str(e), "items": []}
        return {"query": q, "items": [{"id": n["id"], "title": n["title"], "source": n["source"],
                                        "published_at": n["published_at"]} for n in items]}

    async def get_latest_report(_: dict) -> dict:
        row = s.conn.execute("SELECT id, finished_at, artifact_path FROM runs WHERE kind = 'news_report' "
                             "AND status = 'succeeded' ORDER BY id DESC LIMIT 1").fetchone()
        rep = load_report(row["artifact_path"]) if row else None
        if not rep:
            return {"available": False}
        age = (market.now_kst().date() - date.fromisoformat(str(row["finished_at"])[:10])).days
        if age > REPORT_DAYS:
            return {"available": False, "note": f"마지막 리포트가 {age}일 전({str(row['finished_at'])[:10]})이에요. 자동으로 만들지 않으니 search_news 로 보세요"}
        parts = [x for x in rep.get("sections") or [] if x.get("symbol") in (*s.symbols, None)]
        return {"available": True, "report_id": row["id"], "finished_at": row["finished_at"],
                "headline": rep.get("headline"), "sections": [{"label": x["label"], **x["analysis"]} for x in parts]}

    async def get_market_overview(_: dict) -> dict:
        try:
            gainers = p.rankings("gainers", 5, conn=s.conn)
            losers = p.rankings("losers", 5, conn=s.conn)
        except MarketError:
            gainers, losers = [], []
        brief = lambda rs: [{"symbol": r["symbol"], "name": r["name"], "change_rate": _r(r["change_rate"], 4)} for r in rs]  # noqa: E731
        return {"indicators": p.indicators(), "session": p.session(), "top_gainers": brief(gainers),
                "top_losers": brief(losers), "data_source": _source(p)}

    async def get_portfolio(_: dict) -> dict:
        o = portfolio.overview(s.conn, p, candidates=s.symbols)
        top = sorted(o["positions"], key=lambda x: x["value_krw"], reverse=True)[:15]
        return {**{k: o[k] for k in ("total_assets_krw", "stock_value_krw", "cash", "cash_ratio", "usd_krw",
                                     "concentration", "other_accounts", "candidate_exposure")},
                "positions": [{"where": x["where"], "symbol": x["symbol"], "name": x["name"], "currency": x["currency"],
                               "quantity": x["quantity"], "avg_price": _r(x["avg_price"]), "last_price": x["last_price"],
                               "value_krw": round(x["value_krw"]), "weight": _r(x["weight"], 4),
                               "pnl_rate": _r(x["pnl_rate"], 4)} for x in top],
                "positions_total": len(o["positions"])}

    async def get_trading_limits(_: dict) -> dict:
        acct = trading.account(s.conn, market=p)
        lim = config.limits(s.conn)
        held = [h["symbol"] for h in acct.summary()["holdings"] if h.get("currency") in (None, "KRW")]  # 볼 종목에 없는 보유도 판매 가능 수량을 본다
        sellable = {**{c["symbol"]: 0.0 for c in s.candidates}, **{sym: acct.sellable(sym) for sym in held}}
        return {"halted": config.halted(s.conn), **lim, "position_limit_basis": "평가액 × |레버리지 배수| (일반 종목은 평가액)",
                "used_today_krw": round(trading._today_amount(s.conn)),
                "buying_power": round(acct.buying_power()), "sellable_quantity": sellable,
                "orders_this_session": len(s.orders), "max_orders_per_session": MAX_ORDERS,
                "agent_order_needs_approval": not (s.auto_submit or config.agent_auto_approve(s.conn))}

    async def place_order(a: dict) -> dict:
        gate = s.gate
        aligned = {x["symbol"]: x["direction"] for x in gate.get("aligned") or []}
        if not aligned:
            return {"ok": False, "error": f"게이트가 열린 종목이 없습니다 ({GATE_RULE[s.gate_mode]})"}
        sym = str(a.get("symbol") or "").strip() or (next(iter(aligned)) if len(aligned) == 1 else "")
        if sym not in aligned:
            return {"ok": False, "error": "게이트가 열린 종목만 주문할 수 있습니다 — "
                                          + ", ".join(f"{s.name_of(k)}({k}) {STANCE_KO[v]}" for k, v in aligned.items())}
        side = str(a.get("side") or "")
        if side != aligned[sym]:
            return {"ok": False, "error": f"{s.name_of(sym)}: 게이트 방향({STANCE_KO[aligned[sym]]})과 다른 주문은 낼 수 없습니다"}
        if sym in s.orders:
            return {"ok": False, "error": f"{s.name_of(sym)}: 이번 세션에서 이미 주문했습니다 (종목당 1건)"}
        if len(s.orders) >= s.max_orders:
            return {"ok": False, "error": f"이번에 낼 수 있는 주문은 최대 {s.max_orders}건입니다"}
        if s.order_attempts.get(sym, 0) >= MAX_ATTEMPTS_PER_SYMBOL:
            return {"ok": False, "error": f"{s.name_of(sym)}: 주문 시도 횟수({MAX_ATTEMPTS_PER_SYMBOL}회)를 넘었습니다"}
        s.order_attempts[sym] = s.order_attempts.get(sym, 0) + 1
        try:
            qty = int(a.get("quantity") or 0)
            price = float(a["price"]) if a.get("price") not in (None, "") else None
        except (TypeError, ValueError):
            return {"ok": False, "error": "수량·가격 형식이 올바르지 않습니다"}
        try:
            order = trading.place_order(s.conn, symbol=sym, side=side, order_type=str(a.get("order_type") or "market"),
                                        quantity=qty, price=price, source="agent", run_id=s.run_id,
                                        reason=str(a.get("reason") or "")[:1000], market=p, auto_submit=s.auto_submit)
        except TradeError as e:
            s.order_errors[sym] = str(e)
            return {"ok": False, "error": str(e), "checks": e.checks}
        s.orders[sym] = order
        s.emit({"type": "order", "order": order})
        return {"ok": True, "order_id": order["id"], "symbol": sym, "status": order["status"],
                "quantity": order["quantity"], "avg_fill_price": order["avg_fill_price"], "checks": order["checks"],
                "note": "관리자(사용자) 승인 대기 — 승인하면 제출됩니다" if order["status"] == "pending_approval" else None}

    return {"get_quote": get_quote, "get_price_history": get_price_history, "get_technicals": get_technicals,
            "get_intraday": get_intraday, "get_orderbook": get_orderbook, "get_investor_flow": get_investor_flow,
            "get_company_info": get_company_info, "get_valuation": get_valuation, "compare_candidates": compare_candidates,
            "get_sector_overview": get_sector_overview, "search_news": search_news, "get_latest_report": get_latest_report,
            "get_market_overview": get_market_overview, "get_portfolio": get_portfolio,
            "get_trading_limits": get_trading_limits, EXECUTOR_TOOL: place_order}


def _source(p) -> str:
    return "샘플 데이터(실제 시세 아님)" if getattr(p, "key", "") == "sample" else "토스증권"


def load_report(artifact_path: str | None) -> dict | None:
    if not artifact_path:
        return None
    from app.core.db import data_dir
    path = data_dir() / artifact_path / "report.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build_tools(s: Session, names: list[str], agent_key: str) -> list[ToolDef]:
    handlers = _handlers(s)
    out = []
    for n in names:
        info = TOOL_INFO[n]
        base = handlers[n]

        async def logged(args: dict, _n: str = n, _h=base) -> object:
            entry = {"agent": agent_key, "tool": _n, "args": args}
            s.emit({"type": "tool", "agent": agent_key, "title": s.titles.get(agent_key, agent_key), "tool": _n,
                    "tool_title": TOOL_INFO[_n]["title"], "symbol": args.get("symbol") if isinstance(args, dict) else None})
            try:
                result = await _h(args)
            except (MarketError, TradeError, TossError, LLMError, ToolInputError, portfolio.PortfolioError) as e:
                entry["error"] = str(e)
                s.tool_log.append(entry)
                raise
            entry["result"] = _brief(result)
            s.tool_log.append(entry)
            return result

        out.append(ToolDef(n, info["desc"], info["schema"], logged, read_only=info.get("read_only", True)))
    return out


def _brief(value: object, limit: int = 400) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


# ---- 에이전트 명단 ----
DEFAULT_ROSTER: list[dict] = [
    {"key": "technical", "title": "차티스트", "enabled": True, "executor": False,
     "persona": "추세 추종형 단기 트레이더. 가격·거래량·이동평균·모멘텀을 근거로 판단하고, 추세가 꺾이면 빠르게 입장을 바꾼다. "
                "펀더멘털 이야기는 가격에 반영돼 있다고 본다.",
     "tools": ["compare_candidates", "get_quote", "get_price_history", "get_technicals", "get_intraday", "get_orderbook"]},
    {"key": "value", "title": "가치 투자자", "enabled": True, "executor": False,
     "persona": "보수적인 장기 가치 투자자. 공시 재무로 계산한 가치평가(PER·PBR·ROE·업종 대비·적정가치)를 먼저 보고, 시가총액·사업 안정성·수급의 "
                "지속성을 함께 본다. 단기 급등이나 유행에는 회의적이고, 싸다는 근거가 없으면 사지 않는다. 재무 자료가 없으면 없다고 말하고 추측하지 "
                "않는다. ETF 는 구성과 분산 효과를 본다.",
     "tools": ["compare_candidates", "get_valuation", "get_quote", "get_company_info", "get_price_history", "get_investor_flow",
               "get_sector_overview"]},
    {"key": "news", "title": "뉴스 애널리스트", "enabled": True, "executor": False,
     "persona": "이벤트·센티먼트 중심 애널리스트. 최근 뉴스 흐름과 시장 분위기에 민감하게 반응하지만, 제목만으로 과장하지 않는다.",
     "tools": ["search_news", "get_latest_report", "get_market_overview", "get_sector_overview", "get_quote"]},
    {"key": "growth", "title": "모멘텀 투자자", "enabled": True, "executor": False,
     "persona": "적극적인 성장·모멘텀 투자자. 강한 추세, 거래량 급증, 업종 주도주, 호재 뉴스처럼 상승 동력이 보이면 기회를 놓치지 말자고 "
                "먼저 매수 의견을 낸다. 다만 근거는 도구로 확인한 사실만 쓰고, 매수를 말할 때는 손절 기준(어디서 틀렸다고 볼지)을 함께 "
                "말한다. 동력이 꺾이면 빠르게 매도·비중 축소를 주장한다. 지나친 신중함에는 반박하되 리스크 매니저의 경고는 숫자로 따져 본다.",
     "tools": ["get_quote", "get_technicals", "get_intraday", "get_investor_flow", "search_news", "compare_candidates",
               "get_sector_overview"]},
    {"key": "portfolio", "title": "포트폴리오 매니저", "enabled": True, "executor": False,
     "persona": "내 투자현황 관점의 자산 배분 담당. 토스 보유(국내·해외)·예수금·다른 계좌 보유까지 합친 전체 자산에서 현금 비중, "
                "종목 집중도, 후보와 이미 가진 종목의 중복, 손익 상태를 보고 판단한다. 이미 비중이 큰 종목·같은 업종에 더 싣는 매수에는 "
                "반대하고, 현금이 부족하면 신중하며, 손실 종목 추가 매수(물타기)는 분명한 근거가 있을 때만 찬성한다. 과도한 비중·큰 평가익은 "
                "차익 실현·리밸런싱 매도를 제안한다.",
     "tools": ["get_portfolio", "compare_candidates", "get_trading_limits", "get_market_overview"]},
    {"key": "risk", "title": "리스크 매니저", "enabled": True, "executor": False,
     "persona": "손실 회피형 리스크 매니저이자 반대 검증 담당. 변동성·집중도·한도·시장 상황을 보고 다수 의견의 약점을 반드시 "
                "찾아 말한다. 위험이 보상보다 크면 반대하고, 불확실하면 의견 없음을 택한다.",
     "tools": ["get_portfolio", "get_trading_limits", "get_technicals", "get_market_overview"]},
    {"key": "trader", "title": "트레이더", "enabled": True, "executor": True,
     "persona": "실행 담당. 토론 결과와 계좌·한도·호가를 확인해 주문 여부와 수량을 정한다. 확신이 약하거나 호가가 불리하면 "
                "행동하지 않는다. 한 번에 한도의 일부만 쓴다.",
     "tools": ["get_quote", "get_orderbook", "get_portfolio", "get_trading_limits", EXECUTOR_TOOL]},
]
_KEY = re.compile(r"^[a-z][a-z0-9_]{1,24}$")
# 다국적 팀 — 화면은 '켄지 · 차티스트'(이름 · 직함), 프로필에 전체 이름·국적, 프롬프트에도 이름을 알려 준다. 직함(title)은 토론 안에서
# 서로를 부르는 이름(답장 대상)이라 그대로 둔다.
PEOPLE: dict[str, tuple[str, str, str]] = {
    "technical": ("켄지", "Kenji Watanabe", "일본"),
    "value": ("마거릿", "Margaret Collins", "영국"),
    "news": ("프리야", "Priya Sharma", "인도"),
    "growth": ("제이크", "Jake Morrison", "미국"),
    "portfolio": ("루카스", "Lukas Weber", "독일"),
    "risk": ("아마라", "Amara Okonkwo", "나이지리아"),
    "trader": ("디에고", "Diego Ramírez", "멕시코"),
}
# 프로필 그림 = 포켓몬(전국도감 번호, 화면이 PokeAPI 공개 스프라이트를 불러옴 — 그림은 저장소에 넣지 않는다). 역할에 맞춰:
# 차티스트 후딘(분석) · 가치 투자자 잠만보(느긋한 장기 보유) · 뉴스 패리퍼(소식 배달) · 포트폴리오 나옹(돈) · 리스크 단단지(방어) ·
# 모멘텀 리자몽(공격적) · 트레이더 피카츄(빠른 실행). 직접 만든 에이전트는 POKEMON_POOL 에서 키로 고른다.
POKEMON = {"technical": 65, "value": 143, "news": 279, "growth": 6, "portfolio": 52, "risk": 213, "trader": 25}
POKEMON_POOL = [1, 4, 7, 133, 39, 54, 94, 131, 196, 448, 658, 778]
MAX_POKEMON = 1025
NAME_POOL: list[tuple[str, str, str]] = [  # 직접 만든 에이전트 — 키로 고정해서 고른다
    ("엘레나", "Elena Rossi", "이탈리아"), ("올레", "Ole Hansen", "노르웨이"), ("파티마", "Fatima Al-Sayed", "이집트"),
    ("마테우스", "Mateus Costa", "브라질"), ("웨이", "Lin Wei", "중국"), ("소피", "Sophie Laurent", "프랑스"),
    ("투이", "Nguyen Minh Thuy", "베트남"), ("이선", "Ethan Brooks", "미국"), ("아이게림", "Aigerim Nurlanova", "카자흐스탄"),
    ("라파엘", "Rafael Mendes", "포르투갈"),
]


def default_person(key: str, taken: set[str] | None = None) -> tuple[str, str, str]:
    """(이름, 전체 이름, 국적) — 기본 에이전트는 정해진 사람, 나머지는 키로 고른 이름(이미 쓰는 이름은 건너뜀)."""
    if key in PEOPLE:
        return PEOPLE[key]
    taken = taken or set()
    start = zlib.crc32(key.encode())
    for i in range(len(NAME_POOL)):
        person = NAME_POOL[(start + i) % len(NAME_POOL)]
        if person[0] not in taken:
            return person
    return NAME_POOL[start % len(NAME_POOL)]


def get_roster(conn: sqlite3.Connection) -> list[dict]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (ROSTER_KEY,)).fetchone()
    if row:
        try:
            return validate_roster(json.loads(row[0]))  # 예전에 저장한 명단에는 이름이 없다 — 채워서
        except (ValueError, AgentError):
            pass
    return validate_roster([dict(a, tools=list(a["tools"])) for a in DEFAULT_ROSTER])


def validate_roster(roster: object) -> list[dict]:
    if not isinstance(roster, list) or not roster:
        raise AgentError("에이전트 명단이 비어 있습니다")
    keys, out = set(), []
    taken = {str(a.get("name") or "").strip() for a in roster if isinstance(a, dict)}
    for a in roster:
        if not isinstance(a, dict):
            raise AgentError("에이전트 형식이 올바르지 않습니다")
        key = str(a.get("key") or "")
        if not _KEY.match(key) or key in keys:
            raise AgentError(f"에이전트 키가 올바르지 않거나 겹칩니다: {key!r} (영문 소문자·숫자·_)")
        keys.add(key)
        title = str(a.get("title") or "").strip()[:30]
        persona = str(a.get("persona") or "").strip()[:1500]
        tools = [t for t in a.get("tools") or [] if t in TOOL_INFO]
        executor = bool(a.get("executor"))
        if not title or not persona:
            raise AgentError(f"{key}: 이름과 성향(프롬프트)을 입력하세요")
        name = str(a.get("name") or "").strip()[:20]
        full_name = str(a.get("full_name") or "").strip()[:40]
        country = str(a.get("country") or "").strip()[:20]
        if not name:
            name, full_name, country = default_person(key, taken)
            taken.add(name)
        try:
            pokemon = int(a.get("pokemon") or 0)
        except (TypeError, ValueError):
            pokemon = 0
        if not 1 <= pokemon <= MAX_POKEMON:
            pokemon = POKEMON.get(key) or POKEMON_POOL[zlib.crc32(key.encode()) % len(POKEMON_POOL)]
        if ADMIN in (title, name):
            raise AgentError(f"'{ADMIN}'은 사용자 이름이라 에이전트 이름·직함으로 쓸 수 없습니다")
        if not tools:
            raise AgentError(f"{title}: 도구를 하나 이상 고르세요")
        if (EXECUTOR_TOOL in tools) != executor:
            raise AgentError(f"{title}: 주문 도구는 실행 담당만, 실행 담당은 주문 도구를 가져야 합니다")
        out.append({"key": key, "title": title, "name": name, "full_name": full_name or name, "country": country, "pokemon": pokemon,
                    "persona": persona, "tools": tools, "executor": executor, "enabled": bool(a.get("enabled", True))})
    active = [a for a in out if a["enabled"]]
    if sum(a["executor"] for a in active) != 1:
        raise AgentError("실행 담당(주문 도구를 가진 에이전트)은 정확히 한 명이어야 합니다")
    if sum(not a["executor"] for a in active) < 2:
        raise AgentError("분석 에이전트가 두 명 이상 필요합니다")
    return out


def save_roster(conn: sqlite3.Connection, roster: object) -> list[dict]:
    clean = validate_roster(roster)
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (ROSTER_KEY, json.dumps(clean, ensure_ascii=False)))
    conn.commit()
    return clean


def reset_roster(conn: sqlite3.Connection) -> list[dict]:
    conn.execute("DELETE FROM settings WHERE key = ?", (ROSTER_KEY,))
    conn.commit()
    return get_roster(conn)


# ---- 프롬프트·스키마 ----
_CONF = {"type": "number", "minimum": 0, "maximum": 1}
_STANCE = {"type": "string", "enum": list(STANCES)}
_VIEW = {"type": "object", "properties": {
    "symbol": {"type": "string"}, "stance": _STANCE, "confidence": _CONF, "reason": {"type": "string"}},
    "required": ["symbol", "stance", "confidence", "reason"]}
OPINION_SCHEMA = {
    "type": "object",
    "properties": {
        "views": {"type": "array", "items": _VIEW, "minItems": 1},
        "thesis": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "object", "properties": {
            "fact": {"type": "string"}, "tool": {"type": "string"}}, "required": ["fact", "tool"]}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "message_to_others": {"type": "string"},
    },
    "required": ["views", "thesis", "evidence", "risks"],
}
# 주문 전 필수 의견 — 찬성·반대 중 하나로만 받는다 (의견 없음 없음)
POLL_SCHEMA = {**OPINION_SCHEMA, "properties": {**OPINION_SCHEMA["properties"], "views": {"type": "array", "minItems": 1, "items": {
    **_VIEW, "properties": {**_VIEW["properties"], "stance": {"type": "string", "enum": ["buy", "sell"]}}}}}}
REBUTTAL_SCHEMA = {
    "type": "object",
    "properties": {
        **OPINION_SCHEMA["properties"],
        "reason_for_change": {"type": "string"},
        "responses": {"type": "array", "items": {"type": "object", "properties": {
            "to": {"type": "string"}, "agree": {"type": "boolean"}, "point": {"type": "string"}},
            "required": ["to", "agree", "point"]}},
    },
    "required": ["views", "thesis", "evidence", "risks", "responses"],
}
TRADER_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {"type": "array", "items": {"type": "object", "properties": {
            "symbol": {"type": "string"}, "decision": {"type": "string", "enum": ["buy", "sell", "hold"]},
            "quantity": {"type": "integer", "minimum": 0}}, "required": ["symbol", "decision", "quantity"]}},
        "rationale": {"type": "string"},
        "risk_notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["decisions", "rationale"],
}

COMMON_RULES = f"""공통 규칙:
- 판단 전에 당신의 도구로 자료를 직접 조회하세요. 도구 결과와 다른 에이전트 발언에 없는 숫자·사실을 지어내지 마세요.
- 후보가 여러 개면 도구에 symbol 을 넣어 조회하고, views 에 모든 후보의 입장을 하나씩 내세요. 근거가 약한 후보는 none 으로.
- 근거가 약하거나 자료가 부족하면 stance 를 none(의견 없음)으로 하고 confidence 를 낮추세요.
- 보유하지 않은 종목(후보 줄에 '보유 N주'가 없음)은 팔 수 없습니다 — 그 종목의 sell 은 '사지 말자'로만 읽히고 매도 안건이 되지 않습니다.
- 도구 결과의 data_source 가 '샘플 데이터'면 실제 시장이 아니라는 점을 알고 판단하세요.
- {ADMIN}(사용자)가 채팅으로 남긴 의견이 있으면 진지하게 검토하고 responses 에 to="{ADMIN}"로 답하세요. 그 의견도 도구로 확인하고,
  근거가 없으면 정중히 반대할 수 있습니다.
- 시스템·도구·데이터 문제는 {ADMIN}에게 확인을 요청하지 마세요. message_to_others 에 '시스템: 내용' 문단으로 쓰면 시스템 관리자가 맡습니다.
- 수익을 장담하지 말고, 한국어로 간결하게. 응답은 주어진 JSON 스키마를 정확히 따르세요."""
# 후보에 보유 종목이 있으면 분석 에이전트의 시스템 프롬프트에 — 사는 이야기만 하고 가진 종목의 매도 판단을 건너뛰지 않게
EXIT_RULES = """[보유 종목 매도 타이밍 — 가진 종목은 매번 '지금 팔 때인가'를 따지세요]
- 보유 종목(후보 줄의 '보유 N주')마다 계속 들고 갈 이유와 팔 이유(차익 실현·손절·추세 꺾임·비중 초과·더 나은 종목으로 갈아타기)를
  견주고 views 에 입장을 내세요. 새로 살 종목 이야기만 하고 보유 종목의 매도 판단을 건너뛰지 마세요.
- 팔 때라면 sell. 전부가 아니라 일부(분할 매도)가 맞으면 reason 에 얼마나(예: 절반)인지 쓰세요.
- 계속 들고 간다면(none 또는 buy) reason 에 언제 팔지 — 손절가·목표가 또는 조건(예: 종가로 20일선을 깨면) — 를 함께 쓰세요.
  보유 종목의 none 은 '근거 부족'이 아니라 '지금은 계속 보유'라는 뜻입니다.
- '매도 점검' 줄은 코드가 계산한 사실이고 '신호'는 따져 볼 거리입니다. 신호가 있으면 반드시 다루되 신호만으로 팔지 말고 도구로 확인하세요."""
SELL_SIZING = ("매도는 찬성 의견의 이유에 맞춰 수량을 정하세요 — 손절·추세 꺾임은 전량, 차익 일부 실현·비중 조절은 일부 "
               "(get_trading_limits 의 판매 가능 수량 안에서).")


def _candidates_text(s: Session) -> str:
    lines = []
    for c in s.candidates:
        kind = "ETF" if c.get("security_type") == "ETF" else "주식"
        held = f" · 보유 {c['held']:g}주" if c.get("held") else ""
        why = f" · 후보 이유: {c['why']}" if c.get("why") else ""
        lines.append(f"- {c['name']} ({c['symbol']}) · {kind}{held}{why}")
        if c["symbol"] in s.holdings:
            lines.append(f"  · 매도 점검: {exits.line(s.holdings[c['symbol']])}")
    return "\n".join(lines)


def _held(s: Session) -> bool:
    return any(c.get("held") for c in s.candidates)


def _exit_ask(s: Session) -> str:
    return "보유 종목은 매도 타이밍(지금 팔지, 들고 가면 언제 팔지)도 꼭 판단하세요. " if _held(s) else ""


def system_prompt(agent: dict, s: Session) -> str:
    tools = "\n".join(f"- {t}: {TOOL_INFO[t]['desc']}" for t in agent["tools"])
    who = f" 이름은 {agent['name']}({agent['full_name']}" + (f", {agent['country']}" if agent.get("country") else "") + ")입니다." \
        if agent.get("name") else ""
    desk = (f"[리서치 데스크 — 오늘 시장 분석·섹터·발굴, 참고 자료이니 도구로 확인하고 성향대로 판단하세요]\n{s.brief}\n"
            if s.brief else "")
    exit_rules = f"{EXIT_RULES}\n" if _held(s) and not agent["executor"] else ""
    return (f"당신은 '{agent['title']}' 에이전트입니다.{who} 토론 범위 '{s.label}'의 매매 토론에 참여합니다.\n"
            f"[후보 종목]\n{_candidates_text(s)}\n{_goal_text(s)}{desk}{s.system}[성향]\n{agent['persona']}\n[쓸 수 있는 도구]\n{tools}\n"
            f"{exit_rules}{COMMON_RULES}\n- 참고: {GATE_RULE[s.gate_mode]}")


def _goal_text(s: Session) -> str:
    """운용 목표·이번 회의 안건 블록 (없으면 '')."""
    out = ""
    if s.policy:
        out += (f"[운용 목표 — 관리자가 정한 투자 정책. 성향대로 판단하되 이 목표(수익률·기간·위험·비중)에 맞는지 함께 따지세요]\n"
                f"{s.policy}\n")
    if s.agenda:
        out += f"[이번 회의 안건 — 먼저 다루세요]\n{s.agenda}\n"
    return out


def _notes_text(s: Session) -> str:
    notes = s.notes()
    if not notes:
        return ""
    return (f"\n{ADMIN}(사용자) 의견 — 검토하고 responses 에 to=\"{ADMIN}\"로 답하세요:\n"
            + "\n".join(f"- [{n.get('ts', '')}] {n['text']}" for n in notes) + "\n")


def _statement_text(s: Session, st: dict) -> str:
    lines = [f"[{st['title']}] 요지 — {st['thesis']}"]
    for v in st.get("views", []):
        if v.get("implicit"):
            continue
        lines.append(f"  · {s.name_of(v['symbol'])}({v['symbol']}): {STANCE_KO[v['stance']]} (확신 {v['confidence']:.2f})"
                     + (f" — {v['reason']}" if v.get("reason") else ""))
    lines += [f"  · 근거: {e['fact']} ({e['tool']})" for e in st.get("evidence", [])[:4]]
    if st.get("risks"):
        lines.append(f"  · 위험: {'; '.join(st['risks'][:3])}")
    if st.get("message_to_others"):
        lines.append(f"  · 설득: {st['message_to_others']}")
    for r in st.get("responses", [])[:4]:
        lines.append(f"  · {r['to']}에게 {'동의' if r['agree'] else '반박'}: {r['point']}")
    return "\n".join(lines)


def _distribution(s: Session, stmts: list[dict]) -> str:
    out = []
    for sym in s.symbols:
        stances = [_view(st, sym)["stance"] for st in stmts if not st.get("error")]
        out.append(f"{s.name_of(sym)}: " + ", ".join(f"{STANCE_KO[k]} {stances.count(k)}" for k in STANCES))
    return " / ".join(out)


def round1_prompt(s: Session) -> str:
    return (f"라운드 1 (독립 의견). 다른 에이전트의 의견은 아직 보지 않습니다.\n토론 범위: {s.label}\n"
            f"후보 종목:\n{_candidates_text(s)}\n" + _notes_text(s)
            + "도구로 필요한 자료를 확인한 뒤 후보마다 매수(buy)/매도(sell)/의견 없음(none)과 이유를 views 에 내세요. " + _exit_ask(s)
            + "전체 판단 요지는 thesis 에, 다른 에이전트를 설득할 한마디는 message_to_others 에 남기세요.")


def rebuttal_prompt(s: Session, agent: dict, rnd: int, prev: list[dict]) -> str:
    mine = next((p for p in prev if p["agent"] == agent["key"] and not p.get("error")), None)
    others = [p for p in prev if p["agent"] != agent["key"] and not p.get("error")]
    mine_text = ""
    if mine:
        mine_text = "당신의 직전 입장: " + "; ".join(
            f"{s.name_of(v['symbol'])} {STANCE_KO[v['stance']]}({v['confidence']:.2f})" for v in mine["views"]) + "\n"
    return (f"라운드 {rnd} (설득·반박). 토론 범위: {s.label}\n" + mine_text
            + f"직전 라운드 분포: {_distribution(s, prev)}\n" + _notes_text(s)
            + "\n다른 에이전트 의견:\n" + "\n\n".join(_statement_text(s, p) for p in others)
            + "\n\n할 일: 다른 에이전트(와 관리자)의 주장을 검토하고, 의심스러운 주장은 도구로 사실을 확인하세요. 동의/반박을 responses 에 "
              "이름(to)과 함께 쓰고, message_to_others 로 설득하세요. " + _exit_ask(s)
            + "views 에 모든 후보의 입장을 다시 내되, 설득력 있는 새 근거가 있을 "
              "때만 바꾸고(바꾸면 reason_for_change 필수), 다수에 맞추려고 바꾸지 마세요.")


def trader_prompt(s: Session, transcript: list[dict], gate: dict, needs_approval: bool) -> str:
    final_round = max(t["round"] for t in transcript)
    finals = [t for t in transcript if t["round"] == final_round]
    mode_line = ("토스증권 실계좌 주문입니다 (실제 돈)"
                 + (" · 당신의 주문은 관리자(사용자) 승인 후 제출됩니다" if needs_approval else " · 승인 없이 바로 제출됩니다"))
    opened = "\n".join(f"- {s.name_of(x['symbol'])} ({x['symbol']}): {STANCE_KO[x['direction']]} 방향만 가능"
                       + (f" — {gate['symbols'][x['symbol']]['reason']}" if x["symbol"] in gate.get("symbols", {}) else "")
                       for x in gate["aligned"])
    closed = [f"{s.name_of(sym)} {g['status']}" for sym, g in gate["symbols"].items() if g["status"] != "aligned"]
    return (f"토론 범위: {s.label} · {mode_line}\n" + _goal_text(s) + _notes_text(s)
            + f"게이트가 열린 종목 ({GATE_RULE[s.gate_mode]}):\n{opened}\n"
            + (f"닫힌 종목(주문 불가): {', '.join(closed)}\n" if closed else "")
            + f"\n최종 라운드 의견:\n" + "\n\n".join(_statement_text(s, t) for t in finals)
            + "\n\n할 일: get_portfolio·get_trading_limits·get_quote·get_orderbook 으로 계좌·한도·호가를 확인하고, 열린 종목마다 "
              "그 방향으로 주문할지 행동하지 않을지(hold) 정하세요. 주문한다면 한도와 비중 안에서 수량을 정하고 place_order 를 종목당 한 번 "
              f"호출하세요(symbol 필수, 세션당 최대 {MAX_ORDERS}건, 닫힌 종목·반대 방향은 시스템이 거절). 거절되면 사유를 보고 한 번만 "
              "수량을 줄여 다시 시도할 수 있습니다. " + (SELL_SIZING + " " if any(x["direction"] == "sell" for x in gate["aligned"]) else "")
            + "마지막에 decisions(종목별 buy|sell|hold·quantity)·rationale 을 JSON 으로 답하세요. "
              "rationale 에는 이번에 내는 주문(없으면 관망 이유)만 짧게 — 지난 회의의 주문을 다시 적지 마세요.")


# ---- 게이트 ----
def unanimity_gate(stances: list[str], failed: int = 0) -> dict:
    """한 종목의 모든 최종 입장이 한 방향(그 방향 + 의견 없음)일 때만 aligned."""
    counts = {k: stances.count(k) for k in STANCES}
    if failed:
        return {"status": "incomplete", "direction": None, "counts": counts,
                "reason": f"에이전트 {failed}명의 응답을 받지 못해 행동하지 않습니다"}
    dirs = {x for x in stances if x != "none"}
    if not dirs:
        return {"status": "no_view", "direction": None, "counts": counts, "reason": "모든 에이전트가 의견 없음 — 행동 안 함"}
    if len(dirs) > 1:
        return {"status": "conflict", "direction": None, "counts": counts,
                "reason": f"의견 충돌 (매수 {counts['buy']} · 매도 {counts['sell']}) — 행동 안 함"}
    d = dirs.pop()
    return {"status": "aligned", "direction": d, "counts": counts,
            "reason": f"모두 {STANCE_KO[d]} 또는 의견 없음 (매수 {counts['buy']} · 매도 {counts['sell']} · 의견 없음 {counts['none']})"}


def supermajority_gate(stances: list[str], failed: int = 0) -> dict:
    """과반이 한 방향이고 반대는 응답한 수의 MAX_OPPOSE_SHARE 이하(내림)일 때만 aligned. 응답 실패는 의견 없음처럼 센다."""
    counts = {k: stances.count(k) for k in STANCES}
    n = len(stances) + failed
    allowed = int(n * MAX_OPPOSE_SHARE)
    for d, other in (("buy", "sell"), ("sell", "buy")):
        if counts[d] * 2 > n and counts[other] <= allowed:
            return {"status": "aligned", "direction": d, "counts": counts,
                    "reason": f"{n}명 중 {STANCE_KO[d]} {counts[d]}명(과반) · 반대 {counts[other]}명(허용 {allowed}명)"
                              + (f" · 응답 실패 {failed}명" if failed else "")}
    if not counts["buy"] and not counts["sell"]:
        return {"status": "incomplete" if failed and not stances else "no_view", "direction": None, "counts": counts,
                "reason": "모든 에이전트가 의견 없음 — 행동 안 함" if stances else f"에이전트 {failed}명의 응답을 받지 못했습니다"}
    lead = "buy" if counts["buy"] >= counts["sell"] else "sell"
    why = (f"반대가 {counts['sell' if lead == 'buy' else 'buy']}명으로 허용({allowed}명)보다 많음" if counts[lead] * 2 > n
           else f"{STANCE_KO[lead]} {counts[lead]}명 — {n}명의 과반이 아님")
    return {"status": "conflict", "direction": None, "counts": counts,
            "reason": f"{why} (매수 {counts['buy']} · 매도 {counts['sell']} · 의견 없음 {counts['none']}) — 행동 안 함"}


def _view(st: dict, symbol: str) -> dict:
    return next((v for v in st.get("views", []) if v["symbol"] == symbol),
                {"symbol": symbol, "stance": "none", "confidence": 0.0, "reason": "", "implicit": True})


NOT_HELD = "보유하지 않은 종목이라 팔 것이 없어요 — 매도 의견은 '사지 말자'로 보고 행동 안 함"


def close_unheld_sells(per: dict[str, dict], held: dict[str, float] | None) -> None:
    """보유하지 않은 종목의 매도 쪽 게이트를 닫는다(not_held) — 안 가진 종목의 sell 은 '사지 말자'라 주문 안건이 아니다.
    held 에 없는 종목은 보유를 모르는 것이라 그대로 둔다(계좌를 못 읽었으면 주문 단계의 판매 가능 수량 검사가 막는다)."""
    for sym, g in per.items():
        if g["status"] == "aligned" and g["direction"] == "sell" and held is not None and sym in held and not held[sym]:
            per[sym] = {**g, "status": "not_held", "direction": None, "reason": NOT_HELD}


def session_gate(final: list[dict], symbols: list[str], names: dict[str, str] | None = None, mode: str = "unanimous",
                 held: dict[str, float] | None = None) -> dict:
    """종목마다 게이트(mode: unanimous | supermajority). 열린 종목이 하나라도 있으면 aligned. held(종목 → 보유 수량)를 주면
    보유하지 않은 종목의 매도 쪽은 닫는다."""
    names = names or {}
    ok = [st for st in final if not st.get("error")]
    failed = len(final) - len(ok)
    one = supermajority_gate if mode == "supermajority" else unanimity_gate
    per = {sym: one([_view(st, sym)["stance"] for st in ok], failed) for sym in symbols}
    close_unheld_sells(per, held)
    aligned = [{"symbol": sym, "direction": g["direction"]} for sym, g in per.items() if g["status"] == "aligned"]
    statuses = {g["status"] for g in per.values()}
    if mode == "supermajority":  # 응답 실패는 의견 없음처럼 세므로 실패만으로 닫지 않는다
        status = ("aligned" if aligned else "conflict" if "conflict" in statuses else
                  "incomplete" if "incomplete" in statuses else "not_held" if "not_held" in statuses else "no_view")
    else:
        status = ("aligned" if aligned else "incomplete" if failed else "conflict" if "conflict" in statuses else
                  "not_held" if "not_held" in statuses else "no_view")
    if aligned:
        reason = "게이트 열림: " + ", ".join(f"{names.get(x['symbol'], x['symbol'])} {STANCE_KO[x['direction']]}" for x in aligned)
        rest = len(symbols) - len(aligned)
        if rest:
            reason += f" (나머지 {rest}종목은 닫힘)"
    elif len(symbols) == 1:
        reason = per[symbols[0]]["reason"]
    else:
        reason = {"incomplete": f"에이전트 {failed}명의 응답을 받지 못해 행동하지 않습니다",
                  "conflict": "열린 종목 없음 — 의견 충돌이 있어 행동 안 함",
                  "not_held": "열린 종목 없음 — 매도 쪽은 보유하지 않은 종목뿐이에요('사지 말자')",
                  "no_view": "열린 종목 없음 — 모두 의견 없음"}[status]
    single = per[symbols[0]] if len(symbols) == 1 else None
    return {"status": status, "symbols": per, "aligned": aligned, "reason": reason, "mode": mode,
            "direction": single["direction"] if single else None, "counts": single["counts"] if single else None}


# ---- 실행 ----
AgentLlm = Callable[..., Awaitable[dict]]


async def _default_llm(feature: str, system: str, prompt: str, schema: dict, tools: list[ToolDef],
                       model: str | None, max_turns: int) -> dict:
    return await agentic(feature, prompt, schema, tools=tools, model=model, system_prompt=system, max_turns=max_turns)


LLM: AgentLlm = _default_llm  # 테스트에서 가짜로 바꾼다


def _conf(v: object) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _strs(v: object, n: int = 6) -> list[str]:
    return [str(x).strip() for x in v or [] if str(x).strip()][:n] if isinstance(v, list) else []


def normalize_statement(raw: dict, agent: dict, rnd: int, prev: dict | None, symbols: list[str]) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    given: dict[str, dict] = {}
    for v in raw.get("views") or []:
        if isinstance(v, dict) and str(v.get("symbol") or "").strip() in symbols:
            given.setdefault(str(v["symbol"]).strip(), v)
    if not given and len(symbols) == 1 and raw.get("stance") in STANCES:  # 단일 종목 옛 형식
        given[symbols[0]] = {"stance": raw["stance"], "confidence": raw.get("confidence"), "reason": ""}
    views = []
    for sym in symbols:
        v = given.get(sym)
        views.append({"symbol": sym, "stance": v.get("stance") if v and v.get("stance") in STANCES else "none",
                      "confidence": _conf(v.get("confidence")) if v else 0.0,
                      "reason": str((v or {}).get("reason") or "").strip(), "implicit": v is None})
    prev_views = {v["symbol"]: v["stance"] for v in prev["views"]} if prev and not prev.get("error") else {}
    changed = [v["symbol"] for v in views if v["symbol"] in prev_views and prev_views[v["symbol"]] != v["stance"]]
    explicit = [v for v in views if not v["implicit"]] or views
    stances = {v["stance"] for v in views}
    evidence = [{"fact": str(e.get("fact") or "").strip(), "tool": str(e.get("tool") or "").strip()}
                for e in raw.get("evidence") or [] if isinstance(e, dict) and str(e.get("fact") or "").strip()][:6]
    responses = [{"to": str(r.get("to") or "").strip(), "agree": bool(r.get("agree")), "point": str(r.get("point") or "").strip()}
                 for r in raw.get("responses") or [] if isinstance(r, dict) and str(r.get("point") or "").strip()][:6]
    return {"round": rnd, "agent": agent["key"], "title": agent["title"], "views": views,
            "stance": views[0]["stance"] if len(views) == 1 else (stances.pop() if len(stances) == 1 else "mixed"),
            "confidence": sum(v["confidence"] for v in explicit) / len(explicit),
            "thesis": str(raw.get("thesis") or "").strip(), "evidence": evidence, "risks": _strs(raw.get("risks")),
            "message_to_others": str(raw.get("message_to_others") or "").strip(), "responses": responses,
            "changed": bool(changed), "changed_symbols": changed,
            "previous": {sym: prev_views[sym] for sym in changed},
            "previous_stance": prev_views.get(symbols[0]) if len(symbols) == 1 and changed else None,
            "reason_for_change": str(raw.get("reason_for_change") or "").strip(), "error": None}


def normalize_decision(raw: dict, s: Session, executor: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    items = raw.get("decisions")
    if not isinstance(items, list) and raw.get("decision"):  # 옛 형식 (단일 종목)
        items = [{"symbol": s.symbols[0], "decision": raw.get("decision"), "quantity": raw.get("quantity")}]
    decisions = []
    for d in items or []:
        if not isinstance(d, dict) or str(d.get("symbol") or "").strip() not in s.symbols:
            continue
        dec = d.get("decision") if d.get("decision") in ("buy", "sell", "hold") else "hold"
        decisions.append({"symbol": d["symbol"], "name": s.name_of(d["symbol"]), "decision": dec,
                          "quantity": int(d.get("quantity") or 0) if dec != "hold" else 0})
    acting = [d for d in decisions if d["decision"] != "hold"]
    kinds = {d["decision"] for d in acting}
    return {"decisions": decisions, "decision": "hold" if not acting else (kinds.pop() if len(kinds) == 1 else "mixed"),
            "quantity": sum(d["quantity"] for d in acting), "rationale": str(raw.get("rationale") or "").strip(),
            "risk_notes": _strs(raw.get("risk_notes")), "by": executor["key"]}


@dataclass
class SessionResult:
    transcript: list[dict]
    gate: dict
    decision: dict
    orders: list[dict]
    order_errors: dict[str, str]
    tool_log: list[dict]
    stance_changes: int


async def run_session(s: Session, roster: list[dict], *, rounds: int = 2, model: str | None = None,
                      max_turns: int = 10, on_statement: Callable[[dict], None] | None = None,
                      check_cancel: Callable[[], None] | None = None) -> SessionResult:
    active = [a for a in roster if a["enabled"]]
    analysts = [a for a in active if not a["executor"]]
    executor = next(a for a in active if a["executor"])
    s.titles.update({a["key"]: a["title"] for a in active})
    turns = max_turns + 2 * (len(s.symbols) - 1)  # 후보가 많으면 조회할 것이 많다
    transcript: list[dict] = []

    async def speak(agent: dict, rnd: int, prev: list[dict]) -> dict:
        mine = next((p for p in prev if p["agent"] == agent["key"]), None)
        prompt = round1_prompt(s) if rnd == 1 else rebuttal_prompt(s, agent, rnd, prev)
        schema = OPINION_SCHEMA if rnd == 1 else REBUTTAL_SCHEMA
        try:
            raw = await LLM("agent_action", system_prompt(agent, s), prompt, schema,
                            build_tools(s, agent["tools"], agent["key"]), model, turns)
            st = normalize_statement(raw, agent, rnd, mine, s.symbols)
        except LLMError as e:
            st = normalize_statement({}, agent, rnd, None, s.symbols)
            st.update(error=str(e)[:300])
        if on_statement:
            on_statement(st)
        s.emit({"type": "statement", **st})
        return st

    prev: list[dict] = []
    for rnd in range(1, max(1, rounds) + 1):
        if check_cancel:
            check_cancel()
        s.emit({"type": "round", "round": rnd, "label": "독립 의견" if rnd == 1 else "설득·반박",
                "agents": [a["key"] for a in analysts]})
        stmts = await asyncio.gather(*(speak(a, rnd, prev) for a in analysts))
        transcript.extend(stmts)
        prev = list(stmts)

    held = {c["symbol"]: c["held"] or 0.0 for c in s.candidates if "held" in c}  # 계좌를 못 읽은 후보는 held 가 없다(모름)
    gate = session_gate(prev, s.symbols, {c["symbol"]: c["name"] for c in s.candidates}, s.gate_mode, held)
    s.gate = gate
    s.emit({"type": "gate", "gate": gate})
    changes = sum(len(t["changed_symbols"]) for t in transcript)
    if gate["status"] != "aligned":
        decision = {"decisions": [], "decision": "hold", "quantity": 0, "rationale": gate["reason"], "by": "gate",
                    "risk_notes": []}
        s.emit({"type": "decision", "decision": decision})
        return SessionResult(transcript, gate, decision, [], {}, s.tool_log, changes)

    if check_cancel:
        check_cancel()
    needs_approval = not (s.auto_submit or config.agent_auto_approve(s.conn))
    s.emit({"type": "trader_start", "agent": executor["key"], "title": executor["title"]})
    try:
        raw = await LLM("agent_action", system_prompt(executor, s), trader_prompt(s, transcript, gate, needs_approval),
                        TRADER_SCHEMA, build_tools(s, executor["tools"], executor["key"]), model, turns + 4)
        decision = normalize_decision(raw, s, executor)
    except (LLMError, TypeError, ValueError) as e:
        decision = {"decisions": [], "decision": "hold", "quantity": 0,
                    "rationale": f"실행 담당 응답 실패 — 행동 안 함 ({e})"[:300], "by": "error", "risk_notes": []}
    missing = [d for d in decision["decisions"] if d["decision"] != "hold" and d["symbol"] not in s.orders]
    if missing:
        decision["note"] = "주문을 결정했지만 만들어지지 않은 종목: " + ", ".join(
            d["name"] + (f" ({s.order_errors[d['symbol']]})" if d["symbol"] in s.order_errors else "") for d in missing)
    held_back = [sym for sym in s.orders if not any(d["symbol"] == sym and d["decision"] != "hold" for d in decision["decisions"])]
    if held_back:
        decision["note"] = (decision.get("note", "") + " · " if decision.get("note") else "") + \
            "주문한 뒤 관망으로 답한 종목이 있습니다 — 주문 기록을 확인하세요"
    s.emit({"type": "decision", "agent": executor["key"], "title": executor["title"], "decision": decision})
    if on_statement:
        on_statement({"round": max(t["round"] for t in transcript) + 1, "agent": executor["key"], "title": executor["title"],
                      "views": [], "stance": {"buy": "buy", "sell": "sell"}.get(decision["decision"], "none"),
                      "confidence": 1.0, "thesis": decision["rationale"], "evidence": [], "risks": decision["risk_notes"],
                      "message_to_others": "", "responses": [], "changed": False, "changed_symbols": [],
                      "previous": {}, "previous_stance": None, "reason_for_change": "", "error": None, "executor": True})
    return SessionResult(transcript, gate, decision, list(s.orders.values()), dict(s.order_errors), s.tool_log, changes)
