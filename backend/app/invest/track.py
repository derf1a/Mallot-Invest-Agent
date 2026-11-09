"""의견 가중치 — 표의 무게 = 확신 × 지난 판단 성과. 상시 운용의 입장판이 쓴다.

- 기록(invest_agent_views): 에이전트가 낸 매수·매도 입장을 종목·날짜·방향마다 처음 한 번만 남긴다(같은 말을 되풀이해 표본이 늘지 않게).
  의견 없음은 판단이 아니라 뺀다. 예전 대화는 messages.jsonl 에서 가져오되 그때 가격을 몰라 그날 종가로 친다(price 없음).
- 성과: 판단 뒤 HORIZON 거래일까지(끝난 거래일만) 수익률에서 같은 기간 코스피 변화를 뺀 값의 방향이 맞았나. 상승장에 매수만 했다고 좋게 보지 않는다.
- 성과 배수: 맞힌 비율 × 2 — 표본이 적을 때 한두 번으로 흔들리지 않게 반반을 PRIOR 번씩 더하고, MIN_SKILL~MAX_SKILL 로 자른다(혼자 표를 쥐지 않게).
- 확신 배수: confidence ÷ CONVICTION_UNIT — 0.5 가 한 표.
"""

import json
import sqlite3
from datetime import date, datetime

from app.core.db import data_dir
from app.invest.marketdata.market import MarketError
from app.invest.broker.toss import TossError

HORIZON = 5
PRIOR = 5
MIN_SKILL = 0.7
MAX_SKILL = 1.3
CONVICTION_UNIT = 0.5
BENCHMARK = "KOSPI"
HISTORY_DAYS = 40
RECENT = 300


def weight(confidence: float | None, skill: float = 1.0) -> float:
    return round(max(confidence or 0.0, 0.0) / CONVICTION_UNIT * skill, 3)


def skill_of(wins: int, judged: int) -> float:
    return round(min(MAX_SKILL, max(MIN_SKILL, 2 * (wins + PRIOR) / (judged + 2 * PRIOR))), 3)


def _save(conn: sqlite3.Connection, at: str, run_id: int | None, agent: str, view: dict, price: float | None) -> None:
    conn.execute("INSERT OR IGNORE INTO invest_agent_views (at, day, run_id, agent, symbol, stance, confidence, price) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (at, at[:10], run_id, agent, view["symbol"], view["stance"], view.get("confidence") or 0.0, price))


def _decided(st: dict) -> list[dict]:
    return [v for v in st.get("views") or [] if v.get("stance") in ("buy", "sell") and not v.get("implicit")]


def record(conn: sqlite3.Connection, st: dict, now: datetime, run_id: int | None, prices: dict[str, float]) -> None:
    if st.get("error") or st.get("executor"):
        return
    for v in _decided(st):
        _save(conn, now.strftime("%Y-%m-%d %H:%M:%S"), run_id, st["agent"], v, prices.get(v["symbol"]))
    conn.commit()


def backfill(conn: sqlite3.Connection) -> int:
    """아직 옮기지 않은 지난 회의의 입장을 가져온다 (매번 불러도 된다)."""
    done = {r[0] for r in conn.execute("SELECT DISTINCT run_id FROM invest_agent_views WHERE run_id IS NOT NULL")}
    added = 0
    for r in conn.execute("SELECT id, created_at, artifact_path FROM runs WHERE kind = 'agent_action' AND artifact_path IS NOT NULL "
                          "AND status != 'running' ORDER BY id").fetchall():
        path = data_dir() / r["artifact_path"] / "messages.jsonl"
        if r["id"] in done or not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("type") != "statement" or m.get("error") or m.get("executor") or m.get("agent") == "trader":
                continue
            for v in _decided(m):
                _save(conn, f"{r['created_at'][:10]} {m.get('ts') or '00:00:00'}", r["id"], m["agent"], v, None)
                added += 1
    conn.commit()
    return added


def _closes(p, symbol: str) -> dict[str, float]:
    try:
        return {c["t"][:10]: c["c"] for c in p.candles(symbol, "1d", HISTORY_DAYS) if c.get("c")}
    except (MarketError, TossError):
        return {}


def _judge(row: sqlite3.Row, closes: dict[str, float], index: dict[str, float], today: str) -> float | None:
    """판단의 코스피 대비 초과 수익 (방향 반영, 양수면 맞음). 아직 끝난 거래일이 없거나 값이 없으면 None."""
    after = sorted(d for d in closes if row["day"] < d < today)[:HORIZON]
    entry = row["price"] or closes.get(row["day"])
    if not after or not entry or not index.get(row["day"]) or not index.get(after[-1]):
        return None
    gain = closes[after[-1]] / entry - 1 - (index[after[-1]] / index[row["day"]] - 1)
    return gain if row["stance"] == "buy" else -gain


def scores(conn: sqlite3.Connection, p, now: datetime) -> dict[str, dict]:
    """에이전트별 성적 — {key: {judged, wins, hit, excess, skill}}. 끝난 거래일이 지난 판단만 센다."""
    today = now.date().isoformat() if isinstance(now, datetime) else date.today().isoformat()
    rows = conn.execute("SELECT * FROM (SELECT * FROM invest_agent_views WHERE day < ? ORDER BY id DESC LIMIT ?) ORDER BY id", (today, RECENT)).fetchall()
    if not rows:
        return {}
    try:
        index = {c["t"][:10]: c["c"] for c in p.indicator_closes(BENCHMARK, HISTORY_DAYS)}
    except (MarketError, TossError):
        return {}
    closes = {sym: _closes(p, sym) for sym in {r["symbol"] for r in rows}}
    got: dict[str, list[float]] = {}
    for r in rows:
        edge = _judge(r, closes[r["symbol"]], index, today)
        if edge is not None:
            got.setdefault(r["agent"], []).append(edge)
    out = {}
    for agent, edges in got.items():
        wins = sum(e > 0 for e in edges)
        out[agent] = {"judged": len(edges), "wins": wins, "hit": round(wins / len(edges), 3), "excess": round(sum(edges) / len(edges), 4),
                      "skill": skill_of(wins, len(edges))}
    return out
