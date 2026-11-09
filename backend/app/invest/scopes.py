"""토론 범위 → 후보 종목. 에이전트는 후보마다 입장을 내고, 게이트는 종목마다 따진다.

- symbol (단일 종목): 고른 종목 하나 (주식·ETF).
- sector (섹터): 토스 섹터의 구성 주식(시가총액순) + 섹터 관련 ETF. 펀드는 Open API 에 없어 ETF(상장지수펀드)로 포함.
- portfolio (전체): 내 보유 종목(토스 국내) + 관심 종목(모든 그룹), 보유 먼저.
- autopilot (자동 운용) · scan (시장 스캔): 여러 종목을 훑고 회의 후보를 고른다(screening.py) — 운용 지시(사용자가 채팅으로 남긴 글)에
  나온 종목·섹터(코드가 글에서 찾음: intent_symbols·intent_sectors), 보유, 오늘 운용 데스크 발굴 목록(desk), 관심, 시장 랭킹을 모아
  점수를 매기고, pick(회의 작업은 포트폴리오 매니저 LLM 이 리서치 요약과 함께, 미리 보기는 점수 순)이 후보 수만큼 고른다.
  params.focus(리스크 경보 종목)는 언제나 후보 앞에. 보유 종목(국내)은 매도 타이밍을 보려고 후보 수와 따로 언제나 뒤에 붙인다
  (exits.MAX_HELD 개까지, 매도 점검 신호·비중 순, 이유 '보유 — 매도 점검'). 결과 표는 out["screening"].
후보 수는 실험실 설정 max_candidates 로 자른다. 해외 종목은 아직 주문을 못 하므로 후보에서 뺀다.
"""

import re
import sqlite3
from collections.abc import Callable

from app.invest import desk, exits, policy, scorelog, screening
from app.invest.marketdata import market, watch
from app.invest.broker import trading
from app.invest.marketdata.market import MarketError
from app.invest.broker.toss import TossError
from app.invest.broker.trading import TradeError

SCOPES = {"symbol": "단일 종목", "sector": "섹터", "portfolio": "전체 (보유 + 관심)", "scan": "시장 스캔 (여러 종목 → 후보 선정)",
          "autopilot": "자동 운용 (시장 스캔 + 운용 지시)"}
# 그룹 이름을 뗀 이름으로도 찾는다 — 'SK하이닉스'를 '하이닉스'로 써도 (뗀 이름이 3글자 이상일 때만)
GROUP_PREFIXES = ("sk", "lg", "cj", "gs", "hd", "kb", "ls", "삼성", "현대", "롯데", "한화", "포스코", "두산", "신한", "하나", "카카오")
SECTOR_GENERIC = {"장비", "소재", "서비스", "산업", "제품", "기타", "관련", "부품", "기업", "기술"}
INTENT_MAX_SYMBOLS = 4  # 운용 지시에서 찾은 종목은 이만큼까지
INTENT_MAX_SECTORS = 2  # 섹터는 이만큼까지 (섹터마다 시가총액 상위 screening.SECTOR_TOP 종목을 훑는다)


class ScopeError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _kr(symbol: str) -> bool:
    return symbol.isdigit() and len(symbol) == 6  # 국내 종목 코드 (해외는 아직 주문 불가)


def _flat(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


def intent_symbols(conn: sqlite3.Connection, p, text: str, limit: int = INTENT_MAX_SYMBOLS) -> list[dict]:
    """운용 지시 글에 나온 국내 종목 (시세 제공자의 종목 목록에서). 6자리 코드, 이름(2글자 이름은 단어로),
    그룹 이름을 뗀 이름. ETF 는 이름 전체가 있을 때만. 다른 찾은 이름에 들어 있는 짧은 이름은 버린다(LG에너지솔루션 → LG)."""
    flat = _flat(text)
    if not flat:
        return []
    words = set(re.findall(r"[0-9a-z가-힣&]+", text.lower()))
    try:
        rows = p.universe(conn)
    except (MarketError, TossError):
        return []
    hits = []
    for r in rows:
        sym, name = r["symbol"], _flat(r["name"])
        if not _kr(sym) or not name:
            continue
        full = name in flat if len(name) >= 3 else name in words
        core = next((name[len(g):] for g in GROUP_PREFIXES if name.startswith(g)), "")
        if sym in words or full or (r["security_type"] != "ETF" and len(core) >= 3 and core in flat):
            at = flat.find(name) if full else flat.find(core) if core and core in flat else flat.find(sym)
            hits.append({"symbol": sym, "name": r["name"], "security_type": r["security_type"] or "", "at": at, "key": name})
    hits = [h for h in hits if not any(h is not o and h["key"] in o["key"] for o in hits)]
    hits.sort(key=lambda h: (h["at"], -len(h["key"])))
    return [{k: h[k] for k in ("symbol", "name", "security_type")} for h in hits[:limit]]


def intent_sectors(p, text: str, limit: int = INTENT_MAX_SECTORS) -> list[dict]:
    """운용 지시 글에 나온 섹터 (토스 섹터 대·중분류). 섹터 이름이나 그 이름의 낱말(2글자 이상, 흔한 말 제외)이 글에 있으면."""
    flat = _flat(text)
    if not flat:
        return []
    try:
        secs = sectors(p)
    except ScopeError:
        return []
    out = []
    for sec in secs:
        name = _flat(sec["name"])
        tokens = [t for t in re.split(r"[^0-9a-z가-힣]+|와|및", name) if len(t) >= 2 and t not in SECTOR_GENERIC]
        if name in flat or any(t in flat for t in tokens):
            out.append(sec)
    return out[:limit]


def sectors(p) -> list[dict]:
    """섹터 고르기용 — 대분류·중분류만 (소분류는 너무 잘다)."""
    try:
        return [s for s in p.sectors() if (s.get("depth") or 0) <= 1]
    except (MarketError, TossError) as e:
        raise ScopeError(str(e), 502) from None


Pick = Callable[[list[dict], str, int, str], dict]


def resolve(conn: sqlite3.Connection, p, params: dict, max_candidates: int, pick: Pick | None = None) -> dict:
    """→ {scope, label, candidates: [{symbol, name, security_type, held}], sector?, screening?, notes: [..]}
    pick: scan·autopilot 에서 훑은 표(점수 순)·운용 지시 글·후보 수·리서치 요약 → {picks, summary, by}. 없으면 점수 순(미리 보기)."""
    scope = params.get("scope") or "symbol"
    if scope not in SCOPES:
        raise ScopeError(f"알 수 없는 토론 범위입니다: {scope}")
    notes: list[str] = []
    holdings: list[dict] = []
    read = False  # 계좌를 읽었을 때만 후보에 held(보유 수량, 없으면 0)를 단다 — 못 읽었으면 보유를 모르는 것(게이트가 매도 쪽을 닫지 않게)
    if scope in ("portfolio", "sector", "autopilot", "scan", "symbol"):  # 단일 종목도 — 가진 종목이면 매도 점검이 붙게
        try:
            holdings = trading.account(conn, market=p).summary()["holdings"]
            read = True
        except (TradeError, MarketError, TossError) as e:
            if scope in ("portfolio", "autopilot", "scan"):
                notes.append(f"보유 종목을 불러오지 못했습니다: {e}")
    held = {h["symbol"]: h["quantity"] for h in holdings}

    out: dict = {"scope": scope}
    cands: list[dict] = []
    if scope == "symbol":
        symbol = str(params.get("symbol") or "").strip()
        if not symbol:
            raise ScopeError("종목을 골라 주세요")
        try:
            info = p.info(symbol)
        except (MarketError, TossError) as e:
            raise ScopeError(str(e), getattr(e, "status", 404) or 404) from None
        cands = [{"symbol": symbol, "name": info.get("name") or symbol, "security_type": info.get("security_type") or ""}]
        out["label"] = cands[0]["name"]
    elif scope == "sector":
        sector_id = str(params.get("sector_id") or "").strip()
        if not sector_id:
            raise ScopeError("섹터를 골라 주세요")
        try:
            sec = p.sector(sector_id)
        except (MarketError, TossError) as e:
            raise ScopeError(str(e), getattr(e, "status", 404) or 404) from None
        etf_slots = 1 if max_candidates <= 4 else 2
        etfs = [{"symbol": e["symbol"], "name": e["name"], "security_type": "ETF"} for e in sec["etfs"] if _kr(e["symbol"])]
        stocks = [{"symbol": s["symbol"], "name": s["name"], "security_type": "STOCK"} for s in sec["stocks"] if _kr(s["symbol"])]
        take_etf = etfs[:min(etf_slots, len(etfs))]
        cands = stocks[:max_candidates - len(take_etf)] + take_etf
        out["sector"] = {"id": sector_id, "name": sec["name"], "summary": sec.get("summary"),
                         "change_rates": sec.get("change_rates") or {}, "stock_count": len(stocks), "etf_count": len(etfs)}
        out["label"] = f"섹터 {sec['name']}"
        if len(stocks) + len(etfs) > len(cands):
            notes.append(f"구성 {len(stocks)}종목·ETF {len(etfs)}개 중 시가총액 상위 {len(cands)}개만 토론합니다")
    elif scope in ("autopilot", "scan"):
        cands, out["screening"] = _scan(conn, p, params, holdings, max_candidates, notes, pick)
        out["label"] = "시장 스캔" if scope == "scan" else "보유 + 운용 지시"
    else:
        seen: set[str] = set()
        for h in holdings:
            if _kr(h["symbol"]) and h["symbol"] not in seen:
                seen.add(h["symbol"])
                cands.append({"symbol": h["symbol"], "name": h.get("name") or h["symbol"], "security_type": ""})
        for w in watch.symbols(conn):
            if _kr(w["symbol"]) and w["symbol"] not in seen:
                seen.add(w["symbol"])
                cands.append({"symbol": w["symbol"], "name": w["name"] or w["symbol"], "security_type": w["security_type"]})
        if not cands:
            raise ScopeError("보유 종목과 관심 종목이 없어요 — 관심 탭에서 종목을 담거나 단일 종목으로 토론하세요")
        if len(cands) > max_candidates:
            notes.append(f"보유·관심 {len(cands)}종목 중 앞의 {max_candidates}개만 토론합니다 (보유 먼저, 실험실에서 후보 수 변경)")
            cands = cands[:max_candidates]
        out["label"] = "전체 (보유 + 관심)"
    if not cands:
        raise ScopeError("토론할 국내 종목이 없습니다")
    for c in cands:
        if read:
            c["held"] = held.get(c["symbol"], 0.0)
    out["candidates"] = cands
    out["notes"] = notes
    return out


def _scan(conn: sqlite3.Connection, p, params: dict, holdings: list[dict], max_candidates: int, notes: list[str],
          pick: Pick | None) -> tuple[list[dict], dict]:
    """여러 종목을 훑어(screening) 회의 후보를 고른다 — 리스크 경보 종목, 운용 지시의 종목·섹터, 보유, 발굴 목록, 관심, 시장 랭킹."""
    text = "\n".join(str(t) for t in params.get("intents") or [])
    named = intent_symbols(conn, p, text)
    secs = intent_sectors(p, text)
    day = market.now_kst().date()
    ideas = desk.ideas_for_pool(conn, day)
    focus = [s_ for s_ in (str(x).strip() for x in params.get("focus") or []) if _kr(s_)]
    try:
        names = p.names(conn, focus) if focus else {}
    except (MarketError, TossError):
        names = {}
    regime = desk.regime(conn, day)
    result = screening.screen(conn, p, named, secs, holdings, ideas, [{"symbol": f, "name": names.get(f, f)} for f in focus], regime=regime)
    notes.extend(result["notes"])
    goal = policy.text(policy.get(conn))
    context = "\n".join(x for x in (f"운용 목표:\n{goal}" if goal else "",
                                    f"이번 회의 안건: {params['agenda']}" if params.get("agenda") else "", desk.brief(conn, day)) if x)
    chosen = (pick or (lambda rows, _t, n, _c: screening.code_pick(rows, n)))(result["rows"], text, max_candidates, context)
    by_sym = {r["symbol"]: r for r in result["rows"]}
    focus_why = params.get("focus_why") or {}
    forced = [{"symbol": f, "reason": f"리스크 경보 — {focus_why.get(f) or '이번 회의에서 꼭 봐요'}"} for f in focus if f in by_sym]
    picks = (forced + [x for x in chosen["picks"] if x["symbol"] not in focus])[:max(max_candidates, len(forced))]
    picks += _held_picks(conn, p, holdings, result["rows"], picks, notes)
    chosen = {**chosen, "picks": picks}
    cands = [{"symbol": x["symbol"], "name": by_sym[x["symbol"]]["name"], "security_type": by_sym[x["symbol"]]["security_type"],
              "why": x["reason"]} for x in picks]
    if named:
        notes.append("운용 지시에서 찾은 종목: " + ", ".join(f"{c['name']}({c['symbol']})" for c in named))
    if secs:
        notes.append("운용 지시에서 찾은 섹터: " + ", ".join(s_["name"] for s_ in secs))
    if ideas:
        notes.append(f"오늘 발굴 목록 {len(ideas)}종목을 함께 훑었어요")
    if not cands:
        raise ScopeError("훑어볼 종목이 없어요 — 보유·관심 종목이 없고 시장 랭킹도 받지 못했어요")
    picked = {c["symbol"] for c in cands}
    if pick is not None:  # 미리 보기는 판단이 아니라 남기지 않는다
        scorelog.record(conn, "meeting", screening.log_rows(result["rows"], picks), screening.rules(), run_id=params.get("run_id"), regime=regime,
                        pool=result["pool"], by=chosen.get("by", "code"), summary=chosen.get("summary") or "", notes=notes,
                        meta={"scope": params.get("scope"), "agenda": params.get("agenda") or ""})
    rows = [{"symbol": r["symbol"], "name": r["name"], "sources": r["sources"], "score": r["score"], "metrics": r["metrics"], "sector": r.get("sector"),
             "value": r.get("value"), "picked": r["symbol"] in picked} for r in result["rows"]]
    return cands, {"pool": result["pool"], "rows": rows, "picks": chosen["picks"], "summary": chosen.get("summary") or "",
                   "by": chosen.get("by", "code")}


def _held_picks(conn: sqlite3.Connection, p, holdings: list[dict], rows: list[dict], picks: list[dict], notes: list[str]) -> list[dict]:
    """고르지 않은 보유 종목 — 매도 타이밍을 보려고 후보 수와 따로 붙인다. 신호가 많고 비중이 큰 순으로 exits.MAX_HELD 개까지(고른 보유 포함)."""
    found = exits.checks(conn, p, holdings, [r["symbol"] for r in rows], metrics={r["symbol"]: r.get("metrics") for r in rows})
    picked = {x["symbol"] for x in picks}
    room = max(0, exits.MAX_HELD - sum(1 for s_ in found if s_ in picked))
    extra = [s_ for s_ in exits.urgent(found) if s_ not in picked]
    if len(extra) > room:
        notes.append(f"보유 {len(found)}종목 중 {len(found) - len(extra) + room}개만 매도 점검으로 회의에 넣었어요 (신호·비중 순)")
    return [{"symbol": s_, "reason": exits.why(found[s_])} for s_ in extra[:room]]
