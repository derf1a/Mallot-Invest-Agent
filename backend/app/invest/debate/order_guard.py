"""에이전트 주문 장부 — 오늘 낸 주문을 에이전트에게 보이고, 같은 주문이 두 번 나가거나 토론보다 크게 나가지 않게 코드가 막는다.

- 오늘 낸 주문(today_orders · orders_text): 실행 번호 · 시각 · 수량 · 상태. 상시 운용에서 같은 종목을 대화마다 다시 사면 보유가 늘어나는데,
  앞 주문이 보이지 않으면 에이전트가 '중복 주문'을 의심한다 — get_portfolio · get_trading_limits · 상시 운용 대화 프롬프트에 넣는다.
- 하루 자동 주문 건수(day_status): 오늘 에이전트가 낸 주문(거절 빼고)을 DB 로 센다 — 상시 운용 채널이 다시 열려도 같은 기준.
  상시 운용 안건 문구('오늘 남은 자동 주문 N건')와 get_trading_limits 가 이 하나를 쓴다. 세션 건수(이번 회의에서 낼 수 있는 수)와 따로.
- 중복(duplicate): 같은 실행 · 종목 · 방향의 앞 주문이 아직 승인 대기 · 미체결이거나 DUP_MIN 분 안이면 거절.
- 제안 수량(proposals · size_error): 분석 에이전트는 views 에 quantity(몇 주)를 낼 수 있다. 그 방향에 찬성한 사람이 낸 수량 중 가장 큰 것까지만
  주문한다. 아무도 수량을 내지 않았으면 막지 않는다(트레이더가 한도 · 현금 안에서 정한다 — 현금을 묶어 두는 규칙이 아니다).
"""

import sqlite3
from collections.abc import Iterable
from datetime import date, datetime, timedelta

from app.invest.marketdata import market

MAX_DAY_ORDERS = 6        # 상시 운용이 하루에 내는 자동 주문 최대 (오늘 에이전트 주문, 거절 빼고)
DUP_MIN = 30              # 같은 실행 · 종목 · 방향의 주문은 이만큼 안에 다시 내지 않는다
LIVE = ("pending_approval", "open", "partial")
SIDE_KO = {"buy": "매수", "sell": "매도"}
STATUS_KO = {"pending_approval": "승인 대기", "open": "미체결", "partial": "일부 체결", "filled": "체결", "cancelled": "취소",
             "rejected": "거절", "failed": "접수 불명"}


def _today() -> str:
    return date.today().isoformat()  # trading._today_amount 와 같은 기준 (DB 의 localtime)


def today_orders(conn: sqlite3.Connection, symbols: Iterable[str] | None = None, limit: int = 30) -> list[dict]:
    """오늘 낸 주문(거절 빼고, 수동 포함) — 실행 번호 · 시각 · 수량 · 상태. symbols 를 주면 그 종목만."""
    syms = list(dict.fromkeys(symbols)) if symbols is not None else None
    if syms is not None and not syms:
        return []
    where = "date(created_at) = ? AND status != 'rejected'" + (f" AND symbol IN ({','.join('?' * len(syms))})" if syms else "")
    rows = conn.execute(f"SELECT id, run_id, created_at, symbol, name, side, quantity, filled_quantity, status, source FROM invest_orders "
                        f"WHERE {where} ORDER BY id LIMIT ?", (_today(), *(syms or []), limit)).fetchall()
    return [{"order_id": r["id"], "run_id": r["run_id"], "time": str(r["created_at"])[11:16], "symbol": r["symbol"], "name": r["name"],
             "side": r["side"], "quantity": r["quantity"], "filled_quantity": r["filled_quantity"], "status": r["status"],
             "source": r["source"]} for r in rows]


def day_status(conn: sqlite3.Connection) -> dict:
    """하루 자동 주문 건수 — 오늘 에이전트가 낸 주문(거절 빼고)."""
    n = conn.execute("SELECT COUNT(*) FROM invest_orders WHERE source = 'agent' AND status != 'rejected' AND date(created_at) = ?",
                     (_today(),)).fetchone()[0]
    return {"agent_orders_today": n, "max_agent_orders_per_day": MAX_DAY_ORDERS, "agent_orders_left_today": max(0, MAX_DAY_ORDERS - n)}


def day_text(day: dict) -> str:
    """안건 · 프롬프트에 쓰는 같은 문구 — get_trading_limits 의 agent_orders_left_today 와 같은 수."""
    return (f"오늘 남은 자동 주문 {day['agent_orders_left_today']}건(하루 {day['max_agent_orders_per_day']}건 중 "
            f"{day['agent_orders_today']}건 냄 · get_trading_limits 의 agent_orders_left_today)")


def order_line(o: dict) -> str:
    return (f"{o['time']} {o['name'] or o['symbol']}({o['symbol']}) {SIDE_KO.get(o['side'], o['side'])} {o['quantity']:g}주 "
            f"{STATUS_KO.get(o['status'], o['status'])} · 주문 #{o['order_id']}" + (f" · 실행 #{o['run_id']}" if o["run_id"] else " · 수동"))


def orders_text(conn: sqlite3.Connection, symbols: Iterable[str] | None = None) -> str:
    """오늘 낸 주문 블록 (없으면 '') — 보유가 늘어난 이유를 에이전트가 알게. conn 이 없으면(프롬프트 미리보기) ''."""
    if conn is None:
        return ""
    rows = today_orders(conn, symbols)
    if not rows:
        return ""
    return ("[오늘 낸 주문 — 보유 수량에 이미 들어 있어요. 앞 대화의 주문이라 같은 주문이 두 번 나간 것이 아닙니다]\n"
            + "\n".join(f"- {order_line(o)}" for o in rows) + "\n")


def duplicate(conn: sqlite3.Connection, run_id: int | None, symbol: str, side: str, now: datetime | None = None) -> str | None:
    """같은 실행 · 종목 · 방향의 앞 주문이 아직 살아 있거나(승인 대기 · 미체결) DUP_MIN 분 안이면 거절 사유."""
    if run_id is None:
        return None
    now = (now or market.now_kst()).replace(tzinfo=None)
    for r in conn.execute("SELECT id, created_at, quantity, status FROM invest_orders WHERE run_id = ? AND symbol = ? AND side = ? "
                          "AND status != 'rejected' ORDER BY id DESC", (run_id, symbol, side)).fetchall():
        what = f"앞 주문 #{r['id']}({str(r['created_at'])[11:16]} · {r['quantity']:g}주)"
        if r["status"] in LIVE:
            return f"{what}이 아직 {STATUS_KO[r['status']]}이에요 — 같은 주문을 또 내지 않아요. 체결 · 취소된 뒤에 다시 판단하세요"
        try:
            at = datetime.fromisoformat(str(r["created_at"])[:19])
        except ValueError:
            continue
        if abs(now - at) < timedelta(minutes=DUP_MIN):  # abs — DB 시각과 시계가 몇 초 어긋나도
            return f"{what}을 낸 지 {DUP_MIN}분이 안 됐어요 — 같은 종목 · 방향은 {DUP_MIN}분에 한 번만 (중복 주문 방지)"
    return None


def proposals(views: Iterable[tuple[str, dict]], direction: str) -> dict | None:
    """그 방향에 찬성한 분석 에이전트가 낸 수량 → {max, by: [(이름, 수량)]}. 아무도 수량을 내지 않았으면 None(막지 않음)."""
    by = [(title, int(v["quantity"])) for title, v in views if v.get("stance") == direction and (v.get("quantity") or 0) > 0]
    return {"max": max(q for _, q in by), "by": by} if by else None


def proposals_text(p: dict | None) -> str:
    return f"제안 수량 최대 {p['max']}주 (" + ", ".join(f"{t} {q}주" for t, q in p["by"]) + ")" if p else "제안 수량 없음 — 한도 · 현금 안에서 정하세요"


def size_error(quantity: int, p: dict | None, name: str) -> str | None:
    """찬성한 에이전트가 낸 가장 큰 수량보다 많이 사거나 팔면 거절 사유."""
    if not p or quantity <= p["max"]:
        return None
    return f"{name}: 토론에서 찬성한 에이전트가 제안한 수량은 최대 {p['max']}주예요 ({proposals_text(p)}) — {quantity}주는 토론보다 커요"
