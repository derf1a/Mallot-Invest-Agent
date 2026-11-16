"""Mallot 홈 통합 대시보드에 주는 요약 (`/api/feed/*`) — mallot-home 이 이 컴퓨터 안에서 부른다.

- 열쇠: .env 의 MIA_FEED_TOKEN (32자 이상). 요청 머리 X-Mallot-Feed 가 같고, 터널 · 프록시를 거치지 않았고(127.0.0.1 직접),
  GET 이고, 경로가 /api/feed/ 이면 로그인 없이 읽는다 (auth/guard). 관리자는 로그인으로도 본다.
- 숫자만 준다: 자산 합계 · 손익 · 종목 비중. 계좌번호 · 주문 내역은 보내지 않는다. 주문 · 토스 쓰기는 없다 (읽기만).
- 모양은 Mallot 앱들(가계부 · 투자 · 실험실)이 같다 — resources 는 공통, summary 만 앱마다 다르다.
"""

import hmac
import os
import resource
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from app.core import metrics
from app.core.db import data_dir, db_path

APP = "invest"
TOKEN_ENV = "MIA_FEED_TOKEN"
HEADER = "x-mallot-feed"
PREFIX = "/api/feed/"
MIN_TOKEN = 32


def token_ok(headers, method: str, path: str, proxied: bool) -> bool:
    """홈 서버가 보낸 요청인가 — 열쇠가 맞고, 바깥을 거치지 않은 GET /api/feed/*."""
    want = os.environ.get(TOKEN_ENV, "")
    got = headers.get(HEADER, "")
    if len(want) < MIN_TOKEN or not got or proxied or method not in ("GET", "HEAD") or not path.startswith(PREFIX):
        return False
    return hmac.compare_digest(want.encode(), got.encode())


def app_version() -> str:
    try:
        return version("mallot-invest-agent")
    except PackageNotFoundError:
        return "0"


def _rss_bytes() -> int | None:
    try:
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return rss if sys.platform == "darwin" else rss * 1024
    except (OSError, ValueError):
        return None


def _dir_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _since(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def llm_usage(conn: sqlite3.Connection, days: int = 30) -> dict:
    from app.llm.client import FEATURES, auth_mode

    since = _since(days)
    row = conn.execute(
        "SELECT COUNT(*) AS calls, SUM(ok) AS ok, SUM(1 - ok) AS failed, SUM(input_tokens) AS input_tokens, "
        "SUM(output_tokens) AS output_tokens, SUM(cost_usd) AS cost_usd, AVG(duration_ms) AS avg_duration_ms "
        "FROM llm_usage WHERE created_at >= ?", (since,)).fetchone()
    totals = {k: int(row[k] or 0) for k in ("calls", "ok", "failed", "input_tokens", "output_tokens")}
    totals["cost_usd"] = round(row["cost_usd"] or 0.0, 4)
    totals["avg_duration_ms"] = int(row["avg_duration_ms"]) if row["avg_duration_ms"] is not None else None
    durations = sorted(r[0] for r in conn.execute(
        "SELECT duration_ms FROM llm_usage WHERE created_at >= ? AND duration_ms IS NOT NULL", (since,)))
    totals["p95_duration_ms"] = durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else None
    by_feature = [{"feature": r["feature"], "title": FEATURES.get(r["feature"], r["feature"]), "calls": r["calls"],
                   "cost_usd": round(r["cost_usd"] or 0.0, 4)}
                  for r in conn.execute("SELECT feature, COUNT(*) AS calls, SUM(cost_usd) AS cost_usd FROM llm_usage "
                                        "WHERE created_at >= ? GROUP BY feature ORDER BY calls DESC", (since,))]
    rows = {r["day"]: r for r in conn.execute(
        "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS calls, SUM(cost_usd) AS cost_usd FROM llm_usage "
        "WHERE created_at >= ? GROUP BY day", (since,))}
    daily = []
    for i in range(days - 1, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        r = rows.get(d)
        daily.append({"day": d, "calls": r["calls"] if r else 0, "cost_usd": round((r["cost_usd"] or 0.0) if r else 0.0, 4)})
    return {"days": days, "auth_mode": auth_mode(), "totals": totals, "by_feature": by_feature, "daily": daily}


def runs_overview(conn: sqlite3.Connection) -> dict:
    running = conn.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued', 'running')").fetchone()[0]
    failed = conn.execute("SELECT COUNT(*) FROM runs WHERE status = 'failed' AND created_at >= ?", (_since(1),)).fetchone()[0]
    recent = [dict(r) for r in conn.execute(
        "SELECT id, kind, name, status, created_at, finished_at FROM runs ORDER BY id DESC LIMIT 5")]
    return {"running": running, "failed_24h": failed, "recent": recent}


def resources(conn: sqlite3.Connection) -> dict:
    path = db_path()
    return {
        "app": APP,
        "version": app_version(),
        "uptime_s": int(time.time() - metrics.STARTED_AT),
        "rss_bytes": _rss_bytes(),
        "db_bytes": path.stat().st_size if path.exists() else 0,
        "data_bytes": _dir_bytes(data_dir()),
        "llm": llm_usage(conn),
        "requests": metrics.snapshot(),
        "runs": runs_overview(conn),
    }


def summary(conn: sqlite3.Connection) -> dict:
    """내 투자현황 숫자 — 총 자산 · 평가 손익 · 현금 비중 · 종목 비중(큰 순 8개 + 나머지). 토스를 못 읽으면 available=False."""
    from app.invest.broker import portfolio

    try:
        o = portfolio.overview(conn)
    except Exception as e:  # noqa: BLE001 — 대시보드 카드 하나가 실패해도 홈은 뜬다
        return {"app": APP, "available": False, "error": str(e)[:200]}
    total = o["total_assets_krw"] or 0
    cost = sum(x["cost_krw"] for x in o["positions"] if x.get("pnl_krw") is not None)
    pnl = sum(x["pnl_krw"] for x in o["positions"] if x.get("pnl_krw") is not None)
    by_symbol: dict[str, dict] = {}
    for x in o["positions"]:
        agg = by_symbol.setdefault(x["symbol"], {"symbol": x["symbol"], "name": x["name"], "value_krw": 0.0})
        agg["value_krw"] += x["value_krw"] or 0
    slices = sorted(by_symbol.values(), key=lambda a: -a["value_krw"])
    top, rest = slices[:8], slices[8:]
    alloc = [{"symbol": a["symbol"], "name": a["name"], "value_krw": round(a["value_krw"]),
              "weight": round(a["value_krw"] / total, 4) if total else 0.0} for a in top]
    if rest:
        v = sum(a["value_krw"] for a in rest)
        alloc.append({"symbol": "", "name": f"그 밖 {len(rest)}종목", "value_krw": round(v), "weight": round(v / total, 4) if total else 0.0})
    cash = o["cash"]["krw"] + o["cash"]["usd_in_krw"]
    if cash:
        alloc.append({"symbol": "", "name": "현금", "value_krw": round(cash), "weight": round(cash / total, 4) if total else 0.0})
    return {
        "app": APP,
        "available": True,
        "total_assets_krw": total,
        "stock_value_krw": o["stock_value_krw"],
        "cash_ratio": o["cash_ratio"],
        "pnl_krw": round(pnl) if cost else None,
        "pnl_rate": round(pnl / cost, 4) if cost else None,
        "holdings": o["concentration"]["count"],
        "allocation": alloc,
        "usd_krw": o["usd_krw"],
    }
