"""공시 재료 — OpenDART 공시 목록을 코드 표(RULES)로 나눠 가치평가에 붙인다. 숫자와 분류는 모두 코드, LLM 은 읽고 해석만.

- 받기(refresh): 한 종목의 최근 DAYS 일 공시 목록 → invest_disclosures. TTL_MIN 분 안에 다시 묻지 않는다(invest_disclosure_fetch).
  유상증자 · 전환사채 · 신주인수권부사채 · 자기주식 취득은 주요사항보고서 상세를 한 번 더 받아 희석률 · 자사주 규모를 남긴다.
- 분류(classify): 보고서 이름을 RULES 의 정규식으로 위에서부터 맞춘다 → 종류 · 이름 · 영향(-3 ~ +2) · 이유. 맞는 게 없으면 '기타'(점수 0).
  유상증자는 방식(제3자배정 -1 · 주주배정 · 일반공모 -2)과 희석률(BIG_DILUTION 넘으면 -3)로 영향을 다시 정한다.
- 요약(summary): 재료 점수 = 영향 × 기간 무게(RECENT_DAYS 일 안 1, 그 뒤 0.5)의 합을 SCORE_CLIP 로 자른 값. 정정 공시는 원 공시가 있으면 세지 않는다.
  희석률은 최근 DAYS 일 결정의 합(이미 재무에 들어갔을 수 있어 '최대치'로 본다). 가치 점수(valuation)에는 더하지 않고 옆에 둔다.
- 풍문 조회공시(조회공시요구 · 풍문또는보도에대한해명)는 점수 0 이지만 따로 표시한다 — 소문이 돌았고 회사가 답했다는 사실 자체가 재료다.
"""

import json
import re
import sqlite3
from datetime import date, datetime, timedelta

from app.invest.analysis import dart
from app.invest.analysis.dart import DartError

DAYS = 90             # 받는 기간
RECENT_DAYS = 30      # 이 안의 공시는 무게 1, 그 뒤는 0.5
TTL_MIN = 30          # 같은 종목을 다시 묻지 않는 시간
SCORE_CLIP = 5
BIG_DILUTION = 0.20   # 유상증자 희석률이 이보다 크면 영향 -3
SHOWN = 12            # 요약에 보여 주는 공시 수
RETRY_DETAIL = 5      # 상세를 못 받은 결정을 refresh 마다 다시 묻는 수 (최근 RETRY_DAYS 일 것만)
RETRY_DAYS = 7
DETAIL_LOOKBACK = 120 # 상세를 찾을 때 접수일에서 거슬러 가는 날 (정정은 원 결정일로 잡힌다)
URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"

# (키, 보고서 이름 정규식, 이름, 영향, 이유, 상세 종류) — 위에서부터 처음 맞는 것. 영향: -3 큰 악재 ~ +2 큰 호재, 0 은 '읽어 볼 것'
RULES: list[tuple[str, str, str, int, str, str | None]] = [
    ("embezzle", r"횡령|배임", "횡령 · 배임", -3, "경영 신뢰 · 거래정지 위험", None),
    ("delisting", r"상장폐지|관리종목|매매거래정지|상장적격성", "상장 유지 위험", -3, "거래정지 · 상장폐지로 이어질 수 있음", None),
    ("audit", r"감사의견|감사보고서제출지연|감사범위제한", "감사 의견 문제", -3, "재무 신뢰성", None),
    ("unfaithful", r"불성실공시", "불성실공시", -2, "공시 신뢰 · 벌점", None),
    ("capital_cut", r"감자결정", "감자", -2, "자본 잠식 · 주주 손실 가능", None),
    ("rights", r"유상증자결정", "유상증자", -2, "주식 수가 늘어 주당 가치 희석", "rights"),
    ("cb", r"전환사채권발행결정", "전환사채 발행", -1, "전환되면 희석 (잠재 물량)", "cb"),
    ("bw", r"신주인수권부사채권발행결정", "신주인수권부사채 발행", -1, "행사되면 희석 (잠재 물량)", "bw"),
    ("cb_reprice", r"전환가액.*조정|행사가액.*조정", "전환 · 행사가액 조정", -1, "가격이 내려가면 전환 주식 수가 늘어남", None),
    ("lawsuit", r"소송등의제기|소송등의판결", "소송", -1, "결과에 따라 비용 · 영업 차질", None),
    ("contract_cancel", r"공급계약.*해지|판매.*해지", "공급계약 해지", -1, "기대 매출 사라짐", None),
    ("sales_short", r"매출액미달", "매출액 미달", -1, "상장 유지 요건 경고", None),
    ("buyback_cancel", r"자기주식취득신탁계약해지|자기주식.*해지", "자사주 신탁 해지", -1, "매입 수요가 끝남", None),
    ("treasury_sell", r"자기주식처분결정", "자사주 처분", -1, "시장에 물량이 나올 수 있음", None),
    ("cancel_shares", r"주식소각결정|자기주식소각", "주식 소각", 2, "주식 수가 줄어 주당 가치 상승", None),
    ("buyback", r"자기주식취득(신탁계약체결)?결정", "자사주 매입", 1, "주주 환원 · 매수 수요", "buyback"),
    ("dividend", r"현금ㆍ현물배당결정|현금배당결정|분기배당|중간배당", "배당 결정", 1, "주주 환원", None),
    ("bonus_issue", r"무상증자결정", "무상증자", 1, "유동성 · 주주 환원 신호 (가치는 그대로)", None),
    ("contract", r"단일판매ㆍ공급계약체결|공급계약체결", "공급계약 · 수주", 1, "매출로 이어질 계약", None),
    ("earnings", r"영업\(잠정\)실적|영업실적등에대한전망|매출액또는손익구조", "실적 발표 · 전망", 0, "숫자를 읽어 볼 것", None),
    ("rumor_query", r"조회공시요구", "풍문 조회공시 요구", 0, "거래소가 소문 · 급변의 사실 확인을 요구함", None),
    ("rumor_answer", r"풍문또는보도에대한해명", "풍문 · 보도 해명", 0, "회사가 소문에 답함 — 미확정이면 뒤에 다시 공시", None),
    ("control", r"최대주주변경|최대주주등소유주식변동", "최대주주 변동", 0, "지배구조 변화", None),
    ("restructure", r"합병|분할|영업양수|영업양도|주식교환|타법인주식및출자증권(취득|처분)", "합병 · 분할 · 양수도", 0, "사업 구조 변경 — 조건을 읽어 볼 것", None),
    ("block", r"주식등의대량보유상황보고서", "5% 이상 지분 변동", 0, "큰손의 지분 변화", None),
    ("insider", r"임원ㆍ주요주주특정증권등소유상황보고서", "임원 · 주요주주 지분", 0, "내부자 매매 — 많으면 의미 있음", None),
    ("ir", r"기업설명회", "IR 개최", 0, "회사 설명 자리", None),
]
_COMPILED = [(k, re.compile(p), label, impact, why, det) for k, p, label, impact, why, det in RULES]
_CORRECTION = re.compile(r"^\s*\[[^\]]*\]\s*")
RUMOR_KINDS = {"rumor_query", "rumor_answer"}


def base_name(report_nm: str) -> str:
    """'[기재정정]주요사항보고서(유상증자결정)' → '주요사항보고서(유상증자결정)'."""
    return re.sub(r"\s+", " ", _CORRECTION.sub("", report_nm or "")).strip()


def is_correction(report_nm: str) -> bool:
    return bool(_CORRECTION.match(report_nm or ""))


def classify(report_nm: str) -> dict | None:
    name = base_name(report_nm).replace(" ", "")
    for key, rx, label, impact, why, det in _COMPILED:
        if rx.search(name):
            return {"kind": key, "label": label, "impact": impact, "why": why, "detail": det}
    return None


def rules() -> dict:
    """판단 기록(scorelog)에 남길 기준."""
    return {"rules": [{"kind": k, "pattern": p, "label": label, "impact": i, "why": w} for k, p, label, i, w, _ in RULES],
            "days": DAYS, "recent_days": RECENT_DAYS, "score_clip": SCORE_CLIP, "big_dilution": BIG_DILUTION}


def _num(v: object) -> float | None:
    s = str(v or "").replace(",", "").strip()
    try:
        return float(s) if s not in ("", "-") else None
    except ValueError:
        return None


def parse_detail(kind: str, row: dict) -> dict:
    """주요사항보고서 상세 한 줄 → 희석 · 자사주 숫자. 없는 값은 None."""
    if kind == "rights":
        new, before = _num(row.get("nstk_ostk_cnt")), _num(row.get("bfic_tisstk_ostk"))
        return {"new_shares": new, "shares_before": before, "dilution": new / before if new and before else None,
                "method": str(row.get("ic_mthn") or "").strip() or None}
    if kind == "cb":
        pct = _num(row.get("cvisstk_tisstk_vs"))
        return {"new_shares": _num(row.get("cvisstk_cnt")), "dilution": pct / 100 if pct is not None else None,
                "amount": _num(row.get("bd_fta")), "conversion_price": _num(row.get("cv_prc"))}
    if kind == "bw":
        pct = _num(row.get("nstk_isstk_tisstk_vs"))
        return {"new_shares": _num(row.get("nstk_isstk_cnt")), "dilution": pct / 100 if pct is not None else None,
                "amount": _num(row.get("bd_fta"))}
    if kind == "buyback":
        return {"shares": _num(row.get("aqpln_stk_ostk")), "amount": _num(row.get("aqpln_prc_ostk")),
                "method": str(row.get("aq_mth") or "").strip() or None, "purpose": str(row.get("aq_pp") or "").strip() or None}
    return {}


def rights_impact(detail: dict) -> int:
    """유상증자 영향 — 희석이 크면 -3, 제3자배정(대개 할인 · 전략 투자)은 -1, 주주배정 · 일반공모는 -2."""
    d = detail.get("dilution")
    if d is not None and d > BIG_DILUTION:
        return -3
    return -1 if "제3자" in str(detail.get("method") or "") else -2


def classify_kind(kind: str) -> dict:
    k, _p, label, impact, why, det = next(x for x in RULES if x[0] == kind)
    return {"kind": k, "label": label, "impact": impact, "why": why, "detail": det}


def _detail(c: dict, code: str, rcept_no: str, day: str) -> dict:
    """주요사항보고서 상세 → parse_detail. 못 받으면 {} (다음 refresh 때 다시)."""
    d = date.fromisoformat(day)
    try:  # 상세 API 는 '원래 결정일'로 찾고 '가장 최근 정정'의 접수번호를 돌려준다 — 넓게 찾고 접수번호로 맞춘다
        rows = dart.decision_detail(c["detail"], code, _ymd(d - timedelta(days=DETAIL_LOOKBACK)), _ymd(d))
    except DartError:
        return {}
    hit = next((x for x in rows if str(x.get("rcept_no")) == rcept_no), None)
    return parse_detail(c["detail"], hit) if hit else {}


def corp_code(conn: sqlite3.Connection, symbol: str) -> str | None:
    row = conn.execute("SELECT corp_code FROM invest_dart_corps WHERE symbol = ?", (symbol,)).fetchone()
    return row["corp_code"] if row else None


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def refresh(conn: sqlite3.Connection, symbol: str, now: datetime | None = None, *, force: bool = False) -> dict:
    """한 종목의 최근 DAYS 일 공시를 받아 저장한다 → {ok, saved, reason}. 키 · 고유번호가 없거나 TTL 안이면 묻지 않는다."""
    if not dart.configured():
        return {"ok": False, "saved": 0, "reason": dart.NOT_CONFIGURED}
    code = corp_code(conn, symbol)
    if not code:
        return {"ok": False, "saved": 0, "reason": "OpenDART 고유번호가 없는 종목이에요 (ETF · 우선주 · 아직 목록을 안 받음)"}
    now = now or datetime.now()
    row = conn.execute("SELECT fetched_at FROM invest_disclosure_fetch WHERE symbol = ?", (symbol,)).fetchone()
    if row and not force and now - datetime.fromisoformat(row["fetched_at"]) < timedelta(minutes=TTL_MIN):
        return {"ok": True, "saved": 0, "reason": "최근에 받았어요"}
    end, start = now.date(), now.date() - timedelta(days=DAYS)
    try:
        rows = dart.disclosure_list(code, _ymd(start), _ymd(end))
    except DartError as e:
        return {"ok": False, "saved": 0, "reason": str(e)}
    have = {r["rcept_no"] for r in conn.execute("SELECT rcept_no FROM invest_disclosures WHERE symbol = ?", (symbol,))}
    saved = 0
    for r in rows:
        no, name = str(r.get("rcept_no") or ""), str(r.get("report_nm") or "").strip()
        if not no or not name or no in have:
            continue
        c = classify(name)
        day = str(r.get("rcept_dt") or "")
        day = f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else now.date().isoformat()
        detail = _detail(c, code, no, day) if c and c["detail"] else {}
        impact = rights_impact(detail) if c and c["kind"] == "rights" and detail else (c["impact"] if c else 0)
        conn.execute("INSERT OR REPLACE INTO invest_disclosures (rcept_no, symbol, corp_name, report_nm, rcept_dt, flr_nm, rm, kind, impact, detail) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (no, symbol, str(r.get("corp_name") or ""), name, day, str(r.get("flr_nm") or ""), str(r.get("rm") or ""),
                      c["kind"] if c else None, impact, json.dumps(detail, ensure_ascii=False)))
        saved += 1
    # 상세를 못 받은 결정(당일 공시는 상세 API 에 늦게 잡힌다)은 다시 — 한 번에 RETRY_DETAIL 개까지
    since = (now.date() - timedelta(days=RETRY_DAYS)).isoformat()
    for r in conn.execute("SELECT rcept_no, kind, rcept_dt FROM invest_disclosures WHERE symbol = ? AND detail = '{}' AND rcept_dt >= ? AND kind IN "
                          f"({','.join('?' * len(dart.DETAIL))}) ORDER BY rcept_dt DESC LIMIT ?", (symbol, since, *dart.DETAIL, RETRY_DETAIL)).fetchall():
        c = classify_kind(r["kind"])
        detail = _detail(c, code, r["rcept_no"], r["rcept_dt"])
        if detail:
            impact = rights_impact(detail) if r["kind"] == "rights" else c["impact"]
            conn.execute("UPDATE invest_disclosures SET detail = ?, impact = ? WHERE rcept_no = ?",
                         (json.dumps(detail, ensure_ascii=False), impact, r["rcept_no"]))
    conn.execute("INSERT INTO invest_disclosure_fetch (symbol, fetched_at) VALUES (?, ?) ON CONFLICT(symbol) DO UPDATE SET fetched_at = excluded.fetched_at",
                 (symbol, now.isoformat(timespec="seconds")))
    conn.commit()
    return {"ok": True, "saved": saved, "reason": ""}


def recent(conn: sqlite3.Connection, symbol: str, days: int = DAYS, today: date | None = None) -> list[dict]:
    since = ((today or date.today()) - timedelta(days=days)).isoformat()
    out = []
    for r in conn.execute("SELECT * FROM invest_disclosures WHERE symbol = ? AND rcept_dt >= ? ORDER BY rcept_dt DESC, rcept_no DESC",
                          (symbol, since)):
        c = next((x for x in RULES if x[0] == r["kind"]), None)
        out.append({"rcept_no": r["rcept_no"], "date": r["rcept_dt"], "title": r["report_nm"], "kind": r["kind"],
                    "label": c[2] if c else "기타", "impact": r["impact"], "why": c[4] if c else "", "filer": r["flr_nm"],
                    "correction": is_correction(r["report_nm"]), "detail": json.loads(r["detail"] or "{}"), "url": URL.format(r["rcept_no"])})
    return out


def summary(conn: sqlite3.Connection, symbol: str, *, shares: float | None = None, today: date | None = None) -> dict:
    """재료 요약 — 점수 · 표시할 공시 · 희석 · 자사주 · 풍문. shares(발행주식수)가 있으면 자사주 비율도."""
    today = today or date.today()
    items = recent(conn, symbol, DAYS, today)
    originals = {base_name(x["title"]) for x in items if not x["correction"]}
    counted = [x for x in items if not (x["correction"] and base_name(x["title"]) in originals)]
    corrections: dict[str, list[dict]] = {}
    for x in items:
        if x["correction"]:
            corrections.setdefault(base_name(x["title"]), []).append(x)
    raw = 0.0
    for x in counted:  # 정정이 있으면 더 무거운 영향을 따른다 (상세는 정정 공시에 붙는다)
        impact = min([x["impact"], *(y["impact"] for y in corrections.get(base_name(x["title"]), []))]) if not x["correction"] else x["impact"]
        age = (today - date.fromisoformat(x["date"])).days
        raw += impact * (1.0 if age <= RECENT_DAYS else 0.5)
    score = max(-SCORE_CLIP, min(SCORE_CLIP, round(raw, 1)))
    dil = [x for x in items if x["kind"] in ("rights", "cb", "bw")]
    decisions = {(x["kind"], x["detail"].get("new_shares"), round(x["detail"]["dilution"], 6)): x["detail"]["dilution"]
                 for x in dil if x["detail"].get("dilution") is not None}  # 같은 결정(원 · 정정)은 한 번만
    known = list(decisions.values())
    unknown = [] if known else [f"{x['date']} {x['label']}" for x in dil if not x["correction"]]
    buys = [x for x in counted if x["kind"] == "buyback"]
    buy_shares = sum(x["detail"].get("shares") or 0 for x in buys)
    flags = sorted({x["label"] for x in counted if x["impact"] <= -2 or x["kind"] in RUMOR_KINDS | {"control", "restructure"}})
    notable = [x for x in counted if x["kind"] and x["kind"] not in ("insider", "ir")]
    shown = (notable or counted)[:SHOWN]
    return {
        "days": DAYS, "count": len(items), "score": score,
        "score_note": f"공시 영향 합 (최근 {RECENT_DAYS}일 ×1, 그 전 ×0.5, ±{SCORE_CLIP}) — 가치 점수와 따로 본다",
        "flags": flags,
        "dilution": {"pct": round(sum(known), 4) if known else None, "unknown": unknown,
                     "note": f"최근 {DAYS}일 유상증자 · 전환사채 · 신주인수권부사채 결정의 합 (재무에 이미 들어갔을 수 있어 최대치)"
                             + (" · unknown 은 상세를 아직 못 받아 비율을 모르는 결정" if unknown else "")} if dil else None,
        "buyback": {"shares": buy_shares or None, "amount": sum(x["detail"].get("amount") or 0 for x in buys) or None,
                    "pct": round(buy_shares / shares, 4) if buy_shares and shares else None} if buys else None,
        "rumors": [{"date": x["date"], "title": x["title"], "url": x["url"]} for x in counted if x["kind"] in RUMOR_KINDS],
        "items": [{k: x[k] for k in ("date", "label", "impact", "title", "why", "url")} | ({"detail": x["detail"]} if x["detail"] else {})
                  for x in shown],
    }
