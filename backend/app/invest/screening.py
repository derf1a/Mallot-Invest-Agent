"""회의 전 후보 선정 — 여러 종목을 넓게 훑고(코드) 회의할 종목을 고른다(포트폴리오 매니저 관점 LLM, 실패하면 점수 순).

1) 모으기(pool): 리스크 경보 종목 · 운용 지시에 나온 종목 · 토스 보유(국내) · 운용 데스크 발굴 목록 · 관심 종목 · 섹터(운용 지시에 나온
   섹터, 발굴 때는 비중 확대 섹터)의 시가총액 상위 SECTOR_TOP + 나머지 중 거래대금 상위 SECTOR_MID(중소형) · 가치 스크린 상위 · 시장 랭킹
   (거래대금·상승·거래량 상위). 국내(6자리)만, 같은 종목은 출처를 합친다. MAX_POOL 까지(앞 출처 먼저).
2) 점수(factors): 일봉 60일 지표 + 가치평가(valuation.py)를 그룹별로 더한다. 숫자를 못 받은 종목은 점수 없이 뒤로 — 표에는 남는다.
   - 추세: 이동평균 정렬 ±1 + 20일 모멘텀(±1.5) + 5일 모멘텀(±0.9) + 거래량 급증(최대 +1)
   - 눌림목: 중기 상승 중(20일선 > 60일선)에 60일 고점 대비 PULLBACK 만큼 쉬고 RSI 가 식은 종목 +PULLBACK_POINTS
   - 가치: (가치 점수 − 50) ÷ 50 × VALUE_POINTS (±1.5)
   - 위험: 과열(RSI 75 이상 −0.7), 추격(5일 +CHASE_5D 넘게 오르면 넘은 만큼 × CHASE_RATE)
   - 출처: 운용 지시·경보 +1 · 발굴 +0.8 · 보유 +0.6 · 가치 상위 +0.4 · 관심 +0.3 · 섹터·랭킹 +0.1
   시장 국면(regime)이 추세·눌림목·가치 그룹의 가중치(WEIGHTS)를 바꾼다 — 위험 회피면 추세를 줄이고 가치를 키운다. 위험·출처는 그대로.
3) 고르기(pick): LLM 이 점수표·운용 지시(·리서치 요약)를 보고 정한 수만큼 이유와 함께 고른다. 회의 후보는 포트폴리오 매니저 관점
   (PICK_SYSTEM — 보유 종목은 매도 점검으로 회의에 따로 들어가니 자리는 새 종목에, scopes._scan·exits.py), 운용 데스크의 발굴 목록은 종목 발굴 애널리스트 관점(desk_agents.SCOUT_SYSTEM).
   표에 없는 종목은 버리고, 모자라면 점수 순으로 채운다. LLM 이 실패하면 code_pick — 운용 지시·보유 종목 하나씩 먼저, 나머지는 점수 순.
   strict(발굴 목록): LLM 이든 코드든 한 섹터 MAX_PER_SECTOR 개까지(코드가 강제), 코드로 고를 때는 급등 직후·과열 종목을 건너뛴다.
"""

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor

from app.invest import valuation
from app.invest.marketdata import indicators, watch
from app.invest.marketdata.market import MarketError
from app.invest.broker.toss import TossError
from app.llm.client import LLMError, structured

SECTOR_TOP = 5
SECTOR_MID = 3    # 섹터의 시가총액 상위 밖에서 거래대금 상위 — 중소형주가 후보에 들어오는 길
RANKINGS = (("amount", 12), ("gainers", 8), ("volume", 8))  # 시장 랭킹 종류와 개수
MAX_POOL = 80
BARS = 60
WORKERS = 4
SOURCE_BONUS = {"리스크 경보": 1.0, "운용 지시": 1.0, "발굴": 0.8, "보유": 0.6, "가치 상위": 0.4, "관심": 0.3}
OTHER_BONUS = 0.1  # 섹터·랭킹
RANKING_KO = {"amount": "거래대금 상위", "gainers": "상승 상위", "volume": "거래량 상위"}
TABLE_ROWS = 40  # LLM 에게 보여 줄 표 줄 수
TREND_POINTS = 1.0
M20_CLIP, M20_RATE = 0.3, 5
M5_CLIP, M5_RATE = 0.15, 6
VOLUME_FROM, VOLUME_CAP, VOLUME_RATE = 1.5, 2, 0.5
OVERHEAT_RSI, OVERHEAT_POINTS = 75, 0.7
CHASE_5D = 0.20    # 5일 +20% 를 넘으면 넘은 만큼 감점
CHASE_CAP = 0.60   # 감점은 여기까지 (−2)
CHASE_RATE = 5
SKIP_5D = 0.30                                    # 코드가 고를 때 건너뛰는 5일 등락 (LLM 은 이유를 들어 고를 수 있다)
PULLBACK = {"dd_from": -0.20, "dd_to": -0.05, "rsi_from": 35, "rsi_to": 55}
PULLBACK_POINTS = 1.0
VALUE_POINTS = 1.5
MAX_PER_SECTOR = 3
# 시장 국면별 그룹 가중치 — 위험 회피면 추세를 믿지 않고 가치·눌림목을, 위험 선호면 추세를 본다. 위험·출처 그룹은 언제나 1.
WEIGHTS = {"risk_on": {"trend": 1.2, "pullback": 0.5, "value": 0.6},
           "neutral": {"trend": 1.0, "pullback": 0.8, "value": 1.0},
           "risk_off": {"trend": 0.5, "pullback": 0.6, "value": 1.4}}
GROUPS = {"trend": "추세", "pullback": "눌림목", "value": "가치", "risk": "위험", "source": "출처"}


def _kr(symbol: str) -> bool:
    return symbol.isdigit() and len(symbol) == 6


def pool(conn, p, named: list[dict], sectors: list[dict], holdings: list[dict], notes: list[str],
         ideas: list[dict] = (), focus: list[dict] = (), value: list[dict] = ()) -> list[dict]:
    """훑어볼 종목 — [{symbol, name, security_type, sources: [...]}] (경보·지시·보유·발굴·관심·섹터·가치 상위·랭킹 순)."""
    out: dict[str, dict] = {}

    def add(sym: str, name: str, stype: str, source: str) -> None:
        if not _kr(sym):
            return
        row = out.setdefault(sym, {"symbol": sym, "name": name or sym, "security_type": stype or "", "sources": []})
        if source not in row["sources"]:
            row["sources"].append(source)

    for c in focus:
        add(c["symbol"], c.get("name", ""), c.get("security_type", ""), "리스크 경보")
    for c in named:
        add(c["symbol"], c["name"], c.get("security_type", ""), "운용 지시")
    for h in holdings:
        add(h["symbol"], h.get("name", ""), "", "보유")
        if h["symbol"] in out and h.get("pnl_rate") is not None:  # 평단 대비 평가 손익 — 표의 주가 등락과 따로
            out[h["symbol"]]["holding_pnl"] = h["pnl_rate"]
    for c in ideas:
        add(c["symbol"], c.get("name", ""), c.get("security_type", ""), "발굴")
    for w in watch.symbols(conn):
        add(w["symbol"], w["name"], w.get("security_type", ""), "관심")
    for sec in sectors:
        try:
            detail = p.sector(sec["id"])
        except (MarketError, TossError) as e:
            notes.append(f"섹터 {sec['name']} 구성 종목을 불러오지 못했습니다: {e}")
            continue
        stocks = [x for x in detail["stocks"] if _kr(x["symbol"])]
        rest = sorted((x for x in stocks[SECTOR_TOP:] if x.get("trading_amount")), key=lambda x: -x["trading_amount"])
        for s in stocks[:SECTOR_TOP] + rest[:SECTOR_MID]:
            add(s["symbol"], s["name"], "STOCK", f"섹터 {sec['name']}")
    for c in value:
        add(c["symbol"], c.get("name", ""), c.get("security_type", ""), "가치 상위")
    for kind, count in RANKINGS:
        try:
            for r in p.rankings(kind, count, conn=conn):
                add(r["symbol"], r["name"], "STOCK", RANKING_KO[kind])
        except (MarketError, TossError) as e:
            notes.append(f"시장 랭킹({RANKING_KO[kind]})을 불러오지 못했습니다: {e}")
    rows = list(out.values())
    if len(rows) > MAX_POOL:
        notes.append(f"훑어볼 종목 {len(rows)}개 중 {MAX_POOL}개만 봅니다 (경보·지시·보유·발굴·관심 먼저)")
        rows = rows[:MAX_POOL]
    return rows


def metrics(p, symbol: str) -> dict | None:
    try:
        cs = p.candles(symbol, "1d", BARS)
    except (MarketError, TossError):
        return None
    closes = [c["c"] for c in cs if c.get("c")]
    if len(closes) < 21:
        return None
    s = indicators.summary(closes, [c["v"] for c in cs])
    ma5, ma20, ma60 = s.get("ma5"), s.get("ma20"), s.get("ma60")
    trend = 0
    if ma5 and ma20 and ma60:
        trend = 1 if ma5 > ma20 > ma60 else -1 if ma5 < ma20 < ma60 else 0
    return {"price": closes[-1], "change_1d": round(closes[-1] / closes[-2] - 1, 4), "change_5d": s.get("return_5d"),
            "change_20d": s.get("return_20d"), "volume_ratio": s.get("volume_vs_20d_avg"), "rsi": s.get("rsi14"), "trend": trend,
            "drawdown": round(closes[-1] / max(closes) - 1, 4), "uptrend": bool(ma20 and ma60 and ma20 > ma60), "ma20": ma20}


def _clip(value: float | None, limit: float) -> float:
    return max(-limit, min(limit, value or 0.0))


def factors(m: dict | None, sources: list[str], value: dict | None = None, regime: str = "neutral") -> dict | None:
    """점수를 항목별로 → {score, regime, weights, groups, items: [{key, group, label, input, points}]}. 시세가 없으면 None.
    항목의 points 는 가중치 전 값, 그룹 합에 WEIGHTS 를 곱해 더한다."""
    if m is None:
        return None
    w = WEIGHTS.get(regime) or WEIGHTS["neutral"]
    items = [{"key": "trend", "group": "trend", "label": "이동평균 정렬", "input": m["trend"], "points": m["trend"] * TREND_POINTS},
             {"key": "m20", "group": "trend", "label": "20일 모멘텀", "input": m["change_20d"], "points": _clip(m["change_20d"], M20_CLIP) * M20_RATE},
             {"key": "m5", "group": "trend", "label": "5일 모멘텀", "input": m["change_5d"], "points": _clip(m["change_5d"], M5_CLIP) * M5_RATE}]
    vr = m["volume_ratio"] or 0
    if vr > VOLUME_FROM:
        items.append({"key": "volume", "group": "trend", "label": "거래량 급증", "input": vr, "points": min(vr - 1, VOLUME_CAP) * VOLUME_RATE})
    dd, rsi = m.get("drawdown"), m["rsi"] or 0
    if (m.get("uptrend") and dd is not None and PULLBACK["dd_from"] <= dd <= PULLBACK["dd_to"]
            and PULLBACK["rsi_from"] <= rsi <= PULLBACK["rsi_to"]):
        items.append({"key": "pullback", "group": "pullback", "label": "상승 중 눌림목", "input": dd, "points": PULLBACK_POINTS})
    if value and value.get("score") is not None:
        items.append({"key": "value", "group": "value", "label": f"가치 {value['grade']}", "input": value["score"],
                      "points": (value["score"] - 50) / 50 * VALUE_POINTS})
    if rsi >= OVERHEAT_RSI:
        items.append({"key": "overheat", "group": "risk", "label": "과열 (RSI)", "input": rsi, "points": -OVERHEAT_POINTS})
    m5 = m["change_5d"] or 0
    if m5 > CHASE_5D:
        items.append({"key": "chase", "group": "risk", "label": "급등 추격", "input": m5, "points": -(min(m5, CHASE_CAP) - CHASE_5D) * CHASE_RATE})
    items += [{"key": f"source:{s}", "group": "source", "label": f"출처 {s}", "input": None, "points": SOURCE_BONUS.get(s, OTHER_BONUS)} for s in sources]
    groups = {}
    for key, label in GROUPS.items():
        raw = sum(x["points"] for x in items if x["group"] == key)
        weight = w.get(key, 1.0)
        groups[key] = {"label": label, "raw": round(raw, 3), "weight": weight, "points": round(raw * weight, 3)}
    for x in items:
        x["points"] = round(x["points"], 3)
    return {"score": round(sum(g["points"] for g in groups.values()), 2), "regime": regime if regime in WEIGHTS else "neutral", "groups": groups, "items": items}


def score(m: dict | None, sources: list[str], value: dict | None = None, regime: str = "neutral") -> float | None:
    f = factors(m, sources, value, regime)
    return f["score"] if f else None


def rules() -> dict:
    """점수·풀·한도 기준 전부 — 판단 기록에 그대로 남긴다 (바뀌면 해시가 바뀐다)."""
    return {"pool": {"sector_top": SECTOR_TOP, "sector_mid": SECTOR_MID, "rankings": RANKINGS, "max_pool": MAX_POOL, "bars": BARS, "table_rows": TABLE_ROWS},
            "score": {"trend_points": TREND_POINTS, "m20": [M20_CLIP, M20_RATE], "m5": [M5_CLIP, M5_RATE], "volume": [VOLUME_FROM, VOLUME_CAP, VOLUME_RATE],
                      "overheat": [OVERHEAT_RSI, OVERHEAT_POINTS], "chase": [CHASE_5D, CHASE_CAP, CHASE_RATE], "pullback": PULLBACK,
                      "pullback_points": PULLBACK_POINTS, "value_points": VALUE_POINTS, "source_bonus": SOURCE_BONUS, "other_bonus": OTHER_BONUS},
            "weights": WEIGHTS, "limits": {"max_per_sector": MAX_PER_SECTOR, "skip_5d": SKIP_5D}, "valuation": valuation.rules()}


def screen(conn, p, named: list[dict], sectors: list[dict], holdings: list[dict], ideas: list[dict] = (),
           focus: list[dict] = (), *, regime: str = "neutral", value: list[dict] = (), index: dict[str, dict] | None = None) -> dict:
    """모으고 점수를 매긴다 → {pool, rows(점수 순), notes}. rows 에 sector·value(요약)·valuation(근거)·factors(항목별 점수)."""
    notes: list[str] = []
    rows = pool(conn, p, named, sectors, holdings, notes, ideas, focus, value)
    with ThreadPoolExecutor(WORKERS) as ex:
        ms = list(ex.map(lambda r: metrics(p, r["symbol"]), rows))
    index = valuation.sector_index(p, notes) if index is None else index
    vals = valuation.evaluate(conn, p, [r["symbol"] for r in rows], index=index, prices={r["symbol"]: (m or {}).get("price") for r, m in zip(rows, ms)})
    for r, m in zip(rows, ms):
        v = vals.get(r["symbol"])
        r["metrics"] = m
        r["sector"] = (index.get(r["symbol"]) or {}).get("sector")
        r["valuation"] = v
        r["value"] = valuation.brief(v)
        r["factors"] = factors(m, r["sources"], v, regime)
        r["score"] = r["factors"]["score"] if r["factors"] else None
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    return {"pool": len(rows), "rows": rows, "notes": notes}


def log_rows(rows: list[dict], picks: list[dict], dropped: dict[str, str] | None = None, excluded: dict[str, str] | None = None) -> list[dict]:
    """판단 기록 줄 — 고른 종목·뺀 종목·지나친 종목 모두, 근거(지표·항목별 점수·가치평가)와 함께."""
    rank = {x["symbol"]: (i + 1, x.get("reason") or "") for i, x in enumerate(picks)}
    dropped, excluded = dropped or {}, excluded or {}
    out = []
    for r in rows:
        sym, m, v = r["symbol"], r.get("metrics") or {}, r.get("valuation")
        decision, reason = ("excluded", excluded[sym]) if sym in excluded else ("picked", rank[sym][1]) if sym in rank \
            else ("excluded", dropped[sym]) if sym in dropped else ("passed", "")
        out.append({"subject": sym, "name": r["name"], "score": r.get("score"), "value_score": (v or {}).get("score"), "price": m.get("price"),
                    "decision": decision, "pick_rank": rank[sym][0] if sym in rank else None, "reason": reason, "sources": r["sources"],
                    "detail": {"sector": r.get("sector"), "metrics": r.get("metrics"), "factors": r.get("factors"), "valuation": v}})
    return out


def _hot(r: dict) -> str | None:
    """코드가 고를 때 건너뛰는 이유 — 급등 직후·과열 (운용 지시 종목은 사용자가 말했으니 그대로)."""
    m = r.get("metrics") or {}
    if "운용 지시" in r["sources"]:
        return None
    if (m.get("change_5d") or 0) >= SKIP_5D:
        return f"5일 {m['change_5d'] * 100:+.1f}% 급등 직후"
    if (m.get("rsi") or 0) >= OVERHEAT_RSI:
        return f"RSI {m['rsi']:.0f} 과열"
    return None


def _sector_full(r: dict, picks: list[dict]) -> bool:
    sector = r.get("sector")
    return bool(sector) and sum(1 for x in picks if x.get("sector") == sector) >= MAX_PER_SECTOR


def code_pick(rows: list[dict], n: int, *, strict: bool = False) -> dict:
    """LLM 없이 — 운용 지시·보유 종목에서 하나씩 먼저, 나머지는 점수 순. strict: 섹터 한도·급등 직후·과열을 건너뛰고 dropped 에 이유를 남긴다."""
    picks: list[dict] = []
    dropped: dict[str, str] = {}

    def skip(r: dict) -> bool:
        if not strict:
            return False
        why = _hot(r) or (f"{r['sector']} 섹터 {MAX_PER_SECTOR}개 한도" if _sector_full(r, picks) else None)
        if why:
            dropped[r["symbol"]] = why
        return bool(why)

    for src in ("운용 지시", "보유"):
        first = next((r for r in rows if src in r["sources"] and r not in picks), None)
        if first and len(picks) < n and not skip(first):
            picks.append(first)
    for r in rows:
        if len(picks) >= n:
            break
        if r not in picks and r["score"] is not None and not skip(r):
            picks.append(r)
    return {"picks": [{"symbol": r["symbol"], "reason": _why(r)} for r in picks], "summary": "점수 순으로 골랐어요", "by": "code", "dropped": dropped}


def _why(r: dict) -> str:
    m = r.get("metrics") or {}
    bits = [", ".join(r["sources"])]
    if r.get("holding_pnl") is not None:
        bits.append(f"보유 손익 {r['holding_pnl'] * 100:+.1f}%")
    if m.get("change_20d") is not None:
        bits.append(f"주가 20일 등락 {m['change_20d'] * 100:+.1f}%")
    if (m.get("volume_ratio") or 0) > VOLUME_FROM:
        bits.append(f"거래량 {m['volume_ratio']:.1f}배")
    if r.get("value"):
        bits.append(_value_text(r["value"]))
    return " · ".join(bits)


def _value_text(v: dict) -> str:
    bits = [f"가치 {v['score']:.0f}점({v['grade']})"]
    if v.get("per"):
        bits.append(f"PER {v['per']:.1f}")
    elif v.get("loss"):
        bits.append("적자")
    if v.get("pbr"):
        bits.append(f"PBR {v['pbr']:.2f}")
    if v.get("roe") is not None:
        bits.append(f"ROE {v['roe'] * 100:.0f}%")
    if v.get("upside") is not None:
        bits.append(f"적정가치 대비 {v['upside'] * 100:+.0f}%")
    return " · ".join(bits)


def table(rows: list[dict]) -> str:
    """LLM 에게 보여 줄 점수표. 1일·5일·20일은 주가 등락(가격 변동)이고, 보유 종목의 평가 손익은 '보유 손익(평단 대비)'로 따로 —
    20일 등락을 보유 수익으로 읽고 '차익 실현'을 말하지 않게."""
    out = []
    pct = lambda v: "-" if v is None else f"{v * 100:+.1f}%"  # noqa: E731
    for r in rows[:TABLE_ROWS]:
        m = r.get("metrics")
        held = f" · 보유 손익(평단 대비) {pct(r['holding_pnl'])}" if r.get("holding_pnl") is not None else ""
        if m is None:
            out.append(f"- {r['name']}({r['symbol']}) [{', '.join(r['sources'])}] 시세를 못 받음{held}")
            continue
        out.append(f"- {r['name']}({r['symbol']}) [{', '.join(r['sources'])}] 점수 {r['score']}{held} · 주가 등락 1일 {pct(m['change_1d'])} · 5일 "
                   f"{pct(m['change_5d'])} · 20일 {pct(m['change_20d'])} · 거래량 {m['volume_ratio'] or '-'}배 · RSI {m['rsi'] or '-'} · "
                   + {1: "정배열", -1: "역배열", 0: "혼조"}[m["trend"]]
                   + (f" · 섹터 {r['sector']}" if r.get("sector") else "") + (f" · {_value_text(r['value'])}" if r.get("value") else ""))
    return "\n".join(out)


PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {"type": "array", "items": {"type": "object", "properties": {"symbol": {"type": "string"}, "reason": {"type": "string"}},
                                             "required": ["symbol", "reason"]}},
        "summary": {"type": "string"},
    },
    "required": ["picks", "summary"],
}
PICK_SYSTEM = """당신은 투자 회의의 안건을 정하는 포트폴리오 매니저입니다. 여러 종목을 훑은 점수표와 운용 지시(와 리서치 데스크 요약)를 보고,
이번 회의에서 토론할 종목을 고릅니다.
- 운용 지시(주목할 섹터·종목, 성향)를 먼저 따르세요. 보유 종목은 매도 타이밍을 보려고 이 표에서 고르지 않아도 회의에 따로 들어가니,
  자리는 새로 살 만한 종목에 쓰고 보유 종목은 더 살 이유가 분명할 때만 고르세요.
- 리서치 데스크의 시장 국면·비중 확대 섹터·발굴 종목(출처 '발굴')을 참고하되, 그대로 베끼지 말고 보유와 함께 균형을 잡으세요.
  리스크 경보 종목은 이번 회의에 꼭 넣습니다.
- 점수는 참고입니다 — 같은 섹터에 몰리지 않게 나누고, 과열(RSI 75+)은 이유가 있을 때만. 가치 점수·PER·PBR·적정가치가 있으면 비싼 종목을
  살 이유가 있는지 따져 보세요(재무는 공시 기준 추정치, 없는 종목은 평가가 없는 것이지 나쁜 것이 아닙니다).
- '주가 등락 1일·5일·20일'은 가격 변동이지 보유 수익이 아닙니다. 보유 종목의 손익은 '보유 손익(평단 대비)'만 근거로 쓰고,
  그 값이 없거나 손실이면 '차익 실현'이라고 쓰지 마세요.
- 표에 있는 종목(코드)만 고르고, 종목마다 한 문장 이유. summary 는 이번 회의 안건을 한 문장으로. 한국어."""

PickLlm = Callable[[str, str, str, dict, str | None], Awaitable[dict]]


async def _default_llm(feature: str, system: str, prompt: str, schema: dict, model: str | None) -> dict:
    return await structured(feature, prompt, schema, model=model, system_prompt=system)


LLM: PickLlm = _default_llm  # 테스트에서 가짜로 바꾼다


def llm_pick(rows: list[dict], intents: str, n: int, model: str | None = None, *, context: str = "",
             system: str = PICK_SYSTEM, feature: str = "agent_action", ask: str = "이번 회의 후보", strict: bool = False) -> dict:
    """LLM 이 고른다 — 표에 없는 종목은 버리고 모자라면 점수 순으로 채운다. 실패하면 code_pick.
    context = 운용 데스크 리서치 요약(시장 국면·섹터·발굴 이유), system·feature·ask = 고르는 사람(회의 후보 / 발굴 목록).
    strict = 한 섹터 MAX_PER_SECTOR 개를 코드가 강제 (넘친 종목은 dropped 에 이유)."""
    prompt = (f"{ask} {n}개를 고르세요.\n\n운용 지시:\n{intents or '없음'}\n"
              + (f"\n리서치 데스크:\n{context}\n" if context else "")
              + f"\n점수표 (점수 높은 순, {len(rows)}종목 중 {min(len(rows), TABLE_ROWS)}개):\n{table(rows)}")
    try:
        raw = asyncio.run(LLM(feature, system, prompt, PICK_SCHEMA, model))
    except (LLMError, TypeError, ValueError):
        return code_pick(rows, n, strict=strict)
    by_sym = {r["symbol"]: r for r in rows}
    picks: list[dict] = []
    chosen: list[dict] = []
    dropped: dict[str, str] = {}

    def full(r: dict) -> bool:
        if strict and _sector_full(r, chosen):
            dropped[r["symbol"]] = f"{r['sector']} 섹터 {MAX_PER_SECTOR}개 한도"
            return True
        return False

    for x in (raw or {}).get("picks") or []:
        sym = str((x or {}).get("symbol") or "").strip()
        if sym in by_sym and sym not in [p["symbol"] for p in picks] and len(picks) < n and not full(by_sym[sym]):
            picks.append({"symbol": sym, "reason": str(x.get("reason") or "").strip()[:200] or _why(by_sym[sym])})
            chosen.append(by_sym[sym])
    if not picks:
        return code_pick(rows, n, strict=strict)
    for r in rows:  # 모자라면 점수 순으로
        if len(picks) >= n:
            break
        if r["symbol"] not in [p["symbol"] for p in picks] and r["score"] is not None and not (strict and _hot(r)) and not full(r):
            picks.append({"symbol": r["symbol"], "reason": f"점수 순으로 채움 · {_why(r)}"})
            chosen.append(r)
    return {"picks": picks, "summary": str((raw or {}).get("summary") or "").strip()[:300], "by": "llm", "dropped": dropped}
