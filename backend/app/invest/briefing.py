"""수익률과 한 페이지 보고서 — 숫자는 모두 코드가 계산한다(보고서에서 비서는 짧은 의견만 붙인다).

- 기준 원금(settings invest_baseline): 운용을 시작할 때 넣은 돈과 이후 추가 입금 목록. 운용 수익률 = (토스 계좌 총액(국내·해외 보유 +
  원화·달러 예수금, 원화 환산) − 기준 원금 합) ÷ 기준 원금 합. 다른 계좌(수기 입력)는 빼고 — AI 가 굴리는 토스 계좌만. 기준을 정하지 않으면 없음.
- performance: 운용 수익률(기준 원금 대비) + 내 투자현황 평가 손익(원금을 아는 보유의 손익 ÷ 매입 원금, 총 자산·현금 비중·상위 보유) + AI 주문 성과.
  항목별: 종목마다 매입·평가·손익·수익률·기여도(그 종목 손익 ÷ 기준 원금, 없으면 매입 원금 합) — portfolio.items, AI 주문마다 체결 금액·변화·
  손익 — ai.orders(최근 AI_ORDERS 건), 주문을 낸 회의별 합 — ai.by_meeting.
  AI 주문 성과 = 에이전트 주문 중 체결된 것의 체결가 → 지금 가격 변화. 매수는 오른 만큼, 매도는 판 뒤 내린 만큼(피한 손실)을
  '주문 뒤 변화'로 보고 체결 금액으로 가중한다 — 실현 손익이 아니라 '그 판단이 지금까지 맞았나'의 평가.
- snapshot: 한 페이지 보고서 — 관리자가 계속 볼 것만(설정·일정·운용 지시·운용 목표 항목은 넣지 않는다 — 그건 자동 운용 서랍):
  1) 보유 종목·현황: 운용 수익률·총 자산·평가 손익·현금, 목표 대비(goal_check: 주식 비중·수익률·벤치마크 지수 대비), 보유 종목마다
     수량·평가 금액·비중·손익·오늘 등락(HOLDINGS 개, 나머지는 개수), 막힌 것(전체 중지·주문 조건)은 warnings 로 맨 위.
     history: 운용 수익률 추이 — 지나간 보고서에 남은 값(기준 원금 시작일이 같은 것만, 최근 HISTORY 개) + 지금, 같은 시각의 지수 변화와 나란히.
  2) 회의 요약: 최근 MEETINGS 개 회의 — 회의록 한 줄·요약·다음에 볼 것(시스템·개발 이야기는 시스템 관리자 몫이라 뺀다, issues.is_note), 그 회의 주문(주문 뒤 변화). 진행 중인 상시 운용은 지금까지
     (대화 수·에이전트 입장으로 센 종목별 매수·매도·트레이더 마지막 판단). 회의록도 주문도 없이 끊긴 회의는 뺀다.
  3) 장내 이슈: 지수·금리·환율, 시장 전략가의 최근 시황과 섹터 비중 의견(ISSUE_DAYS 안 — 섹터별 기간 등락 포함), 시장 뉴스 제목,
     보유 종목 뉴스, 오늘 리스크 경보.
  토스에 못 닿는 부분은 note 로 남기고 나머지는 그대로. 'v': 2 — 예전 보고서 카드(운영 플랜·운용 지시가 든 것)도 화면이 새 모양으로 그린다.
- to_text: 보고서를 글(복사·붙여넣기용, 아침 의견을 쓰는 비서가 읽는 글)로.
"""

import json
import sqlite3
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

from app.core.db import data_dir
from app.invest import autopilot, desk, market, news, policy
from app.invest.broker import portfolio
from app.invest.market import MarketError
from app.invest.broker.toss import TossError
from app.invest.broker.trading import TradeError
from app.system import issues

AI_ORDERS = 50   # AI 주문 성과에 보는 최근 체결 주문 수
TOP = 5          # 현황의 상위 보유 수
HOLDINGS = 12    # 보고서에 줄로 보이는 보유 종목 수 (나머지는 개수만)
MEETINGS = 3     # 보고서의 최근 회의 수
HISTORY = 60     # 수익률 추이에 잇는 최근 보고서 수
REFERENCE = "KOSPI"  # 운용 목표에 벤치마크가 없을 때 수익률 추이에 같이 그리는 지수
ISSUE_DAYS = 3   # '요즘' — 시황·섹터 의견·뉴스는 이 날수 안의 것만
NEWS = 5         # 시장 뉴스 제목 수
HOLDING_NEWS = 2  # 보유 종목마다 뉴스 제목 수 (모두 합쳐 3종목치까지)
WORKERS = 4      # 보유 종목 오늘 등락을 동시에 받는 수
SIDE_KO = {"buy": "매수", "sell": "매도"}
ORDER_KO = {"pending_approval": "승인 대기", "open": "미체결", "partial": "부분 체결", "filled": "체결", "cancelled": "취소",
            "rejected": "거절", "failed": "확인 필요"}
RUN_KO = {"succeeded": "끝남", "cancelled": "중지", "failed": "실패", "running": "진행 중", "queued": "대기"}
S_BASELINE = "invest_baseline"
MAX_CONTRIBUTIONS = 50
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class BaselineError(Exception):
    pass


def _clean_contributions(items: object) -> list[dict]:
    out = []
    for c in items if isinstance(items, list) else []:
        if not isinstance(c, dict):
            continue
        try:
            amount = int(c.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        day = str(c.get("date") or "")
        if amount and _DATE.match(day):
            out.append({"date": day, "amount": amount, "note": str(c.get("note") or "").strip()[:60]})
    return sorted(out, key=lambda c: c["date"])


def get_baseline(conn: sqlite3.Connection) -> dict:
    """기준 원금 — 넣은 돈(추가 입금은 +, 뺀 돈은 −) 목록과 합계·시작일."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (S_BASELINE,)).fetchone()
    try:
        items = _clean_contributions(json.loads(row["value"]).get("contributions")) if row else []
    except (ValueError, AttributeError):
        items = []
    return {"contributions": items, "amount": sum(c["amount"] for c in items), "since": items[0]["date"] if items else None}


def set_baseline(conn: sqlite3.Connection, contributions: list[dict]) -> dict:
    items = _clean_contributions(contributions)
    if len(items) != len(contributions) or len(items) > MAX_CONTRIBUTIONS:
        raise BaselineError("입금마다 날짜(YYYY-MM-DD)와 0 이 아닌 금액이 필요해요")
    if items and sum(c["amount"] for c in items) <= 0:
        raise BaselineError("기준 원금 합계는 0원보다 커야 해요")
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (S_BASELINE, json.dumps({"contributions": items}, ensure_ascii=False)))
    conn.commit()
    return get_baseline(conn)


def add_contribution(conn: sqlite3.Connection, amount: int, day: str | None = None, note: str = "") -> dict:
    """추가 입금(+) 또는 출금(−)을 기준 원금에 더한다."""
    return set_baseline(conn, [*get_baseline(conn)["contributions"],
                               {"date": day or market.now_kst().date().isoformat(), "amount": amount, "note": note}])


def _baseline_perf(conn: sqlite3.Connection, o: dict | None) -> dict | None:
    b = get_baseline(conn)
    if not b["amount"]:
        return None
    out = {**b, "value_krw": None, "pnl_krw": None, "return_rate": None,
           "days": (market.now_kst().date() - date.fromisoformat(b["since"])).days}
    if o is not None:
        value = sum(x["value_krw"] for x in o["positions"] if x["where"] == "토스") + o["cash"]["krw"] + o["cash"]["usd_in_krw"]
        out.update(value_krw=round(value), pnl_krw=round(value - b["amount"]), return_rate=round((value - b["amount"]) / b["amount"], 4))
    return out


def ai_orders(conn: sqlite3.Connection, p) -> dict:
    rows = conn.execute("SELECT * FROM invest_orders WHERE source = 'agent' AND filled_quantity > 0 AND avg_fill_price > 0 "
                        "ORDER BY id DESC LIMIT ?", (AI_ORDERS,)).fetchall()
    out = {"count": len(rows), "amount_krw": 0, "change": None, "pnl_krw": 0, "orders": [], "note": None}
    if not rows:
        return out
    try:
        quotes = p.quotes(sorted({r["symbol"] for r in rows}))
    except (MarketError, TossError) as e:
        return {**out, "note": f"지금 가격을 불러오지 못했어요: {e}"}
    amount = gain = 0.0
    meetings: dict[int | None, dict] = {}
    for r in rows:
        now = (quotes.get(r["symbol"]) or {}).get("price")
        fill = r["avg_fill_price"]
        value = r["filled_quantity"] * fill
        change = (now / fill - 1 if r["side"] == "buy" else fill / now - 1) if now else None
        pnl = value * change if change is not None else None
        if change is not None:
            amount += value
            gain += pnl
        out["orders"].append({"id": r["id"], "run_id": r["run_id"], "at": (r["filled_at"] or r["created_at"])[:16].replace("T", " "),
                              "symbol": r["symbol"], "name": r["name"], "side": r["side"], "quantity": r["filled_quantity"], "fill": fill,
                              "now": now, "amount_krw": round(value), "change": round(change, 4) if change is not None else None,
                              "pnl_krw": round(pnl) if pnl is not None else None})
        m = meetings.setdefault(r["run_id"], {"run_id": r["run_id"], "orders": 0, "amount_krw": 0.0, "pnl_krw": 0.0, "priced": 0.0})
        m["orders"] += 1
        m["amount_krw"] += value
        if pnl is not None:
            m["pnl_krw"] += pnl
            m["priced"] += value
    out.update(amount_krw=round(amount), pnl_krw=round(gain), change=round(gain / amount, 4) if amount else None)
    out["by_meeting"] = _by_meeting(conn, meetings)
    return out


def _by_meeting(conn: sqlite3.Connection, meetings: dict[int | None, dict]) -> list[dict]:
    """AI 주문을 낸 회의별 — 주문 수·체결 금액·지금까지 손익·변화 (최근 회의 먼저)."""
    ids = [k for k in meetings if k is not None]
    names = {r["id"]: (r["name"], r["created_at"][:16]) for r in conn.execute(
        f"SELECT id, name, created_at FROM runs WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
    out = []
    for k, m in meetings.items():
        name, at = names.get(k, ("(지워진 회의)" if k else "회의 밖 주문", ""))
        out.append({"run_id": k, "name": name, "at": at, "orders": m["orders"], "amount_krw": round(m["amount_krw"]),
                    "pnl_krw": round(m["pnl_krw"]), "change": round(m["pnl_krw"] / m["priced"], 4) if m["priced"] else None})
    return sorted(out, key=lambda x: x["at"], reverse=True)


def performance(conn: sqlite3.Connection, p=None) -> dict:
    p = p or market.provider()
    out: dict = {"baseline": None, "portfolio": None, "portfolio_note": None, "ai": ai_orders(conn, p)}
    try:
        o = portfolio.overview(conn, p)
    except (TradeError, MarketError, TossError, portfolio.PortfolioError) as e:
        out["portfolio_note"] = f"투자현황을 불러오지 못했어요: {e}"
        out["baseline"] = _baseline_perf(conn, None)
        return out
    out["baseline"] = _baseline_perf(conn, o)
    known = [x for x in o["positions"] if x["cost_krw"] is not None and x["pnl_krw"] is not None]
    cost = sum(x["cost_krw"] for x in known)
    pnl = sum(x["pnl_krw"] for x in known)
    ranked = sorted((x for x in o["positions"] if x.get("pnl_rate") is not None), key=lambda x: x["pnl_rate"])
    brief = lambda x: {"symbol": x["symbol"], "name": x["name"], "where": x["where"], "value_krw": round(x["value_krw"]),  # noqa: E731
                       "weight": round(x["weight"], 4) if x.get("weight") is not None else None,
                       "pnl_rate": round(x["pnl_rate"], 4) if x.get("pnl_rate") is not None else None}
    base = (out["baseline"] or {}).get("amount") or cost  # 기여도 = 그 종목 손익 ÷ 기준 원금(없으면 매입 원금 합)
    items = [{"symbol": x["symbol"], "name": x["name"], "where": x["where"], "quantity": x.get("quantity"),
              "cost_krw": round(x["cost_krw"]) if x.get("cost_krw") is not None else None, "value_krw": round(x["value_krw"]),
              "pnl_krw": round(x["pnl_krw"]) if x.get("pnl_krw") is not None else None,
              "pnl_rate": round(x["pnl_rate"], 4) if x.get("pnl_rate") is not None else None,
              "weight": round(x["weight"], 4) if x.get("weight") is not None else None,
              "contribution": round(x["pnl_krw"] / base, 4) if x.get("pnl_krw") is not None and base else None}
             for x in sorted(o["positions"], key=lambda x: -(x.get("pnl_krw") or 0))]
    out["portfolio"] = {
        "items": items, "contribution_base": "기준 원금" if (out["baseline"] or {}).get("amount") else "매입 원금",
        "total_assets_krw": o["total_assets_krw"], "stock_value_krw": o["stock_value_krw"],
        "cash_krw": o["cash"]["krw"] + o["cash"]["usd_in_krw"], "cash_ratio": o["cash_ratio"], "exposure_ratio": o.get("exposure_ratio"),
        "cost_krw": round(cost), "pnl_krw": round(pnl), "return_rate": round(pnl / cost, 4) if cost else None,
        "positions": len(o["positions"]), "unpriced": len(o["positions"]) - len(known),
        "top": [brief(x) for x in sorted(o["positions"], key=lambda x: -x["value_krw"])[:TOP]],
        "best": brief(ranked[-1]) if ranked else None, "worst": brief(ranked[0]) if ranked else None,
    }
    return out


def short(text: str | None, sentences: int = 2, limit: int = 220) -> str | None:
    """긴 글을 앞 몇 문장으로 — 보고서는 한눈에 보는 것, 전체는 회의록·리서치에 그대로 있다."""
    if not text:
        return text
    out = " ".join(re.split(r"(?<=[.!?])\s+", text.strip())[:sentences])
    return out if len(out) <= limit else out[:limit - 1].rstrip() + "…"


def _kr(symbol: str) -> bool:
    return str(symbol).isdigit() and len(str(symbol)) == 6


def _holdings(p, perf: dict) -> tuple[list[dict], int]:
    """보유 종목 — 평가 금액 큰 순 HOLDINGS 개(나머지는 개수만) + 오늘 등락(국내 종목, 일봉 두 개)."""
    from app.invest.desk_agents import day_change  # desk_agents 가 assistant → 이 모듈을 불러서 늦게

    pf = perf.get("portfolio")
    if not pf:
        return [], 0
    items = sorted(pf["items"], key=lambda x: -x["value_krw"])
    shown = items[:HOLDINGS]
    kr = list(dict.fromkeys(x["symbol"] for x in shown if _kr(x["symbol"])))
    with ThreadPoolExecutor(WORKERS) as ex:
        day = dict(zip(kr, ex.map(lambda s: day_change(p, s), kr)))
    rows = [{k: x[k] for k in ("symbol", "name", "where", "quantity", "value_krw", "weight", "pnl_krw", "pnl_rate")}
            | {"day_change": round(day[x["symbol"]], 4) if day.get(x["symbol"]) is not None else None} for x in shown]
    return rows, len(items) - len(shown)


def _live(folder: Path) -> dict:
    """진행 중인 회의(상시 운용 채널)의 지금까지 — 대화 수, 에이전트마다 마지막 입장으로 센 종목별 매수·매도(많이 말한 3종목),
    트레이더의 마지막 판단."""
    from app.invest.jobs.agent_action import read_messages  # 작업 모듈이 이 모듈보다 많은 것을 불러서 늦게

    names: dict[str, str] = {}
    last: dict[str, dict[str, str]] = {}
    talks, trader = 0, None
    for m in read_messages(folder):
        kind = m.get("type")
        if kind == "round":
            talks += 1
            names.update({c["symbol"]: c.get("name") or c["symbol"] for c in m.get("candidates") or []})
        elif kind == "statement" and not m.get("executor") and not m.get("error"):
            last[m.get("agent", "")] = {v["symbol"]: v["stance"] for v in m.get("views") or [] if not v.get("implicit")}
        elif kind == "decision":
            trader = (m.get("decision") or {}).get("rationale") or trader
    counts: dict[str, dict[str, int]] = {}
    for views in last.values():
        for sym, stance in views.items():
            if stance in ("buy", "sell"):
                counts.setdefault(sym, {"buy": 0, "sell": 0})[stance] += 1
    board = sorted(counts.items(), key=lambda kv: -(kv[1]["buy"] + kv[1]["sell"]))[:3]
    return {"talks": talks, "board": [{"symbol": s, "name": names.get(s, s), **c} for s, c in board],
            "trader": trader[:200] if trader else None}


def _meetings(conn: sqlite3.Connection, ai: dict, limit: int = MEETINGS) -> list[dict]:
    """최근 회의 — 회의록(한 줄·요약·다음에 볼 것)과 그 회의의 주문(체결됐으면 주문 뒤 변화). 진행 중인 회의는 지금까지(_live).
    실패·중지로 끝났는데 회의록도 주문도 없는 회의(서버 재시작으로 끊긴 채널 등)는 뺀다."""
    changes = {o["id"]: o["change"] for o in ai.get("orders") or []}
    out = []
    for r in conn.execute("SELECT id, name, status, created_at, summary_json, artifact_path FROM runs WHERE kind = 'agent_action' "
                          "ORDER BY id DESC LIMIT ?", (limit * 5,)):
        try:
            s = json.loads(r["summary_json"] or "{}")
        except ValueError:
            s = {}
        mins = s.get("minutes") or {}
        orders = [{"name": o["name"] or o["symbol"], "symbol": o["symbol"], "side": o["side"], "quantity": o["quantity"],
                   "status": o["status"], "change": changes.get(o["id"])}
                  for o in conn.execute("SELECT id, symbol, name, side, quantity, status FROM invest_orders WHERE run_id = ? ORDER BY id",
                                        (r["id"],))]
        running = r["status"] in ("running", "queued")
        if not running and not mins.get("headline") and not orders:
            continue
        folder = data_dir() / r["artifact_path"] if r["artifact_path"] else None
        follow: list[str] = []
        if folder and (folder / "minutes.json").exists():
            try:
                follow = [str(x)[:200] for x in json.loads((folder / "minutes.json").read_text(encoding="utf-8")).get("follow_up") or []
                          if not issues.is_note(str(x))][:2]
            except ValueError:
                pass
        out.append({"id": r["id"], "name": r["name"], "status": r["status"], "at": r["created_at"][:16], "kind": mins.get("kind"),
                    "headline": issues.strip_headline(mins.get("headline")) or None, "summary": short(issues.strip_notes(mins.get("summary"))), "follow_up": follow,
                    "orders": orders,
                    "live": _live(folder) if running and folder else None})
        if len(out) >= limit:
            break
    return out


def _recent_news(rows: list[dict], since: str, n: int) -> list[dict]:
    out, seen = [], set()
    for x in rows:
        at = x.get("published_at") or x.get("fetched_at") or ""
        if at[:10] < since or x["title"] in seen:
            continue
        seen.add(x["title"])
        out.append({"title": x["title"], "source": x.get("source") or "", "at": at[:16], "symbols": x.get("symbols") or []})
        if len(out) >= n:
            break
    return out


def _issues(conn: sqlite3.Connection, p, now: datetime, held: dict[str, str]) -> dict:
    """요즘 장내 이슈 — 시장 지표, 시장 전략가의 최근 시황·섹터 애널리스트의 비중 의견(ISSUE_DAYS 안), 시장 뉴스 제목(15분 캐시로
    받아 두고 못 받으면 쌓인 것), 보유 종목 뉴스(에이전트·리서치가 모은 것), 오늘 리스크 경보."""
    since = (now.date() - timedelta(days=ISSUE_DAYS)).isoformat()
    try:
        out: dict = {"indicators": [{"name": i["name"], "price": i["price"], "unit": i.get("unit"), "change_rate": i.get("change_rate")}
                                    for i in p.indicators()], "note": None}
    except (MarketError, TossError) as e:
        out = {"indicators": [], "note": f"시장 지표를 불러오지 못했어요: {e}"}
    mv, sv = desk.latest(conn, "market"), desk.latest(conn, "sector")
    out["view"] = None
    if mv and mv["day"] >= since:
        d = mv["data"]
        out["view"] = {"at": mv["created_at"][5:16], "by": desk.PEOPLE["strategist"]["name"], "regime": desk.REGIME_KO.get(d.get("regime")),
                       "headline": d.get("headline"), "summary": short(d.get("summary")), "favor": d.get("favor") or [],
                       "avoid": d.get("avoid") or [], "cash": desk.CASH_KO.get(d.get("cash"))}
    out["sectors"] = None
    if sv and sv["day"] >= since:
        d = sv["data"]
        rates = {str(x.get("id")): x.get("rates") or {} for x in d.get("rows") or []}
        row = lambda x, side: {"name": x["name"], "side": side, "reason": short(x.get("reason") or "", 1, 70),  # noqa: E731
                               "rates": rates.get(str(x.get("id")), {})}
        out["sectors"] = {"at": sv["created_at"][5:16], "overweight": [{"name": x["name"], "reason": short(x.get("reason") or "", 1, 70)}
                                                                        for x in d.get("overweight") or []],
                          "underweight": [x["name"] for x in d.get("underweight") or []],
                          "rows": [row(x, "over") for x in d.get("overweight") or []] + [row(x, "under") for x in d.get("underweight") or []]}
    try:
        news.collect(conn, [], True, per_query=8, on_error=lambda _: None)
    except Exception:  # noqa: BLE001 — 뉴스는 곁들이는 자료라 무엇이 실패해도 쌓인 것으로 보고서를 만든다
        pass
    out["news"] = _recent_news(news.recent(conn, market=True, limit=40), since, NEWS)
    per: dict[str, int] = {}
    hold_news = []
    for x in _recent_news(news.recent(conn, symbols=list(held), limit=80), since, 80) if held else []:
        sym = next((s for s in x["symbols"] if s in held and per.get(s, 0) < HOLDING_NEWS), None)
        if sym and len(hold_news) < HOLDING_NEWS * 3:
            per[sym] = per.get(sym, 0) + 1
            hold_news.append({**x, "symbol": sym, "name": held[sym]})
    out["holding_news"] = hold_news
    out["alerts"] = [a["data"].get("text", "") for a in desk.alerts(conn, now.date())][-3:]
    return out


def snapshot(conn: sqlite3.Connection, p=None, now: datetime | None = None) -> dict:
    """한 페이지 보고서 — 관리자가 계속 볼 것만: 보유 종목·현황(수익률·목표 대비), 최근 회의 요약, 요즘 장내 이슈, 막힌 것."""
    p = p or market.provider()
    now = now or market.now_kst()
    perf = performance(conn, p)
    rows, more = _holdings(p, perf)
    issues = _issues(conn, p, now, {x["symbol"]: x["name"] or x["symbol"] for x in rows})
    return {"title": "투자 현황 보고", "v": 2, "created_at": now.strftime("%Y-%m-%d %H:%M"), "source": p.key,
            "warnings": [w for w in (autopilot.blocked(conn),) if w], "performance": perf, "holdings": rows, "holdings_more": more,
            "history": history(conn, p, perf, issues["indicators"], now), "policy": goal_check(conn, p, perf),
            "meetings": _meetings(conn, perf["ai"]), "market": issues}


def _base_close(closes: list[dict], since: str) -> float | None:
    """since 날짜 전 마지막 종가 — 기준 원금 이후 지수 변화의 출발점."""
    return next((c["c"] for c in reversed(closes) if c["t"] < since), None)


def benchmark_change(p, symbol: str, since: str) -> float | None:
    """그 지수의 since 날짜 전 마지막 종가 → 지금 변화율 (기준 원금 이후 지수 수익률)."""
    try:
        closes = p.indicator_closes(symbol, 250)
        now_price = next((i["price"] for i in p.indicators() if i["symbol"] == symbol), None)
    except (MarketError, TossError):
        return None
    base = _base_close(closes, since)
    last = now_price or (closes[-1]["c"] if closes else None)
    return round(last / base - 1, 4) if base and last else None


def history(conn: sqlite3.Connection, p, perf: dict, indicators: list[dict], now: datetime) -> dict | None:
    """운용 수익률 추이 — 지나간 보고서에 남은 수익률(따로 쌓는 기록 없이, 기준 원금 시작일이 같은 것만)을 잇고 끝에 지금을 붙인다.
    index = 같은 시각의 지수(운용 목표의 벤치마크, 없으면 REFERENCE)가 기준 원금 시작 전 종가에서 움직인 만큼 — 보고서에 남은 지수 값으로."""
    bl = perf.get("baseline")
    if not bl or bl.get("return_rate") is None:
        return None
    bm = policy.get(conn).get("benchmark")
    symbol = bm if bm and bm != "none" else REFERENCE
    name = policy.show("benchmark", symbol)
    try:
        base = _base_close(p.indicator_closes(symbol, 250), bl["since"])
    except (MarketError, TossError):
        base = None

    def index(rows: list[dict]) -> float | None:
        level = next((i.get("price") for i in rows if i.get("name") == name), None)
        return round(level / base - 1, 4) if base and level else None

    points = []
    for row in conn.execute("SELECT meta FROM invest_assistant_messages WHERE role = 'event' AND json_extract(meta, '$.kind') = 'report' "
                            "ORDER BY id DESC LIMIT ?", (HISTORY,)):
        try:
            r = json.loads(row["meta"]).get("report") or {}
        except ValueError:
            continue
        b = (r.get("performance") or {}).get("baseline") or {}
        if b.get("since") == bl["since"] and b.get("return_rate") is not None:
            points.append({"at": r["created_at"], "rate": b["return_rate"], "index": index((r.get("market") or {}).get("indicators") or [])})
    points.reverse()
    start = [{"at": bl["since"], "rate": 0.0, "index": 0.0 if base else None}] if len(points) < HISTORY else []
    return {"index": name if base else None,
            "points": [*start, *points, {"at": now.strftime("%Y-%m-%d %H:%M"), "rate": bl["return_rate"], "index": index(indicators)}]}


def goal_check(conn: sqlite3.Connection, p, perf: dict) -> dict:
    """운용 목표 대비 지금 — 주식 비중(레버리지는 노출액으로), 수익률(기준 원금 대비, 1년 목표를 지난 날만큼 나눠 본 값), 벤치마크 대비."""
    pol = policy.get(conn)
    out: dict = {"lines": [f"{policy.FIELDS[k]['label']}: {policy.show(k, pol[k])}" for k in policy.FIELDS if pol.get(k) is not None],
                 "missing": [policy.FIELDS[k]["label"] for k in policy.missing(pol)], "updated_at": pol.get("updated_at"),
                 "equity": None, "return": None, "benchmark": None}
    pf, bl = perf.get("portfolio"), perf.get("baseline")
    if pol.get("equity_target") is not None and pf and pf.get("cash_ratio") is not None:
        ratio = pf["exposure_ratio"] if pf.get("exposure_ratio") is not None else 1 - pf["cash_ratio"]
        out["equity"] = {"target": pol["equity_target"], "actual": round(ratio * 100, 1)}
    if bl and bl.get("return_rate") is not None:
        days = max(bl.get("days") or 0, 1)
        out["return"] = {"actual": bl["return_rate"], "days": days, "target_year": pol.get("target_return"),
                         "target_so_far": round(pol["target_return"] / 100 * days / 365, 4) if pol.get("target_return") is not None else None}
        bm = pol.get("benchmark")
        if bm and bm != "none" and bl.get("since"):
            change = benchmark_change(p, bm, bl["since"])
            if change is not None:
                out["benchmark"] = {"symbol": bm, "name": policy.show("benchmark", bm), "change": change,
                                    "excess": round(bl["return_rate"] - change, 4), "since": bl["since"]}
    return out


def won(n: float | None) -> str:
    return "-" if n is None else f"{round(n):,}원"


def pct(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:+.1f}%"


def _order_line(o: dict) -> str:
    change = f", 주문 뒤 {pct(o['change'])}" if o.get("change") is not None else ""
    return f"{o['name']} {SIDE_KO.get(o['side'], o['side'])} {o['quantity']:g}주 ({ORDER_KO.get(o['status'], o['status'])}{change})"


def goal_lines(g: dict | None) -> list[str]:
    """목표 대비 지금 — 주식 비중·수익률(1년 목표를 지난 날만큼)·벤치마크 대비. 정한 목표가 없으면 []."""
    if not g:
        return []
    out = []
    if g.get("equity"):
        out.append(f"주식 비중 {g['equity']['actual']:g}% / 목표 {g['equity']['target']:g}%")
    if g.get("return") and g["return"]["target_year"] is not None:
        rt = g["return"]
        out.append(f"수익률 {pct(rt['actual'])}({rt['days']}일) / 목표 연 {rt['target_year']:g}%(지금까지 {pct(rt['target_so_far'])})")
    if g.get("benchmark"):
        b = g["benchmark"]
        out.append(f"{b['name']} {pct(b['change'])} 대비 {b['excess'] * 100:+.1f}%p")
    return out


def to_text(r: dict) -> str:
    """보고서를 글로 (복사·붙여넣기용, 아침 의견을 쓰는 비서가 읽는 글)."""
    perf, mk = r["performance"], r["market"]
    pf, bl = perf["portfolio"], perf.get("baseline")
    lines = [f"■ {r['title']} · {r['created_at']}" + (" · 샘플 시세" if r.get("source") == "sample" else "")]
    if r.get("comment"):
        lines += ["", r["comment"]]
    if r.get("warnings"):
        lines += ["", "[주의]"] + [f"- {w}" for w in r["warnings"]]

    lines += ["", "[보유 종목·현황]"]
    if bl:
        lines.append(f"- 운용 수익률 {pct(bl['return_rate'])} — 토스 계좌 {won(bl['value_krw'])} / 기준 원금 {won(bl['amount'])}"
                     f"({bl['since']}부터, {bl['days']}일) · 손익 {won(bl['pnl_krw'])}")
    if pf:
        lines.append(f"- 총 자산 {won(pf['total_assets_krw'])} · 주식 {won(pf['stock_value_krw'])} · 현금 {won(pf['cash_krw'])}"
                     + (f"({pf['cash_ratio'] * 100:.0f}%)" if pf["cash_ratio"] is not None else "")
                     + f" · 평가 손익 {won(pf['pnl_krw'])}({pct(pf['return_rate'])})")
        goal = goal_lines(r.get("policy"))
        if goal:
            lines.append("- 목표 대비: " + " · ".join(goal))
        for x in r["holdings"]:
            where = "" if x["where"] == "토스" else f" [{x['where']}]"
            lines.append(f"- {x['name']}{where} {x['quantity']:g}주 {won(x['value_krw'])}"
                         + (f"({x['weight'] * 100:.0f}%)" if x["weight"] is not None else "")
                         + (f" · 손익 {pct(x['pnl_rate'])}({won(x['pnl_krw'])})" if x["pnl_rate"] is not None else "")
                         + (f" · 오늘 {pct(x['day_change'])}" if x["day_change"] is not None else ""))
        if not r["holdings"]:
            lines.append("- 보유 종목 없음 — 모두 현금")
        if r.get("holdings_more"):
            lines.append(f"- 외 {r['holdings_more']}종목")
    else:
        lines.append(f"- {perf['portfolio_note']}")

    lines += ["", "[회의 요약]"]
    for m in r["meetings"]:
        live = m.get("live")
        head = f"- {m['at'][5:]} {m['name']}" + (" (진행 중)" if live else "")
        if m.get("headline"):
            head += f" — {m['headline']}"
        elif live:
            head += f" — 대화 {live['talks']}번 · 주문 {len(m['orders'])}건"
        lines.append(head)
        if m.get("summary"):
            lines.append(f"  {m['summary']}")
        if live and live["board"]:
            lines.append("  지금 입장: " + " · ".join(f"{b['name']} 매수 {b['buy']}·매도 {b['sell']}" for b in live["board"]))
        if live and live["trader"]:
            lines.append(f"  트레이더: {live['trader']}")
        if m["orders"]:
            lines.append("  주문: " + ", ".join(_order_line(o) for o in m["orders"]))
        lines += [f"  다음에 볼 것: {x}" for x in m.get("follow_up") or []]
    if not r["meetings"]:
        lines.append("- 아직 회의가 없어요")

    lines += ["", "[장내 이슈]"]
    ind = [f"{i['name']} {i['price']:,.2f}{i.get('unit') or ''}({pct(i['change_rate'])})" for i in mk["indicators"] if i.get("price") is not None]
    lines.append(f"- 지표: {' · '.join(ind)}" if ind else f"- {mk['note'] or '시장 지표 없음'}")
    v = mk.get("view")
    if v:
        lines.append(f"- 시황 {v['at']} {v['by']}: {v['regime'] or '중립'} — {v['headline'] or ''}" + (f" · {v['cash']}" if v["cash"] else ""))
        if v.get("summary"):
            lines.append(f"  {v['summary']}")
        if v["favor"] or v["avoid"]:
            lines.append(f"  주목 {', '.join(v['favor']) or '없음'}" + (f" · 피할 것 {', '.join(v['avoid'])}" if v["avoid"] else ""))
    sc = mk.get("sectors")
    if sc and (sc["overweight"] or sc["underweight"]):
        lines.append(f"- 섹터 {sc['at']}: 비중 확대 " + ("; ".join(f"{x['name']}({x['reason'].rstrip('.')})" if x["reason"] else x["name"] for x in sc["overweight"]) or "없음")
                     + (f" · 축소 {', '.join(sc['underweight'])}" if sc["underweight"] else ""))
    lines += [f"- 뉴스: {n['title']} ({n['source']} {n['at'][5:]})" for n in mk.get("news") or []]
    lines += [f"- {n['name']} 뉴스: {n['title']} ({n['source']} {n['at'][5:]})" for n in mk.get("holding_news") or []]
    lines += [f"- 경보: {a}" for a in mk.get("alerts") or []]
    return "\n".join(lines)
