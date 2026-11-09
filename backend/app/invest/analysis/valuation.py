"""종목 가치평가 — OpenDART 재무 + 토스 시가총액 → 지표(PER·PBR·ROE…)·적정가치(S-RIM)·가치 점수(0~100).

숫자는 모두 코드가 만든다. 기준은 BANDS·PILLARS 표(데이터)이고 rules() 로 판단 기록(scorelog)에 그대로 남는다.

1) 재무(refresh): 장 전 리서치 때 하루 한 번 — 고유번호(일주일에 한 번)·다중회사 주요계정을 보고서 단위로 invest_fundamentals 에 쌓는다.
   이미 있는 보고서는 다시 묻지 않는다. 시간 예산(SYNC_BUDGET_SEC)을 넘기면 멈추고 다음 리서치 때 이어서.
2) 최근 12개월(financials): 사업보고서 + (이번 해 누적 − 지난해 같은 기간 누적). 재무상태는 가장 최근 보고서, 성장률은 같은 기간끼리.
3) 평가(assess): 항목마다 BANDS 구간으로 0~100 → 항목 평균이 기둥(저평가·수익성·성장·안정성) → PILLARS 가중 평균이 가치 점수.
   PER·PBR 은 절대 구간과 같은 대분류 섹터의 중앙값 대비(peer)를 함께 본다 — 은행이 늘 싸 보이고 바이오가 늘 비싸 보이는 쏠림을 줄인다.
   금융·지주·리츠는 영업이익률·부채비율·유동비율이 맞지 않아 뺀다. 항목이 절반 미만이면 점수를 내지 않는다(0 이 아니라 '없음').
4) 적정가치(fair_value, S-RIM): 자기자본 + 자기자본 × (ROE − 요구수익률) × w ÷ (1 + 요구수익률 − w). ROE 는 최근 것에 무게를 둔 3개 값
   가중 평균, w 는 초과이익 지속 계수. 지배주주 지분을 따로 구분하지 않은 추정치 — 정답이 아니라 비교용 기준선이다.

주요계정에는 현금흐름·배당이 없어 배당수익률·FCF 는 아직 보지 않는다.
"""

import json
import re
import sqlite3
import statistics
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

from app.invest.analysis import dart
from app.invest.analysis.dart import DartError
from app.invest.broker.toss import TossError
from app.invest.marketdata import market
from app.invest.marketdata.market import MarketError

S_STATE = "invest_dart_state"
CORPS_DAYS = 7                # 고유번호 목록을 다시 받는 간격
SYNC_BUDGET_SEC = 240         # 한 번의 동기화에 쓰는 시간 — 넘기면 다음 리서치 때 이어서
PAUSE_SEC = 0.1               # 요청 사이 쉬는 시간
INDEX_TTL = 6 * 3600          # 시가총액은 하루 한 번 바뀐다
WORKERS = 4
MIN_COVERAGE = 0.5            # 평가할 수 있는 항목이 이 비율 미만이면 점수를 내지 않는다
MIN_PEERS = 5                 # 섹터 중앙값을 쓰려면 있어야 하는 종목 수
REQUIRED_RETURN = 0.10        # S-RIM 요구수익률
PERSISTENCE = 0.9             # S-RIM 초과이익 지속 계수 w
ROE_WEIGHTS = (3, 2, 1)       # 최근 ROE 부터 가중치
VALUE_TOP = 15                # 가치 스크린에서 발굴 후보로 넘기는 종목 수
VALUE_LOG = 50                # 가치 스크린에서 기록에 남기는 상위 종목 수
VALUE_MIN_CAP = 50_000_000_000     # 가치 스크린 시가총액 하한 (500억 원)
VALUE_MIN_AMOUNT = 100_000_000     # 가치 스크린 당일 거래대금 하한 (1억 원, 값이 없으면 통과)
FINANCIAL_SECTORS = {"금융", "지주사", "리츠"}  # 영업이익률·부채비율·유동비율이 맞지 않는 업종 (토스 대분류 이름)
FINANCIAL_SKIP = {"op_margin", "debt_ratio", "current_ratio", "rev_growth", "op_growth"}
ACCOUNT_KEYS = {"매출액": "revenue", "영업이익": "op_income", "당기순이익": "net_income", "자산총계": "assets", "부채총계": "liabilities",
                "자본총계": "equity", "유동자산": "current_assets", "유동부채": "current_liabilities"}
FLOWS = ("revenue", "op_income", "net_income")
INTERIM_ORDER = ("11014", "11012", "11013")   # 최근 보고서부터
# 보고서를 찾기 시작하는 날짜 — 법정 기한보다 앞서 내는 회사가 있어 넉넉히 당겼다 (월, 일)
REPORT_FROM = {"11013": (4, 15), "11012": (7, 15), "11014": (10, 15)}
ANNUAL_FROM = (2, 1)          # 사업보고서는 다음 해 이날부터
GRADES = ((75, "A"), (60, "B"), (45, "C"), (30, "D"), (0, "E"))

PILLARS: dict[str, dict] = {
    "cheap": {"label": "저평가", "weight": 0.40},
    "quality": {"label": "수익성", "weight": 0.25},
    "growth": {"label": "성장", "weight": 0.20},
    "safety": {"label": "안정성", "weight": 0.15},
}
# steps = [(기준, 점수), …] — better 'low' 는 값이 기준 이하, 'high' 는 기준 이상이면 그 점수 (위에서부터). 어디에도 안 맞으면 else.
BANDS: dict[str, dict] = {
    "per": {"label": "PER", "unit": "배", "pillar": "cheap", "better": "low", "steps": [(8, 100), (12, 80), (18, 60), (30, 35)], "else": 15},
    "per_rel": {"label": "PER ÷ 업종 중앙값", "unit": "배", "pillar": "cheap", "better": "low",
                "steps": [(0.6, 100), (0.8, 80), (1.0, 60), (1.3, 40)], "else": 20},
    "pbr": {"label": "PBR", "unit": "배", "pillar": "cheap", "better": "low", "steps": [(0.7, 100), (1.0, 80), (1.5, 60), (3.0, 35)], "else": 15},
    "pbr_rel": {"label": "PBR ÷ 업종 중앙값", "unit": "배", "pillar": "cheap", "better": "low",
                "steps": [(0.6, 100), (0.8, 80), (1.0, 60), (1.3, 40)], "else": 20},
    "upside": {"label": "적정가치 대비 여유", "unit": "%", "pillar": "cheap", "better": "high",
               "steps": [(0.5, 100), (0.2, 80), (0.0, 60), (-0.3, 35)], "else": 15},
    "roe": {"label": "ROE", "unit": "%", "pillar": "quality", "better": "high",
            "steps": [(0.20, 100), (0.15, 85), (0.10, 65), (0.05, 45), (0.0, 25)], "else": 0},
    "op_margin": {"label": "영업이익률", "unit": "%", "pillar": "quality", "better": "high",
                  "steps": [(0.20, 100), (0.10, 80), (0.05, 60), (0.0, 35)], "else": 0},
    "rev_growth": {"label": "매출 성장", "unit": "%", "pillar": "growth", "better": "high",
                   "steps": [(0.30, 100), (0.15, 80), (0.05, 60), (0.0, 40)], "else": 15},
    "op_growth": {"label": "영업이익 성장", "unit": "%", "pillar": "growth", "better": "high",
                  "steps": [(0.50, 100), (0.20, 80), (0.05, 60), (0.0, 40)], "else": 15},
    "debt_ratio": {"label": "부채비율", "unit": "%", "pillar": "safety", "better": "low",
                   "steps": [(0.5, 100), (1.0, 80), (2.0, 50), (4.0, 20)], "else": 0},
    "current_ratio": {"label": "유동비율", "unit": "%", "pillar": "safety", "better": "high",
                      "steps": [(2.0, 100), (1.5, 80), (1.0, 50)], "else": 15},
}
LOSS_POINTS = 0        # 최근 12개월 적자면 PER 점수
TURNAROUND_POINTS = 80 # 지난해 같은 기간 영업손실 → 이번 영업이익


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def rules() -> dict:
    """가치평가 기준 전부 — 판단 기록에 그대로 남긴다."""
    return {"pillars": PILLARS, "bands": BANDS, "grades": GRADES, "min_coverage": MIN_COVERAGE, "min_peers": MIN_PEERS,
            "required_return": REQUIRED_RETURN, "persistence": PERSISTENCE, "roe_weights": ROE_WEIGHTS, "financial_sectors": sorted(FINANCIAL_SECTORS),
            "financial_skip": sorted(FINANCIAL_SKIP), "loss_points": LOSS_POINTS, "turnaround_points": TURNAROUND_POINTS,
            "value_top": VALUE_TOP, "value_min_cap": VALUE_MIN_CAP, "value_min_amount": VALUE_MIN_AMOUNT}


# ---- 재무 받기 ----
def _account(name: object) -> str | None:
    text = re.sub(r"\s|\(.*?\)", "", str(name or ""))
    return ACCOUNT_KEYS.get(text)


def _amount(value: object) -> float | None:
    text = str(value if value is not None else "").replace(",", "").strip()
    if text in ("", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_rows(rows: list[dict]) -> dict[str, dict]:
    """주요계정 응답 → {종목: 보고서 하나} — 연결(CFS)이 있으면 연결, 없으면 별도."""
    grouped: dict[tuple[str, str], dict] = {}
    for r in rows:
        symbol, fs = str(r.get("stock_code") or "").strip(), str(r.get("fs_div") or "")
        key = _account(r.get("account_nm"))
        if len(symbol) != 6 or fs not in ("CFS", "OFS") or key is None:
            continue
        rep = grouped.setdefault((symbol, fs), {
            "bsns_year": int(r.get("bsns_year") or 0), "reprt_code": str(r.get("reprt_code") or ""), "fs_div": fs,
            "currency": str(r.get("currency") or "KRW"), "rcept_no": str(r.get("rcept_no") or ""),
            "period_end": str(r.get("thstrm_dt") or ""), "accounts": {}})
        rep["accounts"][key] = {"cur": _amount(r.get("thstrm_amount")), "cur_add": _amount(r.get("thstrm_add_amount")),
                                "prev": _amount(r.get("frmtrm_amount")), "prev_add": _amount(r.get("frmtrm_add_amount")),
                                "prev2": _amount(r.get("bfefrmtrm_amount"))}
    out: dict[str, dict] = {}
    for (symbol, fs), rep in grouped.items():
        if fs == "CFS" or symbol not in out:
            out[symbol] = rep
    return out


def reports_due(today: date) -> list[tuple[int, str]]:
    """지금 나와 있을 수 있는 보고서 (최근 것부터) — 이번 해 분기·지난해 전부·재작년 사업보고서."""
    y = today.year
    due = [(y, r) for r in INTERIM_ORDER if (today.month, today.day) >= REPORT_FROM[r]]
    if (today.month, today.day) >= ANNUAL_FROM:
        due.append((y - 1, "11011"))
    due += [(y - 1, r) for r in INTERIM_ORDER]
    due.append((y - 2, "11011"))
    return due


def state(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (S_STATE,)).fetchone()
    try:
        raw = json.loads(row["value"]) if row else {}
    except ValueError:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def _put_state(conn: sqlite3.Connection, st: dict) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (S_STATE, json.dumps(st, ensure_ascii=False)))
    conn.commit()


def _sync_corps(conn: sqlite3.Connection, st: dict, now: datetime) -> None:
    last = st.get("corps_at")
    fresh = last and (now.date() - date.fromisoformat(last)).days < CORPS_DAYS
    if fresh and conn.execute("SELECT 1 FROM invest_dart_corps LIMIT 1").fetchone():
        return
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    conn.executemany("INSERT INTO invest_dart_corps (symbol, corp_code, corp_name, modify_date, refreshed_at) VALUES (?, ?, ?, ?, ?) "
                     "ON CONFLICT(symbol) DO UPDATE SET corp_code = excluded.corp_code, corp_name = excluded.corp_name, "
                     "modify_date = excluded.modify_date, refreshed_at = excluded.refreshed_at",
                     [(c["symbol"], c["corp_code"], c["corp_name"], c["modify_date"], stamp) for c in dart.corp_codes()])
    st["corps_at"] = now.date().isoformat()
    conn.commit()


def _save_report(conn: sqlite3.Connection, symbol: str, rep: dict, stamp: str) -> None:
    conn.execute("INSERT INTO invest_fundamentals (symbol, bsns_year, reprt_code, fs_div, currency, rcept_no, period_end, accounts, fetched_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(symbol, bsns_year, reprt_code) DO UPDATE SET fs_div = excluded.fs_div, "
                 "currency = excluded.currency, rcept_no = excluded.rcept_no, period_end = excluded.period_end, accounts = excluded.accounts, "
                 "fetched_at = excluded.fetched_at",
                 (symbol, rep["bsns_year"], rep["reprt_code"], rep["fs_div"], rep["currency"], rep["rcept_no"], rep["period_end"],
                  json.dumps(rep["accounts"], ensure_ascii=False), stamp))


def refresh(conn: sqlite3.Connection, symbols: set[str] | None = None, *, now: datetime | None = None, log: Callable[[str], None] | None = None,
            check: Callable[[], None] | None = None) -> dict:
    """재무 동기화 → {ok, saved, calls, done, reason}. 키가 없으면 아무것도 묻지 않는다. 오늘 끝냈으면 건너뛴다."""
    if not dart.configured():
        return {"ok": False, "saved": 0, "calls": 0, "done": False, "reason": dart.NOT_CONFIGURED}
    now = now or market.now_kst()
    st = state(conn)
    today = now.date().isoformat()
    if st.get("synced_day") == today:
        return {"ok": True, "saved": 0, "calls": 0, "done": True, "reason": "오늘 이미 받았어요"}
    say = log or (lambda _m: None)
    tick = check or (lambda: None)
    saved = calls = 0
    done = True
    reason = ""
    started = time.monotonic()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    try:
        _sync_corps(conn, st, now)
        corps = {r["symbol"]: r["corp_code"] for r in conn.execute("SELECT symbol, corp_code FROM invest_dart_corps")
                 if symbols is None or r["symbol"] in symbols}
        have = {(r["symbol"], r["bsns_year"], r["reprt_code"]) for r in conn.execute("SELECT symbol, bsns_year, reprt_code FROM invest_fundamentals")}
        for year, code in reports_due(now.date()):
            missing = [c for s, c in corps.items() if (s, year, code) not in have]
            for i in range(0, len(missing), dart.MAX_CORPS):
                tick()
                if time.monotonic() - started > SYNC_BUDGET_SEC:
                    done = False
                    reason = f"시간 예산 {SYNC_BUDGET_SEC}초를 넘겨 다음 리서치 때 이어 받아요"
                    raise StopIteration
                reports = parse_rows(dart.multi_accounts(missing[i:i + dart.MAX_CORPS], year, code))
                calls += 1
                for symbol, rep in reports.items():
                    if symbol in corps:
                        _save_report(conn, symbol, rep, stamp)
                        saved += 1
                conn.commit()
                sleep(PAUSE_SEC)
    except StopIteration:
        pass
    except DartError as e:
        done = False
        reason = str(e)
    st.update(last_run=stamp, last_saved=saved, last_calls=calls, last_error=reason if not done else "")
    if done:
        st["synced_day"] = today
    _put_state(conn, st)
    say(f"OpenDART 재무: {calls}번 물어 보고서 {saved}건 저장" + (f" · {reason}" if reason else ""))
    return {"ok": done, "saved": saved, "calls": calls, "done": done, "reason": reason}


def status(conn: sqlite3.Connection) -> dict:
    st = state(conn)
    row = conn.execute("SELECT COUNT(DISTINCT symbol) AS n, COUNT(*) AS reports, MAX(fetched_at) AS at FROM invest_fundamentals").fetchone()
    corps = conn.execute("SELECT COUNT(*) AS n FROM invest_dart_corps").fetchone()["n"]
    return {"configured": dart.configured(), "corps": corps, "symbols": row["n"], "reports": row["reports"], "fetched_at": row["at"],
            "synced_day": st.get("synced_day"), "last_run": st.get("last_run"), "last_error": st.get("last_error") or ""}


# ---- 최근 12개월 재무 ----
def load_reports(conn: sqlite3.Connection, symbols: list[str] | None = None) -> dict[str, list[dict]]:
    sql = "SELECT * FROM invest_fundamentals"
    args: tuple = ()
    if symbols is not None:
        sql += f" WHERE symbol IN ({','.join('?' * len(symbols))})"
        args = tuple(symbols)
    out: dict[str, list[dict]] = {}
    for r in conn.execute(sql, args):
        out.setdefault(r["symbol"], []).append({**dict(r), "accounts": json.loads(r["accounts"])})
    return out


def _cell(acc: dict, key: str, field: str) -> float | None:
    return (acc.get(key) or {}).get(field)


def _ratio(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b else None


def financials(reports: list[dict]) -> dict | None:
    """보고서들 → 최근 12개월 손익·최신 재무상태·같은 기간 대비 값. 사업보고서가 없으면 None. 원화 재무만 (달러 공시는 시가총액과 단위가 달라 뺀다)."""
    annual = sorted((r for r in reports if r["reprt_code"] == "11011" and r["currency"] == "KRW"), key=lambda r: r["bsns_year"], reverse=True)
    if not annual:
        return None
    a = annual[0]
    aa = a["accounts"]
    interims = [r for r in reports if r["bsns_year"] == a["bsns_year"] + 1 and r["reprt_code"] in INTERIM_ORDER and r["currency"] == "KRW"]
    i = min(interims, key=lambda r: INTERIM_ORDER.index(r["reprt_code"])) if interims else None
    ia = i["accounts"] if i else {}
    out: dict = {"year": a["bsns_year"], "fs_div": (i or a)["fs_div"], "period_end": (i or a)["period_end"], "rcept_no": (i or a)["rcept_no"],
                 "report": f"{(i or a)['bsns_year']} {dart.REPORTS[(i or a)['reprt_code']]}", "ttm": bool(i)}
    for k in FLOWS:
        base = _cell(aa, k, "cur")
        if i:
            cur = _cell(ia, k, "cur_add")
            cur = _cell(ia, k, "cur") if cur is None else cur
            prior = _cell(ia, k, "prev_add")
            prior = _cell(ia, k, "prev") if prior is None else prior
            out[k] = base + cur - prior if None not in (base, cur, prior) else base
            out[f"{k}_now"], out[f"{k}_prior"] = cur, prior
        else:
            out[k] = base
            out[f"{k}_now"], out[f"{k}_prior"] = base, _cell(aa, k, "prev")
    for k in ("equity", "assets", "liabilities", "current_assets", "current_liabilities"):
        latest = _cell(ia, k, "cur") if i else None
        out[k] = latest if latest is not None else _cell(aa, k, "cur")
    # ROE: 최근 값 → 그 앞 두 해 (적자도 그대로 — 지나간 손실을 지우지 않는다)
    if i:
        pairs = [(out["net_income"], out["equity"]), (_cell(aa, "net_income", "cur"), _cell(aa, "equity", "cur")),
                 (_cell(aa, "net_income", "prev"), _cell(aa, "equity", "prev"))]
    else:
        pairs = [(_cell(aa, "net_income", f), _cell(aa, "equity", f)) for f in ("cur", "prev", "prev2")]
    out["roe_history"] = [_ratio(n, e) for n, e in pairs]
    return out


# ---- 평가 ----
def band_points(key: str, value: float) -> int:
    b = BANDS[key]
    for limit, points in b["steps"]:
        if (value <= limit if b["better"] == "low" else value >= limit):
            return points
    return b["else"]


def fair_value(equity: float | None, roe_history: list[float | None]) -> dict | None:
    """S-RIM 적정 시가총액. ROE 는 값이 있는 것만 ROE_WEIGHTS 로 가중 평균. 음수면 0 으로."""
    known = [(r, w) for r, w in zip(roe_history, ROE_WEIGHTS) if r is not None]
    if not equity or equity <= 0 or not known:
        return None
    roe = sum(r * w for r, w in known) / sum(w for _, w in known)
    value = equity + equity * (roe - REQUIRED_RETURN) * PERSISTENCE / (1 + REQUIRED_RETURN - PERSISTENCE)
    return {"value": max(value, 0.0), "roe_used": roe, "required_return": REQUIRED_RETURN, "persistence": PERSISTENCE}


def indicators(fin: dict, market_cap: float | None) -> dict:
    """재무 + 시가총액 → 지표. 적자 PER·자본잠식 PBR 처럼 뜻이 없는 값은 None."""
    net, equity = fin.get("net_income"), fin.get("equity")
    fair = fair_value(equity, fin.get("roe_history") or [])
    op_now, op_prior = fin.get("op_income_now"), fin.get("op_income_prior")
    return {
        "market_cap": market_cap, "loss": net is not None and net < 0,
        "per": market_cap / net if market_cap and net and net > 0 else None,
        "pbr": market_cap / equity if market_cap and equity and equity > 0 else None,
        "roe": _ratio(net, equity) if equity and equity > 0 else None,
        "op_margin": _ratio(fin.get("op_income"), fin.get("revenue")),
        "rev_growth": fin["revenue_now"] / fin["revenue_prior"] - 1 if fin.get("revenue_now") is not None and (fin.get("revenue_prior") or 0) > 0 else None,
        "op_growth": op_now / op_prior - 1 if op_now is not None and (op_prior or 0) > 0 else None,
        "turnaround": op_now is not None and op_prior is not None and op_prior <= 0 < op_now,
        "op_loss": op_now is not None and op_now <= 0,
        "debt_ratio": _ratio(fin.get("liabilities"), equity) if equity and equity > 0 else None,
        "current_ratio": _ratio(fin.get("current_assets"), fin.get("current_liabilities")),
        "fair_value": fair["value"] if fair else None, "roe_used": fair["roe_used"] if fair else None,
        "upside": fair["value"] / market_cap - 1 if fair and market_cap else None,
    }


def grade(score: float) -> str:
    return next(g for limit, g in GRADES if score >= limit)


def assess(fin: dict, market_cap: float | None, *, peer: dict | None = None, financial: bool = False) -> dict:
    """가치 점수 → {score, grade, coverage, pillars, items, missing, indicators, basis}. 항목이 모자라면 score 는 None."""
    ind = indicators(fin, market_cap)
    values = dict(ind)
    peer = peer or {}
    values["per_rel"] = ind["per"] / peer["per"] if ind["per"] and peer.get("per") else None
    values["pbr_rel"] = ind["pbr"] / peer["pbr"] if ind["pbr"] and peer.get("pbr") else None
    applicable = [k for k in BANDS if not (financial and k in FINANCIAL_SKIP)]
    items, missing = [], []
    for key in applicable:
        value, note = values.get(key), ""
        if key == "per" and value is None and ind["loss"]:
            points, note = LOSS_POINTS, "적자"
        elif key == "op_growth" and value is None and ind["turnaround"]:
            points, note = TURNAROUND_POINTS, "흑자 전환"
        elif key == "op_growth" and value is None and ind["op_loss"]:
            points, note = 0, "영업손실"
        elif value is None:
            missing.append(key)
            continue
        else:
            points = band_points(key, value)
        items.append({"key": key, "label": BANDS[key]["label"], "pillar": BANDS[key]["pillar"], "value": value, "points": points, "note": note})
    pillars = {}
    for key, p in PILLARS.items():
        got = [x["points"] for x in items if x["pillar"] == key]
        if got:
            pillars[key] = {"label": p["label"], "weight": p["weight"], "score": round(sum(got) / len(got), 1)}
    coverage = round(len(items) / len(applicable), 2) if applicable else 0.0
    out: dict = {"coverage": coverage, "pillars": pillars, "items": items, "missing": missing, "missing_labels": [BANDS[k]["label"] for k in missing],
                 "indicators": ind, "financial": financial,
                 "peer": peer or None, "basis": {k: fin.get(k) for k in ("report", "period_end", "rcept_no", "fs_div", "ttm", "revenue", "op_income",
                                                                         "net_income", "equity", "liabilities")}}
    if coverage < MIN_COVERAGE or "cheap" not in pillars:
        return {**out, "score": None, "grade": None, "reason": f"평가할 수 있는 항목이 {len(items)}/{len(applicable)}개뿐이에요"}
    total = sum(x["weight"] for x in pillars.values())
    score = round(sum(x["score"] * x["weight"] for x in pillars.values()) / total, 1)
    return {**out, "score": score, "grade": grade(score), "reason": ""}


def brief(v: dict | None) -> dict | None:
    """화면·프롬프트용 요약 (근거 전체는 판단 기록에)."""
    if not v or v.get("score") is None:
        return None
    ind = v["indicators"]
    return {"score": v["score"], "grade": v["grade"], "per": ind["per"], "pbr": ind["pbr"], "roe": ind["roe"], "upside": ind["upside"],
            "loss": ind["loss"], "report": v["basis"].get("report")}


# ---- 시장 전체 ----
def sector_index(p, notes: list[str] | None = None) -> dict[str, dict]:
    """대분류 섹터의 구성 주식 → {종목: {sector, name, market_cap, trading_amount}} (6시간 캐시, 토스 섹터 API 39번). 섹터 쏠림·업종 비교·가치 스크린이 쓴다."""
    def load() -> dict[str, dict]:
        tops = [s for s in p.sectors() if (s.get("depth") or 0) == 0]

        def stocks(sec: dict) -> list[dict]:
            try:
                return p.sector_stocks(sec["id"])
            except (MarketError, TossError):
                return []

        with ThreadPoolExecutor(WORKERS) as ex:
            lists = list(ex.map(stocks, tops))
        out: dict[str, dict] = {}
        for sec, rows in zip(tops, lists):
            for r in rows:
                out.setdefault(r["symbol"], {"sector": sec["name"], "name": r.get("name") or "", "market_cap": r.get("market_cap"),
                                             "trading_amount": r.get("trading_amount")})
        return out

    try:
        return market.cached(("sector_index", p.key), INDEX_TTL, load)
    except (MarketError, TossError) as e:
        if notes is not None:
            notes.append(f"섹터 구성 종목을 받지 못했어요: {e}")
        return {}


def peer_medians(reports: dict[str, list[dict]], index: dict[str, dict]) -> dict[str, dict]:
    """대분류 섹터별 PER·PBR 중앙값 — 흑자·자본 양수 종목만, MIN_PEERS 개 이상일 때."""
    per: dict[str, list[float]] = {}
    pbr: dict[str, list[float]] = {}
    for symbol, reps in reports.items():
        info = index.get(symbol)
        fin = financials(reps) if info and info.get("market_cap") else None
        if not fin:
            continue
        ind = indicators(fin, info["market_cap"])
        if ind["per"]:
            per.setdefault(info["sector"], []).append(ind["per"])
        if ind["pbr"]:
            pbr.setdefault(info["sector"], []).append(ind["pbr"])
    return {s: {"per": statistics.median(per[s]) if len(per.get(s, [])) >= MIN_PEERS else None,
                "pbr": statistics.median(pbr[s]) if len(pbr.get(s, [])) >= MIN_PEERS else None}
            for s in set(per) | set(pbr)}


def _cap(p, symbol: str, info: dict | None, price: float | None) -> float | None:
    if info and info.get("market_cap"):
        return info["market_cap"]
    try:
        shares = p.info(symbol).get("shares_outstanding")
        price = price or (p.quotes([symbol]).get(symbol) or {}).get("price")
    except (MarketError, TossError):
        return None
    return shares * price if shares and price else None


def evaluate(conn: sqlite3.Connection, p, symbols: list[str], *, index: dict[str, dict] | None = None,
             prices: dict[str, float | None] | None = None) -> dict[str, dict]:
    """종목들의 가치평가 → {종목: assess 결과}. 재무가 없는 종목은 뺀다 (키 없음·미공시·ETF)."""
    index = sector_index(p) if index is None else index
    reports = load_reports(conn)
    if not reports:
        return {}
    peers = peer_medians(reports, index)
    prices = prices or {}
    out: dict[str, dict] = {}
    for symbol in symbols:
        fin = financials(reports.get(symbol) or [])
        if not fin:
            continue
        info = index.get(symbol)
        sector = (info or {}).get("sector")
        out[symbol] = assess(fin, _cap(p, symbol, info, prices.get(symbol)), peer=peers.get(sector), financial=sector in FINANCIAL_SECTORS)
    return out


def screen(conn: sqlite3.Connection, p, *, exclude: set[str] = frozenset(), index: dict[str, dict] | None = None) -> dict:
    """시장 전체 가치 스크린 — 재무가 있는 종목을 점수 순으로 → {picks(발굴 풀로), rows(기록용 상위), universe, scored}.
    시세를 일일이 받지 않고 섹터 API 의 시가총액을 쓴다. 조용한 중소형주가 후보에 들어오는 길."""
    index = sector_index(p) if index is None else index
    reports = load_reports(conn)
    if not reports or not index:
        return {"picks": [], "rows": [], "universe": len(index), "scored": 0}
    peers = peer_medians(reports, index)
    rows = []
    for symbol, info in index.items():
        cap, amount = info.get("market_cap"), info.get("trading_amount")
        if not cap or cap < VALUE_MIN_CAP or (amount is not None and amount < VALUE_MIN_AMOUNT):
            continue
        fin = financials(reports.get(symbol) or [])
        if not fin:
            continue
        v = assess(fin, cap, peer=peers.get(info["sector"]), financial=info["sector"] in FINANCIAL_SECTORS)
        if v["score"] is not None:
            rows.append({"symbol": symbol, "name": info["name"], "security_type": "STOCK", "sector": info["sector"], "valuation": v})
    rows.sort(key=lambda r: -r["valuation"]["score"])
    picks = [r for r in rows if r["symbol"] not in exclude and not r["valuation"]["indicators"]["loss"]][:VALUE_TOP]
    return {"picks": picks, "rows": rows[:VALUE_LOG], "universe": len(index), "scored": len(rows)}


def report(conn: sqlite3.Connection, p, symbol: str) -> dict:
    """한 종목의 가치평가 — 에이전트 도구·화면. 없으면 이유를 돌려준다."""
    index = sector_index(p)
    v = evaluate(conn, p, [symbol], index=index).get(symbol)
    if v is None:
        reason = dart.NOT_CONFIGURED if not dart.configured() and not load_reports(conn, [symbol]) else "재무 자료가 없어요 (ETF·미공시·우선주 등)"
        return {"symbol": symbol, "available": False, "reason": reason}
    ind = v["indicators"]
    return {"symbol": symbol, "available": True, "sector": (index.get(symbol) or {}).get("sector"), "score": v["score"], "grade": v["grade"],
            "coverage": v["coverage"], "reason": v["reason"], "pillars": v["pillars"], "items": v["items"], "missing": v["missing"], "peer": v["peer"],
            "indicators": ind, "basis": v["basis"], "fair_value_note": f"S-RIM 추정 (요구수익률 {REQUIRED_RETURN:.0%}, 지속 계수 {PERSISTENCE}) — 정답이 아니라 비교 기준"}
