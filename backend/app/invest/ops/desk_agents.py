"""운용 데스크 에이전트 — 리서치 세 단계(시장 분석 → 섹터 탐색 → 종목 발굴)와 장 중 리스크 감시, 그리고 언제 무엇을 할지(tick).
사람·기록·안건은 desk.py. 숫자는 모두 코드(토스 API·지표), LLM 은 표를 보고 판단과 이유만 쓴다 — 실패하면 코드가 정한다.

1) 시장 분석(시장 전략가, market_view): 지수·금리(market.indicators), 장 운영, 시장 랭킹 상승·하락 상위 평균, 거래대금 상위, 시장 뉴스
   제목(news — Google 뉴스 RSS) → 국면(risk_on·neutral·risk_off)·헤드라인·시황·주목/피할 테마·현금 의견. 코드 대체: 코스피 ±REGIME_MOVE.
2) 섹터 탐색(섹터 애널리스트, sector_view): 토스 섹터 대·중분류 전체의 기간 등락(1일·1주·1개월·3개월)을 훑어 모멘텀 점수(MOMENTUM_W 가중)
   → 비중 확대(최대 MAX_OVERWEIGHT)·축소(최대 MAX_UNDERWEIGHT) 섹터와 이유. 표에 없는 섹터는 버리고, 대분류와 그 안의 중분류를 함께 확대하지 않는다.
   코드 대체: 운용 지시에 나온 섹터 + 점수 상위로 3개 확대, 점수가 음수인 하위 2개 축소. 전체 표는 판단 기록(scorelog, kind sector)에.
3) 종목 발굴(종목 발굴 애널리스트, scout): OpenDART 재무를 받고(valuation.refresh, 하루 한 번) 시장 전체 가치 스크린(kind value) → 확대 섹터의
   시가총액 상위·거래대금 상위(중소형) + 가치 상위 + 시장 랭킹 + 운용 지시 종목 + 관심 → screening 점수(시장 국면이 가중치를 바꿈) → LLM 이 IDEAS 개를
   이유와 함께 고른다(SCOUT_SYSTEM). 보유 종목은 표에서 빼고(회의 때 포트폴리오 매니저가 본다), 한 섹터 screening.MAX_PER_SECTOR 개를 코드가
   강제하고, 코드로 고를 때는 급등 직후·과열을 건너뛴다. 고른 것·뺀 것·지나친 것 전부 판단 기록(kind ideas)에.
4) 리스크 감시(리스크 매니저, watch — LLM 없음): 보유(국내)·발굴 종목의 오늘 등락(일봉 마지막 두 개) → 보유 −HOLD_DROP 이하 급락·
   +HOLD_JUMP 이상 급등(차익 검토), 발굴 종목 +IDEA_BREAKOUT 이상 급등. 같은 종목·종류는 하루 한 번. 경보는 invest_desk·채팅방(리스크
   매니저 말풍선)에 남기고, 자동 운용이면 그 종목으로 수시 회의(autopilot.run_adhoc) — 토론 중이면 다음 감시 때 다시(ADHOC_WAIT_MIN 분 안).
tick(서비스가 20초마다): 자동 운용이 켜져 있고 장이 열리는 날만 — 정규장 시작 PREP_MIN 분 전부터 아침 리서치 한 번(결과 없이 끝났으면
RETRY_MIN 분 뒤 다시, 하루 MORNING_TRIES 번까지), 장 중 WATCH_MIN 분마다
리스크 감시, 지수가 마지막 분석 때보다 REFRESH_MOVE 넘게 움직이면 리서치를 다시(하루 MAX_REFRESH 번, REFRESH_GAP_MIN 분 간격).
운용 목표(policy.text)는 세 단계 프롬프트에 모두 들어가고, 선호 섹터는 운용 지시 섹터처럼 [운용 지시] 표시.
채팅방: 단계마다 그 에이전트의 말풍선 + 카드(invest_assistant_messages, role=agent, meta.desk = market·sector·ideas·alert).
"""

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from app.invest.advice import assistant
from app.invest.analysis import dart, policy, scorelog, screening, valuation
from app.invest.broker import trading
from app.invest.broker.toss import TossError
from app.invest.broker.trading import TradeError
from app.invest.debate import agents, scopes
from app.invest.marketdata import market, news
from app.invest.marketdata.market import MarketError
from app.invest.ops import autopilot, desk
from app.invest.ops.desk import CASH_KO, REGIME_KO
from app.llm.client import LLMError, structured
from app.llm.routes import as_agent

REGIME_MOVE = 0.008       # 코드 대체: 코스피가 이만큼 넘게 오르면 위험 선호, 내리면 위험 회피
NEWS_TITLES = 10
WORKERS = 4
RATE_KEYS = ("1d", "1w", "1mo", "3mo")
RATE_KO = {"1d": "1일", "1w": "1주", "1mo": "1개월", "3mo": "3개월"}
MOMENTUM_W = {"1w": 0.3, "1mo": 0.5, "3mo": 0.2}
SECTOR_ROWS = 30          # LLM 에게 보여 줄 위쪽 섹터 줄 수 (+ 아래쪽 SECTOR_BOTTOM + 운용 지시 섹터)
SECTOR_BOTTOM = 10        # 축소를 고를 수 있게 약한 섹터도 보여 준다
MAX_OVERWEIGHT = 4
MAX_UNDERWEIGHT = 3
IDEAS = 8
WATCH_MAX = 20            # 리스크 감시 종목 수
HOLD_DROP = 0.04
HOLD_JUMP = 0.06
IDEA_BREAKOUT = 0.05
ADHOC_WAIT_MIN = 30
ALERT_KO = {"drop": "보유 종목 급락", "jump": "보유 종목 급등 — 차익 실현 검토", "breakout": "발굴 종목 급등"}


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:+.1f}%"


def _minutes(hhmm: str) -> int:
    h, m = hhmm[:5].split(":")
    return int(h) * 60 + int(m)


Llm = Callable[[str, str, str, dict, str | None], Awaitable[dict]]


async def _default_llm(feature: str, system: str, prompt: str, schema: dict, model: str | None) -> dict:
    return await structured(feature, prompt, schema, model=model, system_prompt=system)


LLM: Llm = _default_llm  # 테스트에서 가짜로 바꾼다 (종목 발굴은 screening.LLM)


def _ask(system: str, prompt: str, schema: dict, model: str | None) -> dict | None:
    try:
        raw = asyncio.run(LLM("invest_desk", system, prompt, schema, model))
    except (LLMError, TypeError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _who(key: str) -> str:
    p = desk.PEOPLE[key]
    return f"{p['name']}({p['full_name']})"


# ---- 1) 시장 분석 ----
def market_facts(conn, p, notes: list[str]) -> dict:
    f: dict = {"indicators": [], "session": None, "breadth": {}, "leaders": [], "news": []}
    try:
        f["indicators"] = [{k: i.get(k) for k in ("symbol", "name", "unit", "price", "change_rate")} for i in p.indicators()]
    except (MarketError, TossError) as e:
        notes.append(f"시장 지표를 받지 못했어요: {e}")
    try:
        f["session"] = p.session()
    except (MarketError, TossError):
        pass
    for kind in ("gainers", "losers"):
        try:
            rows = [r for r in p.rankings(kind, 20, conn=conn) if r.get("change_rate") is not None]
        except (MarketError, TossError) as e:
            notes.append(f"시장 랭킹을 받지 못했어요: {e}")
            continue
        if rows:
            f["breadth"][kind] = {"avg": sum(r["change_rate"] for r in rows) / len(rows),
                                  "top": [f"{r['name'] or r['symbol']} {_pct(r['change_rate'])}" for r in rows[:3]]}
    try:
        f["leaders"] = [{"symbol": r["symbol"], "name": r["name"] or r["symbol"], "change_rate": r.get("change_rate")}
                        for r in p.rankings("amount", 10, conn=conn)]
    except (MarketError, TossError):
        pass
    try:
        f["news"] = [{"title": n["title"], "source": n.get("source"), "at": (n.get("published_at") or "")[:16]}
                     for n in (news.collect(conn, [], True, per_query=8, on_error=notes.append).get(None) or [])[:NEWS_TITLES]]
    except Exception as e:  # noqa: BLE001 — 뉴스는 곁들이는 자료라 무엇이 실패해도 분석은 이어 간다
        notes.append(f"시장 뉴스를 받지 못했어요: {e}")
    return f


def market_text(f: dict, intents: str, reason: str = "", goal: str = "") -> str:
    lines = ["지표:"] + [f"- {i['name']} {i['price']}{i.get('unit') or ''} ({_pct(i['change_rate'])})" for i in f["indicators"]]
    s = f.get("session") or {}
    lines.append(f"장 운영: {s.get('date') or '-'} {s.get('start') or '휴장'}~{s.get('end') or ''}"
                 + (" · 지금 정규장" if s.get("is_open") else " · 지금 장 밖"))
    for kind, ko in (("gainers", "상승 상위 20"), ("losers", "하락 상위 20")):
        b = f["breadth"].get(kind)
        if b:
            lines.append(f"{ko} 평균 {_pct(b['avg'])} — {', '.join(b['top'])}")
    if f["leaders"]:
        lines.append("거래대금 상위: " + ", ".join(f"{x['name']} {_pct(x['change_rate'])}" for x in f["leaders"]))
    if f["news"]:
        lines += ["시장 뉴스 제목:"] + [f"- {n['title']} ({n['source'] or ''} {n['at']})" for n in f["news"]]
    lines.append(f"\n운용 지시:\n{intents or '없음'}")
    if goal:
        lines.append(f"\n운용 목표:\n{goal}")
    if reason:
        lines.append(f"\n이번에 다시 보는 이유: {reason}")
    return "\n".join(lines)


MARKET_SCHEMA = {
    "type": "object",
    "properties": {
        "regime": {"type": "string", "enum": list(REGIME_KO)},
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "favor": {"type": "array", "items": {"type": "string"}},
        "avoid": {"type": "array", "items": {"type": "string"}},
        "cash": {"type": "string", "enum": list(CASH_KO)},
    },
    "required": ["regime", "headline", "summary", "favor", "avoid", "cash"],
}
MARKET_SYSTEM = f"""당신은 자산운용사의 시장 전략가 {_who('strategist')}입니다. 장 시작 전(또는 장 중 시장이 크게 움직였을 때) 시장 자료를 보고
오늘의 시장 국면을 정해 투자팀(섹터 애널리스트·종목 발굴·투자위원회)에 알립니다.
- regime: risk_on(위험 선호) · neutral(중립) · risk_off(위험 회피). 지수·금리·상승/하락 상위 평균·거래대금 상위·뉴스 제목을 근거로.
- headline 은 한 줄(30자 안팎), summary 는 2~4문장 시황 — 자료에 있는 숫자만 쓰세요.
- favor·avoid: 오늘 주목할/피할 테마·업종 0~4개(짧은 말). cash: 현금 비중 의견 raise(늘리기)·keep(유지)·lower(줄이기 = 더 투자).
- 운용 지시가 있으면 그 성향(공격적·보수적)을 반영하세요. 수익을 장담하지 말고, 한국어로."""


def code_market(f: dict) -> dict:
    k = next((i for i in f["indicators"] if i.get("symbol") == "KOSPI"), None)
    cr = (k or {}).get("change_rate")
    regime = "neutral" if cr is None else "risk_on" if cr >= REGIME_MOVE else "risk_off" if cr <= -REGIME_MOVE else "neutral"
    head = f"코스피 {_pct(cr)} · {REGIME_KO[regime]}" if cr is not None else "시장 지표가 없어 중립으로 봐요"
    return {"regime": regime, "headline": head, "summary": f"코드가 코스피 등락(±{REGIME_MOVE * 100:.1f}% 기준)으로 국면을 정했어요.",
            "favor": [], "avoid": [], "cash": "raise" if regime == "risk_off" else "keep", "by": "code"}


def _strs(v: object, n: int = 4) -> list[str]:
    return [str(x).strip()[:30] for x in v if str(x).strip()][:n] if isinstance(v, list) else []


def market_view(conn, p, intents: str, model: str | None, notes: list[str], reason: str = "", goal: str = "") -> dict:
    f = market_facts(conn, p, notes)
    with as_agent("strategist"):
        raw = _ask(MARKET_SYSTEM, market_text(f, intents, reason, goal), MARKET_SCHEMA, model)
    if raw and raw.get("regime") in REGIME_KO and str(raw.get("headline") or "").strip():
        view = {"regime": raw["regime"], "headline": str(raw["headline"]).strip()[:80], "summary": str(raw.get("summary") or "").strip()[:600],
                "favor": _strs(raw.get("favor")), "avoid": _strs(raw.get("avoid")),
                "cash": raw.get("cash") if raw.get("cash") in CASH_KO else "keep", "by": "llm"}
    else:
        view = code_market(f)
    return {**view, "indicators": f["indicators"], "breadth": f["breadth"], "leaders": f["leaders"][:5],
            "news": f["news"][:5], "source": p.key}


# ---- 2) 섹터 탐색 ----
def momentum(rates: dict) -> float | None:
    got = [(w, rates[k]) for k, w in MOMENTUM_W.items() if rates.get(k) is not None]
    if not got:
        return None
    return round(sum(w * r for w, r in got) / sum(w for w, _ in got), 4)


def sector_table(p, notes: list[str]) -> list[dict]:
    """섹터 전체 (대·중분류) — [{id, name, depth, parent, rates, score}] 점수 높은 순. 처음엔 섹터마다 한 번씩 묻고(토스 초당 5회) 10분 캐시."""
    try:
        secs = scopes.sectors(p)
    except scopes.ScopeError as e:
        notes.append(f"섹터 목록을 받지 못했어요: {e}")
        return []

    def perf(sec: dict) -> dict | None:
        try:
            return p.sector_perf(sec["id"])
        except (MarketError, TossError):
            return None

    with ThreadPoolExecutor(WORKERS) as ex:
        perfs = list(ex.map(perf, secs))
    rows, missed = [], 0
    for sec, pf in zip(secs, perfs):
        if pf is None:
            missed += 1
            continue
        rates = {k: pf["change_rates"][k] for k in RATE_KEYS if pf["change_rates"].get(k) is not None}
        rows.append({"id": sec["id"], "name": pf.get("name") or sec["name"], "depth": sec.get("depth") or 0, "parent": sec.get("parent"),
                     "rates": rates, "score": momentum(rates)})
    if missed:
        notes.append(f"섹터 {missed}개는 등락을 받지 못했어요")
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    return rows


def _score(v: float | None) -> str:
    """모멘텀 점수를 % 로 (화면 섹터 표와 같은 단위)."""
    return "-" if v is None else f"{v * 100:.1f}"


def sector_text(rows: list[dict], flagged: set[str], view: dict, intents: str, goal: str = "") -> str:
    tail = rows[SECTOR_ROWS:]
    bottom = [r for r in tail[-SECTOR_BOTTOM:] if r["score"] is not None and r["score"] < 0]
    shown = rows[:SECTOR_ROWS] + [r for r in tail if r["id"] in flagged and r not in bottom] + bottom
    lines = [f"- [{r['id']}] {r['name']} ({'대분류' if r['depth'] == 0 else '중분류'}) · "
             + " · ".join(f"{RATE_KO[k]} {_pct(v)}" for k, v in r["rates"].items())
             + f" · 점수 {_score(r['score'])}" + (" [운용 지시]" if r["id"] in flagged else "")
             for r in shown]
    return (f"시장 분석: {REGIME_KO[view['regime']]} — {view['headline']}"
            + (f" · 주목 {', '.join(view['favor'])}" if view.get("favor") else "")
            + (f" · 피할 것 {', '.join(view['avoid'])}" if view.get("avoid") else "")
            + f"\n\n운용 지시:\n{intents or '없음'}" + (f"\n\n운용 목표:\n{goal}" if goal else "") + f"\n\n섹터 기간 등락 (점수 = 1주·1개월·3개월 가중 등락 %, 위쪽·아래쪽 {len(shown)}/{len(rows)}개):\n"
            + "\n".join(lines))


SECTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "overweight": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "reason": {"type": "string"}},
                                                   "required": ["id", "reason"]}},
        "underweight": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "reason": {"type": "string"}},
                                                    "required": ["id", "reason"]}},
        "summary": {"type": "string"},
    },
    "required": ["overweight", "underweight", "summary"],
}
SECTOR_SYSTEM = f"""당신은 자산운용사의 섹터 애널리스트 {_who('sector')}입니다. 토스증권 섹터 전체의 기간 등락표와 시장 전략가의 오늘 시장 분석,
운용 지시를 보고 비중을 늘릴 섹터(overweight)와 줄일 섹터(underweight)를 고릅니다. 종목 발굴 애널리스트가 확대 섹터에서 종목을 찾습니다.
- overweight 1~{MAX_OVERWEIGHT}개, underweight 0~{MAX_UNDERWEIGHT}개, 표에 있는 id 만. 섹터마다 한 문장 이유(표의 숫자 근거).
- [운용 지시] 섹터는 먼저 검토하세요 — 지시를 따르되 숫자가 나쁘면 이유에 위험을 적으세요.
- 시장이 위험 회피면 방어적인 섹터를, 위험 선호면 추세가 강한 섹터를 우선. 대분류와 그 안의 중분류를 함께 고르지 마세요.
- summary 는 한두 문장. 한국어."""


def _related(r: dict, others: list[dict]) -> bool:
    """대분류와 그 안의 중분류 — 함께 확대하면 같은 종목을 두 번 보는 셈이다."""
    return any(r.get("parent") == x["id"] or x.get("parent") == r["id"] for x in others)


def sector_rules() -> dict:
    return {"momentum_weights": MOMENTUM_W, "max_overweight": MAX_OVERWEIGHT, "max_underweight": MAX_UNDERWEIGHT, "sector_rows": SECTOR_ROWS,
            "sector_bottom": SECTOR_BOTTOM, "code_regime_move": REGIME_MOVE}


def code_sector(rows: list[dict], flagged: set[str]) -> dict:
    over = [r for r in rows if r["id"] in flagged][:MAX_OVERWEIGHT]
    for r in rows:
        if len(over) >= 3:
            break
        if r not in over and r["score"] is not None and not _related(r, over):
            over.append(r)
    under = [r for r in reversed(rows) if r["score"] is not None and r["score"] < 0 and r not in over][:2]
    why = lambda r: " · ".join(f"{RATE_KO[k]} {_pct(v)}" for k, v in r["rates"].items())  # noqa: E731
    return {"overweight": [{"id": r["id"], "name": r["name"], "reason": ("운용 지시 · " if r["id"] in flagged else "") + why(r)}
                           for r in over],
            "underweight": [{"id": r["id"], "name": r["name"], "reason": why(r)} for r in under],
            "summary": "코드가 기간 등락 점수 순으로 골랐어요", "by": "code"}


def _log_sectors(conn, rows: list[dict], out: dict, flagged: set[str], view: dict, run_id: int | None, notes: list[str]) -> None:
    why = {x["id"]: ("overweight", x["reason"]) for x in out["overweight"]} | {x["id"]: ("underweight", x["reason"]) for x in out["underweight"]}
    scorelog.record(conn, "sector", [{"subject": r["id"], "name": r["name"], "score": r["score"], "decision": why.get(r["id"], ("passed", ""))[0],
                                      "reason": why.get(r["id"], ("", ""))[1],
                                      "detail": {"rates": r["rates"], "depth": r["depth"], "parent": r.get("parent"), "intent": r["id"] in flagged}}
                                     for r in rows],
                    sector_rules(), run_id=run_id, regime=view["regime"], by=out["by"], summary=out["summary"], notes=notes)


def sector_view(conn, p, view: dict, intents: str, model: str | None, notes: list[str], goal: str = "",
                prefer: list[str] = (), run_id: int | None = None) -> dict:
    rows = sector_table(p, notes)
    flagged = {s["id"] for s in scopes.intent_sectors(p, "\n".join([intents, *prefer]))}
    out = None
    if rows:
        with as_agent("sector"):
            raw = _ask(SECTOR_SYSTEM, sector_text(rows, flagged, view, intents, goal), SECTOR_SCHEMA, model)
        by_id = {r["id"]: r for r in rows}

        def keep(items: object, limit: int, taken: set[str]) -> list[dict]:
            got = []
            for x in items if isinstance(items, list) else []:
                sid = str((x or {}).get("id") or "").strip()
                if sid in by_id and sid not in taken and len(got) < limit and not _related(by_id[sid], [by_id[g["id"]] for g in got]):
                    taken.add(sid)
                    got.append({"id": sid, "name": by_id[sid]["name"], "reason": str(x.get("reason") or "").strip()[:200]})
            return got

        if raw:
            taken: set[str] = set()
            over = keep(raw.get("overweight"), MAX_OVERWEIGHT, taken)
            if over:
                out = {"overweight": over, "underweight": keep(raw.get("underweight"), MAX_UNDERWEIGHT, taken),
                       "summary": str(raw.get("summary") or "").strip()[:300], "by": "llm"}
        if out is None:
            out = code_sector(rows, flagged)
    else:
        out = {"overweight": [], "underweight": [], "summary": "섹터 자료가 없어 고르지 못했어요", "by": "code"}
    _log_sectors(conn, rows, out, flagged, view, run_id, notes)
    picked = {x["id"]: "over" for x in out["overweight"]} | {x["id"]: "under" for x in out["underweight"]}
    return {**out, "count": len(rows),
            "rows": [{**r, "pick": picked.get(r["id"]), "intent": r["id"] in flagged} for r in rows[:15]]
            + [{**r, "pick": picked[r["id"]], "intent": r["id"] in flagged} for r in rows[15:] if r["id"] in picked]}


# ---- 3) 종목 발굴 ----
SCOUT_SYSTEM = f"""당신은 자산운용사의 종목 발굴 애널리스트 {_who('scout')}입니다. 섹터 애널리스트가 고른 비중 확대 섹터의 대표 종목·거래가 붙은 중소형주,
시장 전체 가치 스크린 상위, 시장 랭킹, 운용 지시·관심 종목을 훑은 점수표를 보고, 오늘 투자위원회가 검토할 새 종목 목록을 만듭니다(포트폴리오 매니저가
이 목록과 보유를 보고 회의 안건을 정함). 보유 종목은 회의에서 따로 보므로 표에서 뺐습니다.
- 비중 확대 섹터(출처 '섹터 …')에서 먼저 고르되, 가치 상위와 랭킹에서 새로 떠오른 종목도 섞으세요. 운용 지시에 나온 종목은 넣으세요.
- 이미 오른 종목만 고르지 마세요 — 추세·눌림목·가치를 나눠 담고, 위험 회피 장이면 싸고 재무가 튼튼한 종목을 앞에 두세요.
- 점수는 참고 — 과열(RSI 75+)·급등 직후는 이유가 있을 때만. 한 섹터는 {screening.MAX_PER_SECTOR}개까지(넘치면 코드가 뺍니다).
- 가치 점수·PER·PBR·ROE·적정가치 대비는 공시 재무로 코드가 계산한 값입니다. 없는 종목은 평가가 없는 것이지 나쁜 것이 아닙니다.
- 표에 있는 종목(코드)만 고르고, 종목마다 한 문장 이유(점수표 숫자·섹터 근거). summary 는 오늘 발굴 방향 한 문장. 한국어."""


def _held(conn, p, notes: list[str]) -> set[str]:
    try:
        return {h["symbol"] for h in trading.account(conn, market=p).summary()["holdings"]}
    except (TradeError, MarketError, TossError) as e:
        notes.append(f"보유 종목을 불러오지 못해 발굴에서 빼지 못했어요: {e}")
        return set()


def _log_value(conn, top: dict, picks: set[str], regime: str, run_id: int | None, notes: list[str]) -> None:
    rows = [{"subject": r["symbol"], "name": r["name"], "score": r["valuation"]["score"], "value_score": r["valuation"]["score"],
             "decision": "picked" if r["symbol"] in picks else "passed", "sources": [r["sector"]], "detail": {"sector": r["sector"], "valuation": r["valuation"]}}
            for r in top["rows"]]
    scorelog.record(conn, "value", rows, screening.rules(), run_id=run_id, regime=regime, pool=top["scored"], by="code",
                    summary=f"{top['universe']}종목 중 {top['scored']}종목을 평가해 상위 {len(picks)}개를 발굴 풀로", notes=notes)


def scout(conn, p, mv: dict, sv: dict, intents: str, model: str | None, notes: list[str], goal: str = "", run_id: int | None = None) -> dict:
    named = scopes.intent_symbols(conn, p, intents)
    secs = [{"id": x["id"], "name": x["name"]} for x in sv["overweight"]]
    regime = mv["regime"]
    held = _held(conn, p, notes)
    index = valuation.sector_index(p, notes)
    top = valuation.screen(conn, p, exclude=held, index=index)
    if top["rows"]:
        _log_value(conn, top, {r["symbol"] for r in top["picks"]}, regime, run_id, notes)
    result = screening.screen(conn, p, named, secs, [], regime=regime, value=top["picks"], index=index)
    notes.extend(result["notes"])
    rows = result["rows"]
    if not rows:
        return {"pool": 0, "picks": [], "rows": [], "summary": "훑어볼 종목이 없었어요", "by": "code", "valued": 0}
    fresh = [r for r in rows if r["symbol"] not in held]
    ctx = (f"- 시장: {REGIME_KO[regime]} — {mv['headline']}"
           + (f" · 주목 {', '.join(mv['favor'])}" if mv.get("favor") else "") + (f" · 피할 것 {', '.join(mv['avoid'])}" if mv.get("avoid") else "")
           + "\n- 비중 확대 섹터: " + ("; ".join(f"{x['name']}({x['reason'][:60]})" for x in sv["overweight"]) or "없음")
           + ("\n- 비중 축소 섹터: " + ", ".join(x["name"] for x in sv["underweight"]) if sv["underweight"] else "")
           + (f"\n- 운용 목표:\n{goal}" if goal else ""))
    chosen = screening.llm_pick(fresh, intents, IDEAS, model, context=ctx, system=SCOUT_SYSTEM, feature="invest_desk", ask="발굴 목록", strict=True,
                                agent="scout")
    by_sym = {r["symbol"]: r for r in rows}
    picks = [{"symbol": x["symbol"], "name": by_sym[x["symbol"]]["name"], "security_type": by_sym[x["symbol"]]["security_type"],
              "sources": by_sym[x["symbol"]]["sources"], "score": by_sym[x["symbol"]]["score"], "reason": x["reason"]}
             for x in chosen["picks"]]
    picked = {x["symbol"] for x in picks}
    scorelog.record(conn, "ideas", screening.log_rows(rows, chosen["picks"], chosen.get("dropped") or {}, {s: "보유 종목 — 회의에서 따로 봐요" for s in held}),
                    screening.rules(), run_id=run_id, regime=regime, pool=result["pool"], by=chosen.get("by", "code"),
                    summary=chosen.get("summary") or "", notes=notes, meta={"headline": mv["headline"], "overweight": [x["name"] for x in sv["overweight"]]})
    return {"pool": result["pool"], "picks": picks, "summary": chosen.get("summary") or "", "by": chosen.get("by", "code"),
            "valued": sum(1 for r in rows if r.get("value")),
            "rows": [{"symbol": r["symbol"], "name": r["name"], "sources": r["sources"], "score": r["score"], "metrics": r["metrics"], "sector": r.get("sector"),
                      "value": r.get("value"), "held": r["symbol"] in held, "picked": r["symbol"] in picked} for r in rows[:20]]}


# ---- 리서치 (세 단계를 차례로) ----
def _say(conn, key: str, text: str, stage: str, data: dict, run_id: int | None) -> dict:
    """그 에이전트의 말풍선 + 카드로 채팅방에 남긴다."""
    who = desk.person(key) if key in desk.PEOPLE else _roster_person(conn, key)
    return assistant.add(conn, "agent", text, agent=who["key"], title=who["title"], name=who["name"], pokemon=who.get("pokemon"),
                         desk=stage, data=data, run_id=run_id, by=data.get("by"))


def _roster_person(conn, key: str) -> dict:
    hit = next((a for a in agents.get_roster(conn) if a["key"] == key), None)
    if hit:
        return {k: hit.get(k) for k in ("key", "title", "name", "pokemon")}
    name, _full, _country = agents.default_person(key)
    title = next((a["title"] for a in agents.DEFAULT_ROSTER if a["key"] == key), key)
    return {"key": key, "title": title, "name": name, "pokemon": agents.POKEMON.get(key)}


def research(conn, p, *, model: str | None = None, run_id: int | None = None, trigger: str = "manual", note: str = "",
             log: Callable[[str], None] | None = None, progress: Callable[[float], None] | None = None,
             check_cancel: Callable[[], None] | None = None) -> dict:
    """시장 분석 → 섹터 탐색 → 종목 발굴. 단계마다 invest_desk 에 남기고 채팅방에 그 에이전트가 말한다.
    note: 시장 급변이면 다시 보는 이유(시장 분석 프롬프트에), 그 밖(비서·화면)이면 이번 리서치에 더할 지시(운용 지시처럼)."""
    log = log or (lambda _m: None)
    step = progress or (lambda _v: None)
    check = check_cancel or (lambda: None)
    reason = note if trigger == "market_move" else ""
    texts = [i["text"] for i in autopilot.intents(conn)] + ([note] if note and not reason else [])
    intents = "\n".join(texts)
    pol = policy.get(conn)
    goal = policy.text(pol)
    notes: list[str] = []
    if p.key == "sample":
        notes.append("토스 키가 없어 샘플 시세로 분석했어요")

    mv = market_view(conn, p, intents, model, notes, reason, goal)
    desk.save(conn, "market", mv, run_id=run_id)
    log(f"시장 분석({mv['by']}): {REGIME_KO[mv['regime']]} — {mv['headline']}")
    _say(conn, "strategist", f"**{REGIME_KO[mv['regime']]}** · {mv['headline']}\n{mv['summary']}", "market", mv, run_id)
    step(0.33)
    check()

    sv = sector_view(conn, p, mv, intents, model, notes, goal, pol.get("prefer") or [], run_id)
    desk.save(conn, "sector", sv, run_id=run_id)
    log(f"섹터 탐색({sv['by']}): {sv['count']}개 중 확대 " + ", ".join(x["name"] for x in sv["overweight"])
        + (" · 축소 " + ", ".join(x["name"] for x in sv["underweight"]) if sv["underweight"] else ""))
    _say(conn, "sector", f"섹터 {sv['count']}개를 훑었어요 — 비중 확대 **{', '.join(x['name'] for x in sv['overweight']) or '없음'}**"
         + (f", 축소 {', '.join(x['name'] for x in sv['underweight'])}" if sv["underweight"] else "")
         + (f"\n{sv['summary']}" if sv["summary"] else ""), "sector", sv, run_id)
    step(0.66)
    check()

    fund = valuation.refresh(conn, set(valuation.sector_index(p, notes)) or None, log=log, check=check)
    if not fund["ok"]:
        notes.append(f"가치평가 재무를 받지 못했어요: {fund['reason']}")
    iv = scout(conn, p, mv, sv, intents, model, notes, goal, run_id)
    desk.save(conn, "ideas", iv, run_id=run_id)
    log(f"종목 발굴({iv['by']}): {iv['pool']}종목 중 " + ", ".join(x["name"] for x in iv["picks"]))
    pm = _roster_person(conn, "portfolio")
    _say(conn, "scout", f"{iv['pool']}종목을 훑어 발굴 목록 {len(iv['picks'])}개를 만들었어요 — 다음 회의에서 {pm['name']}({pm['title']})가 "
         "보유와 함께 안건을 정해요" + ("" if iv.get("valued") or dart.configured() else "\n가치 점수는 OpenDART 인증키가 있어야 매길 수 있어요 (설정 > 투자)"),
         "ideas", iv, run_id)
    step(1.0)
    for n in notes:
        log(n)
    return {"market": mv, "sector": sv, "ideas": iv, "notes": notes}


# ---- 4) 리스크 감시 ----
def day_change(p, symbol: str) -> float | None:
    try:
        cs = p.candles(symbol, "1d", 2)
    except (MarketError, TossError):
        return None
    if len(cs) < 2 or not cs[-1].get("c") or not cs[-2].get("c"):
        return None
    return cs[-1]["c"] / cs[-2]["c"] - 1


def watch(conn, p, now: datetime) -> list[dict]:
    """보유·발굴 종목 중 급변한 것 → [{symbol, name, kind, change, held}] (코드, LLM 없음)."""
    try:
        holdings = trading.account(conn, market=p).summary()["holdings"]
    except (TradeError, MarketError, TossError):
        holdings = []
    held = {h["symbol"]: h.get("name") or h["symbol"] for h in holdings if h["symbol"].isdigit() and len(h["symbol"]) == 6}
    ideas = {i["symbol"]: i["name"] for i in desk.ideas_for_pool(conn, now.date()) if i["symbol"] not in held}
    syms = (list(held) + list(ideas))[:WATCH_MAX]
    with ThreadPoolExecutor(WORKERS) as ex:
        changes = list(ex.map(lambda s: day_change(p, s), syms))
    out = []
    for sym, ch in zip(syms, changes):
        if ch is None:
            continue
        kind = (("drop" if ch <= -HOLD_DROP else "jump" if ch >= HOLD_JUMP else None) if sym in held
                else "breakout" if ch >= IDEA_BREAKOUT else None)
        if kind:
            out.append({"symbol": sym, "name": held.get(sym) or ideas.get(sym) or sym, "kind": kind, "change": round(ch, 4),
                        "held": sym in held})
    return out


def _alert_line(a: dict) -> str:
    return f"{a['name']}({a['symbol']}) 오늘 {_pct(a['change'])} · {ALERT_KO[a['kind']]}"


def watch_and_act(conn, p, now: datetime) -> dict:
    """감시 → 새 경보를 남기고 채팅방에 알린 뒤, 자동 운용이면 그 종목으로 수시 회의를 연다(토론 중이면 다음 감시 때)."""
    st = desk.state(conn, now.date())
    new = [a for a in watch(conn, p, now) if f"{a['symbol']}:{a['kind']}" not in st["alerted"]]
    out: dict = {}
    if new:
        for a in new:
            desk.save(conn, "alert", {**a, "text": _alert_line(a)}, now=now)
        st["alerted"] += [f"{a['symbol']}:{a['kind']}" for a in new]
        why = {a["symbol"]: _alert_line(a) for a in new}
        st["pending"] = {"why": {**((st.get("pending") or {}).get("why") or {}), **why}, "at": now.strftime("%H:%M:%S")}
        cfg = autopilot.get_config(conn)
        _say(conn, "risk", "장 중 점검에서 급변을 찾았어요\n" + "\n".join(f"- {line}" for line in why.values())
             + ("" if not cfg["enabled"] else "\n상시 운용 채널에서 바로 볼게요." if cfg["mode"] == "continuous"
                else "\n그 종목으로 수시 회의를 요청할게요."),
             "alert", {"alerts": new, "by": "code"}, None)
        out["alerts"] = new
        if cfg["mode"] == "continuous":
            st["pending"] = None  # 상시 운용 채널이 경보(invest_desk)를 읽고 바로 이야기한다
    pending = st.get("pending")
    if pending and _minutes(now.strftime("%H:%M")) - _minutes(pending["at"]) <= ADHOC_WAIT_MIN:
        if not autopilot.active_session(conn):
            try:
                run_id = autopilot.run_adhoc(conn, pending["why"], now=now, provider=p)
                names = ", ".join(line.split("(")[0] for line in pending["why"].values())
                assistant.add(conn, "event", f"리스크 경보로 수시 회의를 열었어요 — {names}", kind="session_started", run_id=run_id,
                              autopilot=True)
                out["adhoc"] = run_id
            except autopilot.AutopilotError as e:
                assistant.add(conn, "event", f"수시 회의를 열지 못했어요: {e}", kind="risk_skipped")
            st["pending"] = None
    elif pending:
        st["pending"] = None  # 너무 오래 기다렸다 — 다음 정기 회의가 경보를 안건으로 본다
    desk.put_state(conn, st)
    return out


# ---- 언제 무엇을 (서비스가 20초마다) ----
def moved_since(conn, p, now: datetime) -> str | None:
    """지수가 오늘 마지막 시장 분석 때보다 REFRESH_MOVE 넘게 움직였으면 그 설명."""
    mv = desk.latest(conn, "market", now.date())
    if not mv:
        return None
    base = {i.get("symbol"): i.get("price") for i in mv["data"].get("indicators") or []}
    try:
        cur = p.indicators()
    except (MarketError, TossError):
        return None
    for i in cur:
        b = base.get(i["symbol"])
        if i["symbol"] in ("KOSPI", "KOSDAQ") and b and i.get("price"):
            d = i["price"] / b - 1
            if abs(d) >= desk.REFRESH_MOVE:
                return f"{i['name']}가 {mv['created_at'][11:16]} 분석 때보다 {_pct(d)} 움직였어요"
    return None


def tick(conn, now: datetime | None = None, provider=None) -> dict | None:
    """자동 운용이 켜져 있고 장이 열리는 날 — 아침 리서치, 장 중 리스크 감시, 시장 급변 때 리서치 다시. 할 일이 없으면 None."""
    if not autopilot.get_config(conn)["enabled"]:
        return None
    now = now or market.now_kst()
    p = provider or market.provider()
    try:
        sess = p.session()
    except (MarketError, TossError):
        return None
    if not sess.get("start") or not sess.get("end"):
        return None  # 휴장일
    start, end = _minutes(sess["start"]), _minutes(sess["end"])
    minute = now.hour * 60 + now.minute
    last_trade = end - autopilot.CLOSE_BEFORE_MIN
    st = desk.state(conn, now.date())
    running = desk.research_running(conn)
    tries = st["research"]
    if (not running and start - desk.PREP_MIN <= minute <= last_trade and not desk.latest(conn, "ideas", now.date())
            and len(tries) < desk.MORNING_TRIES and (not tries or minute - _minutes(tries[-1]["at"]) >= desk.RETRY_MIN)):
        return {"research": desk.start_research(conn, "morning", now=now)}  # 오늘 첫 리서치, 또는 결과 없이 끝난 리서치(서버 재시작 등) 다시
    if not tries:
        return None
    if not start + autopilot.OPEN_AFTER_MIN <= minute <= last_trade:
        return None
    if st["last_watch"] and minute - _minutes(st["last_watch"]) < desk.WATCH_MIN:
        return None
    st["last_watch"] = now.strftime("%H:%M:%S")
    desk.put_state(conn, st)
    out: dict = {}
    refreshes = [r for r in st["research"] if r.get("trigger") == "market_move"]
    gap_ok = minute - _minutes(st["research"][-1]["at"]) >= desk.REFRESH_GAP_MIN
    if not running and len(refreshes) < desk.MAX_REFRESH and gap_ok:
        why = moved_since(conn, p, now)
        if why:
            out["research"] = desk.start_research(conn, "market_move", note=why, now=now)
    out.update(watch_and_act(conn, p, now))
    return out or None
