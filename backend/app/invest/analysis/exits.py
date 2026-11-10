"""보유 종목 매도 점검 — 가진 종목마다 '지금 팔 때인가'를 따질 사실을 코드가 꺼낸다(LLM 없음). 판단은 에이전트가 한다.

토론(정기 회의·상시 운용·수동 토론)의 후보에 보유 종목이 있으면 이 점검이 그 종목 줄 아래 '매도 점검: …'으로 붙고,
분석 에이전트의 시스템 프롬프트(agents.EXIT_RULES)가 보유 종목마다 매도 타이밍을 따지게 한다 — 지금 팔지(전량·일부) /
계속 들고 갈지(그러면 언제 팔지: 손절가·목표가·조건).
- 사실: 평단·현재가·평가 손익률·계좌 비중, 5일·20일 등락, RSI, 20일선 대비, 60일 고점 대비 (일봉 지표는 screening.metrics)
- 신호(flags): 손실 LOSS_FLAG 이하 · 평가익 GAIN_FLAG 이상 · 60일 고점 대비 DRAWDOWN_FLAG 이하 · 20일선 아래 · 이동평균 역배열 ·
  RSI HOT_RSI 이상 · 비중이 종목당 한도(주문 한도와 운용 목표 중 낮은 쪽) 초과
신호는 '팔아라'가 아니라 '이것을 따져 보라'는 표시 — 신호가 없어도 보유 종목의 매도 판단은 한다.
자동 운용 회의(scopes._scan)는 보유 종목을 후보 수와 따로 MAX_HELD 개까지(신호·비중 순) 언제나 넣고, 상시 운용(stream.py)은
대화마다 보유 종목 한 자리를 두고 새 신호가 뜨면(종목·신호마다 하루 한 번) 바로 그 종목으로 이야기한다.
"""

import sqlite3
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

from app.invest.analysis import policy, screening
from app.invest.broker import config, trading
from app.invest.broker.toss import TossError
from app.invest.broker.trading import TradeError
from app.invest.marketdata.market import MarketError

LOSS_FLAG = -0.07      # 평단 대비 이만큼 잃으면 — 손절 기준 점검
GAIN_FLAG = 0.20       # 평단 대비 이만큼 벌면 — 차익 실현 점검
DRAWDOWN_FLAG = -0.10  # 60일 종가 고점 대비 이만큼 내려오면 — 추세 꺾임 점검
HOT_RSI = 70           # 이 이상이면 과열
MAX_HELD = 6           # 자동 운용 회의에 후보 수와 따로 넣는 보유 종목
WORKERS = 4


def _kr(symbol: str) -> bool:
    return str(symbol).isdigit() and len(str(symbol)) == 6


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:+.1f}%"


def position_limit(conn: sqlite3.Connection) -> float:
    """종목당 비중 한도(%) — 주문 한도(max_position_pct)와 운용 목표의 종목당 최대 비중 중 낮은 쪽."""
    lim = config.limits(conn)["max_position_pct"]
    goal = policy.get(conn).get("max_position")
    return min(lim, goal) if goal else lim


def check(h: dict, m: dict | None, limit_pct: float | None = None) -> dict:
    """보유 한 줄(trading 계좌 요약의 holdings) + 일봉 지표(screening.metrics, 없으면 None) → 사실과 신호."""
    m = m or {}
    price = h.get("last_price") or m.get("price")
    ma20 = m.get("ma20")
    pnl, weight, dd, rsi = h.get("pnl_rate"), h.get("weight"), m.get("drawdown"), m.get("rsi")
    gap = round(price / ma20 - 1, 4) if price and ma20 else None
    flags = []
    if pnl is not None and pnl <= LOSS_FLAG:
        flags.append({"key": "loss", "text": f"손실 {_pct(pnl)}"})
    if pnl is not None and pnl >= GAIN_FLAG:
        flags.append({"key": "gain", "text": f"평가익 {_pct(pnl)}"})
    if dd is not None and dd <= DRAWDOWN_FLAG:
        flags.append({"key": "drawdown", "text": f"60일 고점 대비 {_pct(dd)}"})
    if gap is not None and gap < 0:
        flags.append({"key": "ma20", "text": f"20일선 아래 {_pct(gap)}"})
    if m.get("trend") == -1:  # 급반등으로 종가가 모든 선 위면 그렇다고 적는다 — '역배열'만 보고 하락 중으로 읽지 않게
        flags.append({"key": "downtrend", "text": "이동평균 역배열 (종가는 모든 선 위 — 반등 중)" if m.get("price_vs_ma") == "above_all"
                      else "이동평균 역배열"})
    if rsi is not None and rsi >= HOT_RSI:
        flags.append({"key": "hot", "text": f"RSI {rsi:.0f} 과열"})
    if limit_pct and weight is not None and weight * 100 > limit_pct:
        flags.append({"key": "weight", "text": f"비중 {weight * 100:.1f}% > 한도 {limit_pct:g}%"})
    return {"symbol": h["symbol"], "name": h.get("name") or h["symbol"], "quantity": h.get("quantity"), "avg_price": h.get("avg_price"),
            "price": price, "pnl_rate": pnl, "weight": weight, "change_5d": m.get("change_5d"), "change_20d": m.get("change_20d"),
            "rsi": rsi, "ma20_gap": gap, "drawdown": dd, "flags": flags}


def held_rows(conn: sqlite3.Connection, p) -> list[dict]:
    """토스 계좌의 국내 보유 (못 읽으면 [] — 점검 없이 토론한다)."""
    try:
        rows = trading.account(conn, market=p).summary()["holdings"]
    except (TradeError, MarketError, TossError):
        return []
    return [h for h in rows if _kr(h["symbol"]) and h.get("quantity")]


def checks(conn: sqlite3.Connection, p, holdings: list[dict], symbols: Iterable[str] | None = None,
           metrics: dict[str, dict | None] | None = None) -> dict[str, dict]:
    """보유(held_rows·계좌 요약의 holdings) → {종목: 점검}. symbols 로 고르고, metrics 를 주면 일봉을 다시 받지 않는다."""
    want = set(symbols) if symbols is not None else None
    rows = [h for h in holdings if _kr(h["symbol"]) and h.get("quantity") and (want is None or h["symbol"] in want)]
    if not rows:
        return {}
    metrics = metrics or {}
    todo = [h["symbol"] for h in rows if h["symbol"] not in metrics]
    with ThreadPoolExecutor(WORKERS) as ex:
        got = dict(zip(todo, ex.map(lambda s: screening.metrics(p, s), todo)))
    limit = position_limit(conn)
    return {h["symbol"]: check(h, metrics.get(h["symbol"]) or got.get(h["symbol"]), limit) for h in rows}


def for_candidates(conn: sqlite3.Connection, p, candidates: list[dict]) -> dict[str, dict]:
    """토론 후보 중 보유 종목의 점검."""
    held = [c["symbol"] for c in candidates if c.get("held")]
    return checks(conn, p, held_rows(conn, p), held) if held else {}


def urgent(found: dict[str, dict]) -> list[str]:
    """먼저 볼 순서 — 신호가 많은 순, 같으면 비중이 큰 순."""
    return sorted(found, key=lambda s: (-len(found[s]["flags"]), -(found[s].get("weight") or 0)))


def why(c: dict) -> str:
    """후보 이유 (채팅방 후보 칩)."""
    return "보유 — 매도 점검" + (" · " + ", ".join(f["text"] for f in c["flags"]) if c["flags"] else "")


def line(c: dict) -> str:
    """에이전트 프롬프트의 후보 줄 아래 '매도 점검: …'."""
    bits = []
    if c.get("avg_price") and c.get("price"):
        bits.append(f"평단 {c['avg_price']:,.0f}원 → 지금 {c['price']:,.0f}원 ({_pct(c['pnl_rate'])})")
    elif c.get("price"):
        bits.append(f"지금 {c['price']:,.0f}원 (평단 모름)")
    if c.get("weight") is not None:
        bits.append(f"계좌 비중 {c['weight'] * 100:.1f}%")
    moves = [f"{label} {_pct(c[k])}" for k, label in (("change_5d", "5일"), ("change_20d", "20일")) if c.get(k) is not None]
    if moves:  # 주가 등락 — 평단 대비 손익(앞의 괄호)과 헷갈리지 않게
        bits.append("주가 등락 " + " · ".join(moves))
    if c.get("rsi") is not None:
        bits.append(f"RSI {c['rsi']:.0f}")
    if c.get("ma20_gap") is not None:
        bits.append(f"20일선 대비 {_pct(c['ma20_gap'])}")
    if c.get("drawdown") is not None:
        bits.append(f"60일 고점 대비 {_pct(c['drawdown'])}")
    bits.append("신호: " + (", ".join(f["text"] for f in c["flags"]) if c["flags"] else "없음"))
    return " · ".join(bits)
