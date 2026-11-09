"""상시 운용 — 정기 회의 없이, 장이 열려 있는 동안 에이전트들이 한 채널에서 계속 이야기하고 트레이더는 언제든 주문한다
(자동 운용 mode=continuous). 하루에 실행 하나(agent_action, params.stream) — 채팅방에는 그날의 긴 대화방으로 보인다.

- 언제 말하나(TICK_SEC 마다 확인, 한 번에 하나, 우선순위 순): 관리자·비서의 말 → 리스크 경보(운용 데스크) → 트레이더 제안 →
  리서치 갱신(후보를 다시 고름) → 볼 종목 급변(마지막으로 이야기한 가격보다 MOVE 넘게, 보유 종목 먼저 잼) → 보유 종목 매도 신호
  (EXIT_CHECK_MIN 분마다 exits.checks 로 재서 새 신호가 뜬 종목, 종목·신호마다 하루 한 번) → 트레이더 점검(TRADER_PULSE_MIN 마다) →
  조용하면 점검(PULSE_MIN 마다). 대화 사이는 MIN_GAP_SEC 초(관리자 말은 바로).
- 대화 하나 = 계기 하나: 계기에 맞는 에이전트 + 오래 말하지 않은 순으로 SPEAKERS 명이, 최근 대화(RECENT 발언)·입장판·계기를 보고
  볼 종목(FOCUS 개까지 — 계기의 종목, 나머지는 오래 이야기하지 않은 순)마다 입장(views)과 동의/반박(responses)을 낸다. 채팅방 구분선은 '시각 · 계기'.
- 보유 종목은 후보에 없어도 볼 종목에 더한다(국내만, 그래야 급변을 보고 입장이 모여 손절할 수 있다). 수량은 대화마다 지금 계좌로.
  대화마다 보유 종목 한 자리(오래 이야기하지 않은 보유 종목)를 두고, 보유 종목에는 매도 점검(평단·손익·비중·지표·신호)이 붙어
  에이전트가 매도 타이밍을 따진다(agents.EXIT_RULES). 트레이더 점검도 보유 종목부터 본다.
- 입장판(Board): 에이전트·종목별 최신 입장과 표의 무게(track.weight = 확신 × 지난 판단 성과). STALE_MIN 분이 지나면 빠진다.
  의견 없음은 무게가 없다 — 머릿수의 분모에 들어가 반대처럼 작동하지 않게.
- 트레이더(언제든): 대화가 끝날 때마다 입장판을 본다 — 주요 의견 무게가 PROPOSE_MIN 이상이고 반대가 AGAINST_MAX 이하면 주문 안건이 된다.
  승인 없이 제출, 같은 종목·같은 방향은 COOLDOWN_MIN 분에 한 번(반대 방향은 막지 않음 — 산 뒤 바로 손절할 수 있게), 하루 MAX_DAY_ORDERS 건.
  트레이더 점검 때는 계좌·입장판을 보고 스스로 사거나 팔 종목을 내놓을 수 있고 → 그 종목으로 바로 대화가 열려 입장판이 받쳐 주면 주문한다.
- 필수 의견(주문 전): 트레이더에게 넘기기 전에 그 종목의 찬반이 없는 분석 에이전트 전원에게 묻는다. 의견 없음은 받지 않고 POLL_TRIES 번까지
  묻되, 근거가 약하면 낮은 확신으로 답하게 한다(무게가 작아 막지 않는다). 최종 판단(Board.final, place_order 도구가 검사): 매수 = 전원 응답 +
  주요 의견 FOR_MIN 이상 + 반대 AGAINST_MAX 이하, 답이 없으면 닫힌다. 매도 = 응답한 사람 기준 주요 의견 FOR_MIN 이상 + 반대보다 무거움 —
  위험을 줄이는 주문은 응답 실패·소수 반대로 막지 않는다. 열리면 트레이더는 그 방향으로 주문한다(관망은 주문이 불가능할 때만).
- 리서치 담당(입장 없이 참여): 발언의 '탄디: …' 처럼 이름을 앞에 쓴 부탁에 그 담당이 도구로 확인해 채널에 답하고(ASK_GAP_MIN 분에 한 번),
  타당하면 종목을 볼 종목에 올린다(하루 MAX_WATCH 개).
- 끝: 자동 판단 시간이 끝나면(마감 15분 전) 하루 회의록, 자동 운용이 꺼지거나 방식이 바뀌면 그때 마친다.
- 주문 한도·전체 중지·주문 조건·비중 검사는 그대로(trading.place_order). 주문 조건이 안 맞거나 전체 중지면 트레이더는 쉰다.
"""

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.invest.advice import assistant
from app.invest.analysis import exits, policy, screening
from app.invest.broker import config, trading
from app.invest.broker.toss import TossError
from app.invest.broker.trading import TradeError
from app.invest.debate import agents, scopes, track
from app.invest.marketdata import market
from app.invest.marketdata.market import MarketError
from app.invest.ops import autopilot, desk
from app.llm.client import LLMError
from app.llm.routes import as_agent
from app.system import issues

TICK_SEC = 20
PULSE_MIN = 20
TRADER_PULSE_MIN = 40
MIN_GAP_SEC = 60
SPEAKERS = 3
FOCUS = 4
RECENT = 8
STALE_MIN = 120
PROPOSE_MIN = 1.5    # 주문 안건을 올리는 주요 의견 무게 (0.5 확신 = 1.0)
FOR_MIN = 2.0        # 주문하는 주요 의견 무게 — 평범한 기준
AGAINST_MAX = 1.0    # 허용하는 반대 무게 — 보수적 기준 (보통 확신 1명까지)
MOVE = 0.02
EXIT_CHECK_MIN = 5   # 보유 종목 매도 신호를 이만큼마다 다시 잰다
COOLDOWN_MIN = 30
MAX_DAY_ORDERS = 6
POLL_TRIES = 2
POLL_TURNS = 4
ASK_GAP_MIN = 10
MAX_WATCH = 5
RESEARCHERS = ("strategist", "sector", "scout")
RESEARCH_TOOLS = ["get_quote", "get_technicals", "get_company_info", "get_valuation", "search_news", "get_market_overview"]
RESEARCH_SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}, "watch": {"type": "array", "items": {"type": "string"}}},
                   "required": ["reply", "watch"]}
KIND_KO = {"user": "관리자 말", "risk": "리스크 경보", "trader": "트레이더 제안", "research": "리서치 갱신", "move": "급변",
           "exit": "매도 신호", "pulse": "점검"}
PREFER = {"risk": ("risk", "portfolio"), "move": ("technical", "growth"), "research": ("news", "value"), "trader": ("risk", "portfolio"),
          "exit": ("risk", "technical")}
SLEEP: Callable[[float], None] = time.sleep  # 테스트에서 시계를 돌린다


@dataclass
class Board:
    """에이전트·종목별 최신 입장 — {agent: {symbol: {stance, confidence, weight, reason, at, title}}}. skill = 에이전트별 성과 배수."""
    views: dict[str, dict[str, dict]] = field(default_factory=dict)
    skill: dict[str, float] = field(default_factory=dict)

    def update(self, st: dict, now: datetime) -> None:
        if st.get("error") or st.get("executor"):
            return
        for v in st.get("views") or []:
            if not v.get("implicit"):
                self.views.setdefault(st["agent"], {})[v["symbol"]] = {
                    "stance": v["stance"], "confidence": v["confidence"], "weight": track.weight(v["confidence"], self.skill.get(st["agent"], 1.0)),
                    "reason": v.get("reason") or "", "at": now, "title": st["title"]}

    def stance(self, agent: str, symbol: str, now: datetime) -> str | None:
        v = self.views.get(agent, {}).get(symbol)
        return v["stance"] if v and now - v["at"] <= timedelta(minutes=STALE_MIN) else None

    def weighed(self, symbol: str, now: datetime) -> tuple[dict[str, float], dict[str, int]]:
        """그 종목의 무게 합·머릿수 — 의견 없음은 무게가 없다."""
        vs = self.fresh(symbol, now)
        return ({k: round(sum(v["weight"] for v in vs if v["stance"] == k), 2) for k in ("buy", "sell")},
                {k: sum(v["stance"] == k for v in vs) for k in agents.STANCES})

    def final(self, symbol: str, direction: str, voters: list[dict], now: datetime) -> dict:
        """주문 전 최종 판단 — 필수 의견을 받은 뒤. 매수는 전원 응답 + 주요 의견 FOR_MIN 이상 + 반대 AGAINST_MAX 이하,
        매도는 주요 의견 FOR_MIN 이상 + 반대보다 무거움."""
        w, counts = self.weighed(symbol, now)
        main, other = w[direction], w["buy" if direction == "sell" else "sell"]
        row = {"status": "conflict", "direction": None, "counts": counts, "weights": w}
        ko = agents.STANCE_KO[direction]
        if direction == "sell":
            if main >= FOR_MIN and main > other:
                return {**row, "status": "aligned", "direction": "sell", "reason": f"매도 무게 {main:g} ≥ {FOR_MIN:g} · 반대 {other:g}보다 무거움"}
            return {**row, "reason": f"매도 무게 {main:g} · 반대 {other:g} — 매도 기준({FOR_MIN:g} 이상, 반대보다 무거움)에 못 미쳐요"}
        missing = [a["title"] for a in voters if self.stance(a["key"], symbol, now) not in ("buy", "sell")]
        if missing:
            return {**row, "status": "incomplete", "reason": f"{', '.join(missing)}의 찬반이 없어 매수하지 않아요"}
        if main >= FOR_MIN and other <= AGAINST_MAX:
            return {**row, "status": "aligned", "direction": direction, "reason": f"{ko} 무게 {main:g} ≥ {FOR_MIN:g} · 반대 {other:g} ≤ {AGAINST_MAX:g}"}
        return {**row, "reason": f"{ko} 무게 {main:g} · 반대 {other:g} — 기준({FOR_MIN:g} 이상 · 반대 {AGAINST_MAX:g} 이하)에 못 미쳐요"}

    def fresh(self, symbol: str, now: datetime) -> list[dict]:
        return [v for vs in self.views.values() if (v := vs.get(symbol)) and now - v["at"] <= timedelta(minutes=STALE_MIN)]

    def symbols(self, now: datetime) -> list[str]:
        out: list[str] = []
        for vs in self.views.values():
            out += [s for s, v in vs.items() if now - v["at"] <= timedelta(minutes=STALE_MIN) and s not in out]
        return out

    def gate(self, symbols: list[str], names: dict[str, str], now: datetime, held: dict[str, float] | None = None) -> dict:
        """주문 안건 — 주요 의견 무게 PROPOSE_MIN 이상 + 반대 AGAINST_MAX 이하인 종목 (최종 판단은 필수 의견을 받은 뒤 final).
        held(종목 → 보유 수량)를 주면 보유하지 않은 종목의 매도 쪽은 안건에서 뺀다(agents.close_unheld_sells)."""
        per = {}
        for sym in symbols:
            w, counts = self.weighed(sym, now)
            d, other = ("buy", "sell") if w["buy"] >= w["sell"] else ("sell", "buy")
            row = {"status": "conflict", "direction": None, "counts": counts, "weights": w}
            if not w[d]:
                per[sym] = {**row, "status": "no_view", "reason": "매수·매도 의견이 없어요"}
            elif w[d] >= PROPOSE_MIN and w[other] <= AGAINST_MAX:
                per[sym] = {**row, "status": "aligned", "direction": d,
                            "reason": f"{agents.STANCE_KO[d]} 무게 {w[d]:g} ≥ {PROPOSE_MIN:g} · 반대 {w[other]:g} ≤ {AGAINST_MAX:g}"}
            else:
                per[sym] = {**row, "reason": f"{agents.STANCE_KO[d]} 무게 {w[d]:g} · 반대 {w[other]:g} — 안건 기준({PROPOSE_MIN:g} 이상 · 반대 {AGAINST_MAX:g} 이하)에 못 미쳐요"}
        agents.close_unheld_sells(per, held)
        aligned = [{"symbol": sym, "direction": g["direction"]} for sym, g in per.items() if g["status"] == "aligned"]
        reason = ("열림: " + ", ".join(f"{names.get(x['symbol'], x['symbol'])} {agents.STANCE_KO[x['direction']]}" for x in aligned)
                  if aligned else "입장판이 받쳐 주는 종목이 없어요")
        return {"status": "aligned" if aligned else "conflict", "symbols": per, "aligned": aligned, "reason": reason,
                "mode": "rolling", "direction": None, "counts": None}

    def text(self, symbols: list[str], names: dict[str, str], now: datetime) -> str:
        lines = []
        for sym in symbols:
            vs = self.fresh(sym, now)
            if not vs:
                continue
            c = {k: [v["title"] for v in vs if v["stance"] == k] for k in agents.STANCES}
            lines.append(f"- {names.get(sym, sym)}({sym}): " + " · ".join(f"{agents.STANCE_KO[k]} {len(c[k])}"
                                                                          + (f"({', '.join(c[k])})" if c[k] else "") for k in agents.STANCES)
                         + f" · 무게 매수 {sum(v['weight'] for v in vs if v['stance'] == 'buy'):.1f}·매도 {sum(v['weight'] for v in vs if v['stance'] == 'sell'):.1f}"
                         + f" — 가장 최근 {max(v['at'] for v in vs):%H:%M}")
        return "\n".join(lines) or "아직 없음"


SYSTEM_ASK = re.compile(rf"\s*(?:시스템(?:\s*확인)?(?:\s*요청)?|{issues.PROFILE['name']})\s*[:：,]\s*(.+)")


def system_asks(stmts: list[dict]) -> list[tuple[str, str]]:
    """발언에서 시스템 관리자에게 넘길 말을 꺼낸다 — message_to_others 의 '시스템: …' 문단 → [(누가, 내용)]."""
    out = []
    for st in stmts:
        for part in (st.get("message_to_others") or "").split("\n"):
            m = SYSTEM_ASK.match(part)
            if m:
                out.append((st["title"], m.group(1).strip()))
    return out


def research_asks(stmts: list[dict]) -> dict[str, list[str]]:
    """발언에서 리서치 담당에게 건 부탁을 꺼낸다 — message_to_others 의 '탄디: …' 문단 → {담당 키: ['누가: 부탁']}."""
    who = {desk.PEOPLE[k]["name"]: k for k in RESEARCHERS}
    out: dict[str, list[str]] = {}
    for st in stmts:
        for part in (st.get("message_to_others") or "").split("\n"):
            m = re.match(rf"\s*({'|'.join(who)})\s*[:：,]\s*(.+)", part)
            if m:
                out.setdefault(who[m.group(1)], []).append(f"{st['title']}: {m.group(2)[:300]}")
    return out


def research_system(who: dict) -> str:
    return (f"당신은 리서치 데스크의 {who['title']} {who['name']}입니다. 맡은 일: {who['role']}\n상시 운용 채널에서 에이전트들이 부탁한 일에 답합니다. "
            "매수·매도 입장은 내지 않습니다.\n- 도구로 확인한 사실만 쓰세요. 도구에 없는 자료(ETF 구성 비중·총보수, 증권사 컨센서스, 공시 원문 등)는 "
            "'자료가 없어요'라고 분명히 답하고 지어내지 마세요.\n- 볼 종목에 올려 달라는 부탁이 타당하면 watch 에 6자리 종목 코드를 넣으세요. 아니면 비워 두세요.\n"
            "- 누구의 부탁에 답하는지 밝히고, 한국어로 짧게. 응답은 주어진 JSON 스키마를 정확히 따르세요.")


def poll_prompt(s: agents.Session, rnd: int, aligned: list[dict], board: str, answers: list[str], why: str) -> str:
    lines = "\n".join(f"- {s.name_of(x['symbol'])}({x['symbol']}) {agents.STANCE_KO[x['direction']]} 안건 — 찬성은 {x['direction']}, 반대는 "
                      + ("sell(보유하지 않았다면 '사지 말자')" if x["direction"] == "buy" else "buy('팔지 말자')") for x in aligned)
    return (f"라운드 {rnd} — 필수 의견입니다. 트레이더가 곧 아래 주문을 낼 수 있어 분석 에이전트 전원의 판단이 필요합니다. 계기: {why}\n{lines}\n"
            f"입장판:\n{board}\n" + _answers_text(answers)
            + "\n할 일: 도구로 확인하고 종목마다 views 에 찬성 또는 반대를 내세요. 이번에는 의견 없음(none)을 받지 않습니다. confidence 가 표의 무게입니다 — "
              "구체적인 이유로 막아야 하면 강하게(0.6 이상), 판단 근거가 부족할 뿐이면 더 가까운 쪽을 고르고 0.2 이하로 답하세요. 가격 조건이 있으면 reason 에 적으세요.")


def _answers_text(answers: list[str]) -> str:
    return "\n리서치 담당의 답:\n" + "\n".join(f"- {a}" for a in answers[-3:]) + "\n" if answers else ""


def stream_prompt(s: agents.Session, rnd: int, trig: dict, recent: list[dict], board: str, answers: list[str] = ()) -> str:
    talk = "\n\n".join(agents._statement_text(s, st) for st in recent) or "아직 없음"
    return (f"라운드 {rnd} — 상시 운용 채널의 대화입니다 (정기 회의 없이 계속 이야기해요). 계기: {trig['text']}\n"
            f"볼 종목:\n{agents._candidates_text(s)}\n" + agents._notes_text(s)
            + f"\n지금 입장판 (최근 {STALE_MIN}분, 에이전트별 최신 입장):\n{board}\n{_answers_text(list(answers))}\n최근 대화:\n{talk}\n\n"
              "할 일: 계기와 관련된 자료를 도구로 확인하고, 볼 종목마다 매수(buy)/매도(sell)/의견 없음(none)과 이유를 views 에 내세요. 지금 사거나 팔 이유가 보이면 "
              "조건을 달아 미루지 말고 buy·sell 로 내세요 — none 은 정말 움직일 이유가 없을 때만. " + agents._exit_ask(s)
            + "confidence 는 표의 무게입니다: 0.3 약함·0.5 보통·0.7 강함·0.9 매우 강함. "
              "지난 판단의 성과도 무게에 곱해지니 근거만큼만 강하게 내세요. "
              "이전 입장을 바꾸면 reason_for_change 필수, 다수에 맞추려고 바꾸지 마세요. 다른 에이전트·관리자 말에 동의/반박은 responses 에, "
              "트레이더·다른 에이전트에게 할 말은 message_to_others 에. 리서치 담당(올리비아 시장·닐스 섹터·탄디 종목)에게 부탁할 일은 message_to_others 에 "
              "'탄디: 부탁' 처럼 이름을 문단 맨 앞에 쓰면 그 담당이 답합니다(볼 종목에 올릴 때는 6자리 코드를 함께). 시스템·도구·데이터 문제는 '시스템: 내용' 문단으로 쓰면 "
              "시스템 관리자가 맡습니다 — 위 [시스템 이슈]에 이미 있는 건은 다시 쓰지 마세요. "
              f"주요 의견 무게가 {PROPOSE_MIN:g} 이상·반대 {AGAINST_MAX:g} 이하로 모이면 주문 안건이 되고, 전원의 찬반을 받아 {FOR_MIN:g} 이상이면 그 방향으로 주문합니다.")


def trader_review_prompt(s: agents.Session, board: str, orders_left: int) -> str:
    return (f"라운드 0 — 트레이더 점검 (상시 운용). 지금 계좌·보유·입장판을 보고, 지금 사거나 팔아야 할 종목이 있으면 views 에 그 방향을 "
            f"내세요(예: 목표 비중까지 남은 현금, 차익 실현, 손실 한도 접근). 보유 종목은 '매도 점검' 줄(평단·손익·비중·신호)을 보고 지금 팔 때인지 "
            f"(차익 실현·손절·추세 꺾임·비중 초과, 전량 또는 일부) 꼭 따지세요. 그 종목으로 분석 에이전트들과 바로 대화가 열리고 입장판이 받쳐 "
            f"주면 주문합니다. 없으면 views 를 비우세요.\n볼 종목:\n{agents._candidates_text(s)}\n입장판:\n{board}\n"
            f"오늘 남은 주문 {orders_left}건. thesis 에 판단 요지를, message_to_others 에 분석 에이전트에게 부탁할 것을 쓰세요.")


def trade_prompt(s: agents.Session, gate: dict, board: str, why: str, orders_left: int) -> str:
    opened = "\n".join(f"- {s.name_of(x['symbol'])} ({x['symbol']}): {agents.STANCE_KO[x['direction']]} 방향만 — "
                       f"{gate['symbols'][x['symbol']]['reason']}" for x in gate["aligned"])
    return (f"상시 운용 · 토스증권 실계좌 · 승인 없이 바로 제출됩니다. 계기: {why}\n" + agents._goal_text(s) + agents._notes_text(s)
            + f"무게 기준을 넘긴 종목 (분석 에이전트 전원의 필수 의견까지 받은 결과):\n{opened}\n"
            + f"\n입장판:\n{board}\n\n할 일: get_portfolio·get_trading_limits·get_quote·get_orderbook 으로 계좌·한도·호가를 확인하고, 위 종목은 그 방향으로 주문하세요. "
              f"무게 기준을 넘긴 결정이라 수량·가격은 정하되 관망(hold)은 한도·현금·호가·찬성 의견의 가격 조건 때문에 주문이 불가능할 때만 그 이유와 함께 고르세요. "
              f"주문은 place_order 로 종목당 한 번(오늘 남은 {orders_left}건, 같은 종목·방향은 {COOLDOWN_MIN}분에 한 번). "
            + (agents.SELL_SIZING + " " if any(x["direction"] == "sell" for x in gate["aligned"]) else "")
            + f"운용 목표(주식 비중·종목 비중·손실 한도)를 지키세요. 마지막에 decisions·rationale 을 JSON 으로 — "
              "rationale 에는 이번에 내는 주문(없으면 관망 이유)만, 앞서 낸 주문은 다시 적지 마세요.")


class Stream:
    def __init__(self, ctx, chat, p, model: str | None, roster: list[dict], max_candidates: int, run_async: Callable,
                 pick: Callable):
        self.ctx, self.chat, self.p, self.model = ctx, chat, p, model
        self.conn = ctx.conn
        self.run_async = run_async
        self.pick = pick
        self.max_candidates = max_candidates
        self.active = [a for a in roster if a["enabled"]]
        self.analysts = [a for a in self.active if not a["executor"]]
        self.executor = next(a for a in self.active if a["executor"])
        self.board = Board()
        self.candidates: list[dict] = []
        self.names: dict[str, str] = {}
        self.transcript: list[dict] = []
        self.spoke: dict[str, datetime] = {}
        self.prices: dict[str, float] = {}
        self.queue: list[dict] = []
        self.orders: list[dict] = []
        self.last_order: dict[tuple[str, str], datetime] = {}
        self.order_gates: dict[str, dict] = {}
        self.talked: dict[str, datetime] = {}
        self.asked: dict[str, datetime] = {}
        self.answers: list[str] = []
        self.watched: list[dict] = []
        self.scores: dict[str, dict] = {}
        self.notes_seen = 0
        self.desk_seen = 0
        self.ideas_seen = 0
        self.n = 0
        self.last_talk: datetime | None = None
        self.last_trader: datetime | None = None
        self.decisions: list[dict] = []
        self.holdings: list[dict] = []                          # 지금 계좌의 국내 보유 (_sync_held)
        self.held_read = False                                  # 마지막 _sync_held 가 계좌를 읽었는지 — 못 읽었으면 보유를 모른다
        self.checks: dict[str, dict] = {}                       # 마지막으로 잰 보유 종목 매도 점검 (exits.checks)
        self.exit_seen: set[tuple[str, str]] = set()            # 대화를 연 (종목, 신호) — 하루 한 번
        self.exit_at: datetime | None = None

    # ---- 후보 ----
    def refresh(self, notes: list[str]) -> None:
        texts = [i["text"] for i in autopilot.intents(self.conn)] + notes
        try:
            scope = scopes.resolve(self.conn, self.p, {"scope": "autopilot", "intents": texts, "run_id": self.ctx.run_id}, self.max_candidates, self.pick)
        except scopes.ScopeError as e:
            self.ctx.log(f"후보를 고르지 못했어요: {e}", "warn")
            return
        known = {c["symbol"] for c in scope["candidates"]}
        self.candidates = scope["candidates"] + [c for c in self.watched if c["symbol"] not in known]
        self.names.update({c["symbol"]: c["name"] for c in self.candidates})
        sc = scope.get("screening")
        if sc:
            pm = next((a for a in self.active if a["key"] == "portfolio"), None)
            self.chat({"type": "screening", "pool": sc["pool"], "rows": sc["rows"][:15], "picks": sc["picks"], "summary": sc["summary"],
                       "by": sc["by"], "picker": f"{pm['name']}({pm['title']})" if pm and sc["by"] == "llm" else None})
        self._sync_held()

    def _sync_held(self) -> dict[str, float]:
        """지금 보유 수량으로 맞추고(후보를 고른 뒤 사고판 것까지) 후보에 없는 국내 보유는 더한다. 못 읽으면 적혀 있던 수량 그대로."""
        try:
            rows = trading.account(self.conn, market=self.p).summary()["holdings"]
        except (TradeError, MarketError, TossError) as e:
            self.ctx.log(f"보유 종목을 불러오지 못했어요: {e}", "warn")
            self.held_read = False
            return {c["symbol"]: c.get("held", 0.0) for c in self.candidates}
        rows = [h for h in rows if h.get("currency") in (None, "KRW")]
        self.holdings, self.held_read = rows, True
        held = {h["symbol"]: h["quantity"] for h in rows}
        for c in self.candidates:
            c["held"] = held.get(c["symbol"], 0.0)
        known = {c["symbol"] for c in self.candidates}
        self.candidates += [{"symbol": h["symbol"], "name": h["name"] or h["symbol"], "security_type": "", "held": h["quantity"], "why": "보유 종목"}
                            for h in rows if h["symbol"] not in known]
        self.names.update({c["symbol"]: c["name"] for c in self.candidates})
        return held

    def _held_of(self, symbols: list[str]) -> dict[str, float] | None:
        """게이트용 보유 수량 — 지금 계좌로(없는 종목은 0). 계좌를 못 읽었으면 None(모름 → 매도 쪽을 닫지 않는다)."""
        held = self._sync_held()
        return {sym: held.get(sym, 0.0) for sym in symbols} if self.held_read else None

    def _focus(self, symbols: list[str]) -> list[dict]:
        held = self._sync_held()
        by = {c["symbol"]: c for c in self.candidates}
        out = []
        for sym in symbols:
            if sym in by:
                out.append(by[sym])
            elif sym.isdigit() and len(sym) == 6:
                out.append({"symbol": sym, "name": self.names.get(sym) or sym, "security_type": "", "held": held.get(sym, 0.0), "why": "이번 계기"})
        never = datetime.min.replace(tzinfo=market.KST)
        stale = lambda c: self.talked.get(c["symbol"], never)  # noqa: E731
        mine = [c for c in self.candidates if c.get("held")]
        if mine and not any(c.get("held") for c in out):  # 보유 종목 한 자리 — 대화마다 가진 종목 하나는 매도 타이밍을 본다
            out.append(min(mine, key=stale))
        for c in sorted(self.candidates, key=stale):  # 앞쪽 몇 종목만 되풀이해 보지 않게
            if len(out) >= FOCUS:
                break
            if c not in out:
                out.append(c)
        self.names.update({c["symbol"]: c["name"] for c in out})
        return out[:max(FOCUS, len(symbols))]

    def _session(self, focus: list[dict], agenda: str) -> agents.Session:
        s = agents.Session(conn=self.conn, scope={"scope": "stream", "label": "상시 운용", "candidates": focus}, run_id=self.ctx.run_id,
                           provider=self.p, on_event=self.chat, user_notes=self.chat.notes, gate_mode="supermajority",
                           auto_submit=True, brief=desk.brief(self.conn, market.now_kst().date()),
                           policy=policy.text(policy.get(self.conn)), agenda=agenda, system=issues.brief(self.conn),
                           holdings=exits.checks(self.conn, self.p, self.holdings, [c["symbol"] for c in focus if c.get("held")]))
        self.checks.update(s.holdings)
        s.titles.update({a["key"]: a["title"] for a in self.active})
        return s

    # ---- 계기 ----
    def next_trigger(self, now: datetime) -> dict | None:
        notes = self.chat.notes()
        if len(notes) > self.notes_seen:
            new = notes[self.notes_seen:]
            self.notes_seen = len(notes)
            text = " / ".join(n["text"] for n in new)
            syms = [c["symbol"] for c in scopes.intent_symbols(self.conn, self.p, text)]
            return {"kind": "user", "text": f"관리자 — {text[:120]}", "symbols": syms}
        if self.last_talk and (now - self.last_talk).total_seconds() < MIN_GAP_SEC:
            return None
        alerts = [a for a in desk.alerts(self.conn, now.date()) if a["id"] > self.desk_seen]
        if alerts:
            self.desk_seen = max(a["id"] for a in alerts)
            return {"kind": "risk", "text": " · ".join(a["data"].get("text", "") for a in alerts)[:160],
                    "symbols": [a["data"]["symbol"] for a in alerts if a["data"].get("symbol")]}
        if self.queue:
            return self.queue.pop(0)
        ideas = desk.latest(self.conn, "ideas", now.date())
        if ideas and ideas["id"] > self.ideas_seen:
            self.ideas_seen = ideas["id"]
            self.refresh([n["text"] for n in self.chat.notes()])
            return {"kind": "research", "text": "리서치 갱신 — " + ", ".join(x.get("name") or x["symbol"] for x in ideas["data"].get("picks") or [])[:120],
                    "symbols": [x["symbol"] for x in (ideas["data"].get("picks") or [])[:2]]}
        moved = self._moved()
        if moved:
            return moved
        signal = self._exit_signals(now)
        if signal:
            return signal
        if not self.last_trader or now - self.last_trader >= timedelta(minutes=TRADER_PULSE_MIN):
            return {"kind": "trader_pulse", "text": "트레이더 점검", "symbols": []}
        if not self.last_talk or now - self.last_talk >= timedelta(minutes=PULSE_MIN):
            return {"kind": "pulse", "text": "점검 — 새 소식이 없어도 한 번 살펴요", "symbols": []}
        return None

    def _moved(self) -> dict | None:
        syms = [c["symbol"] for c in sorted(self.candidates, key=lambda c: not c.get("held"))][:12]  # 보유 종목 먼저
        if not syms:
            return None
        try:
            quotes = self.p.quotes(syms)
        except (MarketError, TossError):
            return None
        for sym in syms:
            price = (quotes.get(sym) or {}).get("price")
            if not price:
                continue
            base = self.prices.setdefault(sym, price)
            if abs(price / base - 1) >= MOVE:
                self.prices[sym] = price
                return {"kind": "move", "text": f"{self.names.get(sym, sym)} {price / base - 1:+.1%} ({base:,.0f}원 → {price:,.0f}원, 지난번 이야기한 뒤)",
                        "symbols": [sym]}
        return None

    def _exit_signals(self, now: datetime) -> dict | None:
        """보유 종목에 새 매도 점검 신호가 뜨면 그 종목들로 대화 — EXIT_CHECK_MIN 분마다 재고, (종목, 신호)마다 하루 한 번."""
        if self.exit_at and now - self.exit_at < timedelta(minutes=EXIT_CHECK_MIN):
            return None
        self.exit_at = now
        self._sync_held()
        self.checks = exits.checks(self.conn, self.p, self.holdings)
        new = [(sym, fs) for sym in exits.urgent(self.checks)
               if (fs := [f for f in self.checks[sym]["flags"] if (sym, f["key"]) not in self.exit_seen])][:FOCUS]
        if not new:
            return None
        self.exit_seen |= {(sym, f["key"]) for sym, fs in new for f in fs}  # 넘친 종목은 다음에 잴 때
        return {"kind": "exit", "symbols": [sym for sym, _ in new],
                "text": " · ".join(f"{self.names.get(sym, sym)} {', '.join(f['text'] for f in fs)}" for sym, fs in new)[:160]}

    def _speakers(self, kind: str) -> list[dict]:
        keys = [k for k in PREFER.get(kind, ()) if any(a["key"] == k for a in self.analysts)]
        rest = sorted((a for a in self.analysts if a["key"] not in keys), key=lambda a: self.spoke.get(a["key"], datetime.min.replace(
            tzinfo=market.KST)))
        chosen = [a for k in keys for a in self.analysts if a["key"] == k] + rest
        return chosen[:SPEAKERS + (1 if kind == "user" else 0)]

    # ---- 대화 ----
    async def talk(self, trig: dict, now: datetime) -> None:
        focus = self._focus(trig.get("symbols") or [])
        s = self._session(focus, f"{KIND_KO.get(trig['kind'], trig['kind'])} — {trig['text']}")
        self.n += 1
        speakers = self._speakers(trig["kind"])
        self.chat({"type": "round", "round": self.n, "label": f"{now:%H:%M} · {KIND_KO.get(trig['kind'], '')} — {trig['text']}",
                   "agents": [a["key"] for a in speakers], "stream": True,
                   "candidates": [{k: c.get(k) for k in ("symbol", "name", "security_type", "held", "why")} for c in focus]})
        recent = self.transcript[-RECENT:]
        board = self.board.text([c["symbol"] for c in focus], self.names, now)

        async def speak(agent: dict) -> dict:
            mine = next((t for t in reversed(self.transcript) if t["agent"] == agent["key"]), None)
            try:
                with as_agent(agent["key"]):
                    raw = await agents.LLM("agent_action", agents.system_prompt(agent, s), stream_prompt(s, self.n, trig, recent, board, self.answers),
                                           agents.REBUTTAL_SCHEMA, agents.build_tools(s, agent["tools"], agent["key"]), self.model, 10)
                st = agents.normalize_statement(raw, agent, self.n, mine, s.symbols)
            except LLMError as e:
                st = agents.normalize_statement({}, agent, self.n, None, s.symbols)
                st.update(error=str(e)[:300])
            self.chat({"type": "statement", **st})
            return st

        stmts = await asyncio.gather(*(speak(a) for a in speakers))
        self._remember(stmts, now)
        for st in stmts:
            self.ctx.log(f"[{now:%H:%M} {KIND_KO.get(trig['kind'], '')}] {st['title']}: "
                         + ("응답 실패" if st.get("error") else "; ".join(f"{v['symbol']} {agents.STANCE_KO[v['stance']]}"
                                                                      for v in st["views"] if not v.get("implicit"))))
        self.transcript.extend(stmts)
        for c in focus:
            self.prices.pop(c["symbol"], None)  # 이야기한 가격부터 다시 잰다
            self.talked[c["symbol"]] = now
        self.last_talk = now
        await self.answer_research(stmts, now)
        await self.trade(now, trig["text"])

    def _remember(self, stmts: list[dict], now: datetime) -> None:
        """입장판에 올리고 판단을 그때 가격과 함께 남긴다 — 성과 배수의 재료."""
        symbols = sorted({v["symbol"] for st in stmts for v in st.get("views") or [] if v.get("stance") in ("buy", "sell")})
        try:
            prices = {s_: q.get("price") for s_, q in self.p.quotes(symbols).items()} if symbols else {}
        except (MarketError, TossError):
            prices = {}
        for st in stmts:
            self.board.update(st, now)
            self.spoke[st["agent"]] = now
            track.record(self.conn, st, now, self.ctx.run_id, prices)
        for by, text in system_asks(stmts):
            issues.report(self.conn, text, by, now=now)

    async def answer_research(self, stmts: list[dict], now: datetime) -> None:
        """리서치 담당이 채널의 부탁에 답한다 — 입장 없이 말풍선으로, 다음 대화부터 에이전트들이 읽는다."""
        for key, asks in research_asks(stmts).items():
            if key in self.asked and now - self.asked[key] < timedelta(minutes=ASK_GAP_MIN):
                continue
            self.asked[key] = now
            who = desk.PEOPLE[key]
            s = self._session(self._focus(re.findall(r"\b\d{6}\b", " ".join(asks))[:FOCUS]), f"리서치 담당 답변 — {who['name']}")
            s.titles[key] = who["title"]
            try:
                with as_agent(key):
                    raw = await agents.LLM("invest_desk", research_system(who), "에이전트들의 부탁:\n" + "\n".join(f"- {a}" for a in asks),
                                           RESEARCH_SCHEMA, agents.build_tools(s, RESEARCH_TOOLS, key), self.model, POLL_TURNS)
            except LLMError as e:
                self.ctx.log(f"{who['name']} 답변 실패: {e}", "warn")
                continue
            reply = str(raw.get("reply") or "").strip()[:600] if isinstance(raw, dict) else ""
            if not reply:
                continue
            added = self._add_watch(raw.get("watch") or [], who)
            text = reply + (f"\n볼 종목에 올렸어요: {', '.join(c['name'] for c in added)}" if added else "")
            assistant.add(self.conn, "agent", text, agent=who["key"], title=who["title"], name=who["name"], pokemon=who.get("pokemon"), run_id=self.ctx.run_id)
            self.answers.append(f"{who['name']}({who['title']}): {text[:300]}")

    def _add_watch(self, symbols: list, who: dict) -> list[dict]:
        """담당이 고른 종목을 볼 종목에 — 코드가 국내·거래 가능을 확인한다. 올렸으면 그 종목으로 바로 대화를 연다."""
        added = []
        for sym in dict.fromkeys(str(x).strip() for x in symbols):
            if not re.fullmatch(r"\d{6}", sym) or sym in {c["symbol"] for c in self.candidates} or len(self.watched) >= MAX_WATCH:
                continue
            try:
                info = self.p.info(sym)
            except (MarketError, TossError):
                continue
            if (info.get("currency") or "KRW") != "KRW" or info.get("suspended"):
                continue
            c = {"symbol": sym, "name": info.get("name") or sym, "security_type": info.get("security_type") or "", "held": 0.0, "why": f"{who['name']}가 올림"}
            self.candidates.append(c)
            self.watched.append(c)
            self.names[sym] = c["name"]
            added.append(c)
        if added:
            self.queue.append({"kind": "research", "text": f"{who['name']}가 볼 종목에 올렸어요 — {', '.join(c['name'] for c in added)}",
                               "symbols": [c["symbol"] for c in added]})
        return added

    async def poll(self, aligned: list[dict], why: str, now: datetime) -> None:
        """주문 전 필수 의견 — 찬반이 없는 분석 에이전트 전원에게 그 종목을 묻는다. 의견 없음이면 POLL_TRIES 번까지 다시 묻는다."""
        ask = [a for a in self.analysts if any(self.board.stance(a["key"], x["symbol"], now) not in ("buy", "sell") for x in aligned)]
        if not ask:
            return
        symbols = [x["symbol"] for x in aligned]
        focus = [c for c in self._focus(symbols) if c["symbol"] in symbols]
        topic = ", ".join(f"{self.names.get(x['symbol'], x['symbol'])} {agents.STANCE_KO[x['direction']]}" for x in aligned)
        s = self._session(focus, f"필수 의견 — {topic}")
        self.n += 1
        self.chat({"type": "round", "round": self.n, "label": f"{now:%H:%M} · 필수 의견 — {topic}", "agents": [a["key"] for a in ask], "stream": True,
                   "candidates": [{k: c.get(k) for k in ("symbol", "name", "security_type", "held", "why")} for c in focus]})
        board = self.board.text(symbols, self.names, now)
        prompt = poll_prompt(s, self.n, aligned, board, self.answers, why)

        async def speak(agent: dict) -> dict:
            mine = next((t for t in reversed(self.transcript) if t["agent"] == agent["key"]), None)
            again = ""
            for _ in range(POLL_TRIES):
                try:
                    with as_agent(agent["key"]):
                        raw = await agents.LLM("agent_action", agents.system_prompt(agent, s), prompt + again, agents.POLL_SCHEMA,
                                               agents.build_tools(s, agent["tools"], agent["key"]), self.model, POLL_TURNS)
                    st = agents.normalize_statement(raw, agent, self.n, mine, s.symbols)
                except LLMError as e:
                    st = agents.normalize_statement({}, agent, self.n, None, s.symbols)
                    st.update(error=str(e)[:300])
                    continue
                silent = [v["symbol"] for v in st["views"] if v["stance"] not in ("buy", "sell")]
                if not silent:
                    break
                again = f"\n\n지난 답에 {', '.join(s.name_of(x) for x in silent)} 찬반이 없습니다. 찬성·반대 중 하나로 답하세요."
            self.chat({"type": "statement", **st})
            return st

        stmts = await asyncio.gather(*(speak(a) for a in ask))
        self._remember(stmts, now)
        self.transcript.extend(stmts)
        for c in focus:
            self.talked[c["symbol"]] = now

    def final_gate(self, gate: dict, now: datetime) -> dict:
        """필수 의견을 받은 뒤의 게이트 — 처음 열린 방향 그대로 열려 있을 때만 주문한다."""
        per = {x["symbol"]: self.board.final(x["symbol"], x["direction"], self.analysts, now) for x in gate["aligned"]}
        aligned = [{"symbol": sym, "direction": r["direction"]} for sym, r in per.items() if r["status"] == "aligned"]
        reason = ("열림: " + ", ".join(f"{self.names.get(x['symbol'], x['symbol'])} {agents.STANCE_KO[x['direction']]}" for x in aligned)
                  if aligned else "필수 의견을 받은 뒤 열린 종목이 없어요")
        return {**gate, "symbols": {**gate["symbols"], **per}, "aligned": aligned, "status": "aligned" if aligned else "conflict", "reason": reason}

    async def review(self, now: datetime) -> None:
        """트레이더 점검 — 스스로 사거나 팔 종목을 내놓으면 그 종목으로 대화가 열린다."""
        self.last_trader = now
        if self._blocked():
            return
        mine = [c["symbol"] for c in self.candidates if c.get("held")]
        held = [sym for sym in exits.urgent(self.checks) if sym in mine] + [sym for sym in mine if sym not in self.checks]
        focus = self._focus(list(dict.fromkeys([*held, *self.board.symbols(now)]))[:FOCUS])  # 보유 종목 먼저 — 팔 때인지
        if not focus:
            return
        s = self._session(focus, "트레이더 점검")
        board = self.board.text([c["symbol"] for c in focus], self.names, now)
        try:
            with as_agent(self.executor["key"]):
                raw = await agents.LLM("agent_action", agents.system_prompt(self.executor, s),
                                       trader_review_prompt(s, board, MAX_DAY_ORDERS - len(self.orders)), agents.OPINION_SCHEMA,
                                       agents.build_tools(s, [t for t in self.executor["tools"] if t != agents.EXECUTOR_TOOL],
                                                          self.executor["key"]), self.model, 10)
        except LLMError as e:
            self.ctx.log(f"트레이더 점검 실패: {e}", "warn")
            return
        st = agents.normalize_statement(raw, self.executor, 0, None, s.symbols)
        st["executor"] = True
        unheld = {c["symbol"] for c in focus if "held" in c and not c["held"]}  # 안 가진 종목은 팔 것이 없다
        acting = [v for v in st["views"] if not v.get("implicit") and v["stance"] != "none"
                  and not (v["stance"] == "sell" and v["symbol"] in unheld)]
        if not acting:
            return
        self.chat({"type": "statement", **st})
        self.queue.append({"kind": "trader", "symbols": [v["symbol"] for v in acting],
                           "text": ", ".join(f"{self.names.get(v['symbol'], v['symbol'])} {agents.STANCE_KO[v['stance']]}" for v in acting)
                           + (f" — {st['thesis'][:80]}" if st["thesis"] else "")})

    def _blocked(self) -> str | None:
        return autopilot.blocked(self.conn)

    async def trade(self, now: datetime, why: str) -> None:
        left = MAX_DAY_ORDERS - len(self.orders)
        if left <= 0 or self._blocked():
            return
        syms = self.board.symbols(now)
        gate = self.board.gate(syms, self.names, now, self._held_of(syms))
        cool = {key for key, at in self.last_order.items() if now - at < timedelta(minutes=COOLDOWN_MIN)}
        gate["aligned"] = [x for x in gate["aligned"] if (x["symbol"], x["direction"]) not in cool]
        if not gate["aligned"]:
            return
        await self.poll(gate["aligned"], why, now)
        gate = self.final_gate(gate, now)
        names = {sym: self.names.get(sym, sym) for sym in gate["symbols"]}
        if not gate["aligned"]:
            self.chat({"type": "gate", "gate": gate, "names": names})
            return
        focus = self._focus([x["symbol"] for x in gate["aligned"]])
        focus = [c for c in focus if c["symbol"] in {x["symbol"] for x in gate["aligned"]}]
        s = self._session(focus, f"주문 — {why}")
        s.gate = gate
        s.max_orders = min(agents.MAX_ORDERS, left)
        self.chat({"type": "gate", "gate": gate, "names": names})
        self.chat({"type": "trader_start", "agent": self.executor["key"], "title": self.executor["title"]})
        board = self.board.text(s.symbols, self.names, now)
        try:
            with as_agent(self.executor["key"]):
                raw = await agents.LLM("agent_action", agents.system_prompt(self.executor, s), trade_prompt(s, gate, board, why, left),
                                       agents.TRADER_SCHEMA, agents.build_tools(s, self.executor["tools"], self.executor["key"]), self.model, 14)
            decision = agents.normalize_decision(raw, s, self.executor)
        except (LLMError, TypeError, ValueError) as e:
            decision = {"decisions": [], "decision": "hold", "quantity": 0, "rationale": f"트레이더 응답 실패 — 행동 안 함 ({e})"[:300],
                        "by": "error", "risk_notes": []}
        made = list(s.orders.values())
        for o in made:
            self.last_order[(o["symbol"], o["side"])] = now
            g = gate["symbols"][o["symbol"]]
            self.order_gates[o["symbol"]] = {**g, "reason": f"주문한 {now:%H:%M} 입장판 — {g['reason']}"}
            self.ctx.log(f"주문 #{o['id']} {o['name']} {o['side']} {o['quantity']:g}주 · {o['status']}")
        self.orders += made
        self.decisions.append(decision)
        self.chat({"type": "decision", "agent": self.executor["key"], "title": self.executor["title"], "decision": decision,
                   "order_ids": [o["id"] for o in made]})

    # ---- 하루 ----
    def end_at(self) -> datetime | None:
        try:
            win = autopilot.trading_window(self.p)
        except (MarketError, TossError):
            return None
        if not win:
            return None
        h, m = map(int, win[1].split(":"))
        return market.now_kst().replace(hour=h, minute=m, second=0, microsecond=0)

    def run(self, notes: list[str]) -> str:
        """장이 끝나거나(→ 'closed') 자동 운용이 꺼지거나 방식이 바뀔 때까지(→ 'stopped')."""
        track.backfill(self.conn)
        self.scores = track.scores(self.conn, self.p, market.now_kst())
        self.board.skill = {k: x["skill"] for k, x in self.scores.items()}
        self.refresh(notes)
        self.notes_seen = len(self.chat.notes())  # 시작 메시지(운용 지시)는 이미 후보·프롬프트에 들어갔다
        alerts = desk.alerts(self.conn, market.now_kst().date())
        self.desk_seen = max((a["id"] for a in alerts), default=0)
        ideas = desk.latest(self.conn, "ideas", market.now_kst().date())
        self.ideas_seen = ideas["id"] if ideas else 0
        end = self.end_at()
        while True:
            self.ctx.check_cancel()
            now = market.now_kst()
            if end is None or now >= end:
                return "closed"
            cfg = autopilot.get_config(self.conn)
            if not cfg["enabled"] or cfg["mode"] != "continuous":
                return "stopped"
            trig = self.next_trigger(now)
            if trig and trig["kind"] == "trader_pulse":
                self.run_async(self.review(now))
            elif trig:
                self.run_async(self.talk(trig, now))
            else:
                SLEEP(TICK_SEC)
            self.ctx.progress(min(0.95, self.n / 60))

    def minutes_facts(self) -> tuple[dict, list[dict], dict, dict]:
        """하루 회의록용 — (범위, 에이전트별 마지막 발언, 입장판 게이트, 결정 묶음). 주문한 종목의 게이트는 주문할 때 값이다 — 입장은 STALE_MIN 분이면
        빠져서 마감 때 다시 재면 닫혀 보이고, 회의록이 '닫혔는데 주문이 나갔다'고 잘못 적는다. 주문 상태도 낼 때(open)가 아니라 지금 값으로."""
        now = market.now_kst()
        self.orders = [trading.get_order(self.conn, o["id"]) for o in self.orders]
        syms = list(dict.fromkeys([*self.order_gates, *(self.board.symbols(now) or [c["symbol"] for c in self.candidates])]))
        held = self._held_of(syms)
        scope = {"candidates": [{"symbol": s_, "name": self.names.get(s_, s_)} for s_ in syms]}
        last: dict[str, dict] = {}
        for t in self.transcript:
            last[t["agent"]] = {**t, "round": 1}
        gate = self.board.gate(syms, self.names, now, held)
        gate["symbols"].update(self.order_gates)
        gate["mode"] = "supermajority"
        decisions = [d for x in self.decisions for d in x["decisions"]]
        decision = {"decisions": decisions, "rationale": f"상시 운용 — 오늘 대화 {self.n}번 · 주문 {len(self.orders)}건"}
        return scope, list(last.values()), gate, decision
