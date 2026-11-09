"""운용 목표(투자 정책) — 관리자가 자문 에이전트와 대화로 정하고 비서가 반영하는, 모든 에이전트의 판단 기준.

- 항목(FIELDS): 목표 방식·목표 수익률·벤치마크·투자 기간·매매 빈도·위험 성향·손실 허용 한도·주식 비중 목표·종목당 최대 비중·선호/피할
  섹터·메모. 값은 정해진 형식으로만(clean) — 고르는 항목은 키나 한국어 이름·낱말로, 숫자는 글에서 꺼낸다(parse: '연 10%' → 10).
- 저장(settings invest_policy) + 바뀐 기록(invest_policy_history, 최근 HISTORY 개: 언제·누가·왜·항목별 전 → 후).
- 쓰임: 토론 에이전트·트레이더·포트폴리오 매니저(회의 후보)·리서치 데스크·일정 담당·비서 프롬프트에 '운용 목표' 글(text)로,
  보고서에 목표 대비 지금(주식 비중·수익률·벤치마크).
- 주문 한도(건당·하루·종목 비중)는 코드 한도(config.limits)가 그대로 — 운용 목표의 종목당 최대 비중은 그보다 낮을 때만 의미가 있다.
"""

import json
import re
import sqlite3
from datetime import datetime

from app.invest import market

S_POLICY = "invest_policy"
S_HISTORY = "invest_policy_history"
HISTORY = 30

FIELDS: dict[str, dict] = {
    "objective": {"label": "목표 방식", "type": "enum", "choices": {
        "absolute": "절대 수익 — 시장과 상관없이 플러스", "relative": "벤치마크 초과 — 지수보다 잘하기",
        "growth": "장기 성장 — 오래 두고 크게 불리기", "income": "배당·현금 흐름", "preserve": "원금 보존 우선"}},
    "target_return": {"label": "목표 수익률", "type": "number", "unit": "% (1년)", "min": 0, "max": 100},
    "benchmark": {"label": "벤치마크", "type": "enum", "choices": {"none": "없음", "KOSPI": "코스피", "KOSDAQ": "코스닥"}},
    "horizon": {"label": "투자 기간", "type": "enum", "choices": {
        "short": "단기 — 몇 주~3개월", "mid": "중기 — 3개월~1년", "long": "장기 — 1년 이상"}},
    "turnover": {"label": "매매 빈도", "type": "enum", "choices": {
        "low": "가끔 — 주 1회 이하", "mid": "보통 — 하루 한두 번 점검", "high": "자주 — 하루 여러 번"}},
    "risk": {"label": "위험 성향", "type": "enum", "choices": {"conservative": "보수적", "balanced": "중립", "aggressive": "공격적"}},
    "max_drawdown": {"label": "손실 허용 한도", "type": "number", "unit": "% (고점 대비)", "min": 1, "max": 90},
    "equity_target": {"label": "주식 비중 목표", "type": "number", "unit": "%", "min": 0, "max": 100},
    "max_position": {"label": "종목당 최대 비중", "type": "number", "unit": "%", "min": 1, "max": 100},
    "prefer": {"label": "선호 섹터·테마", "type": "list"},
    "avoid": {"label": "피할 섹터·종목", "type": "list"},
    "notes": {"label": "기타 메모", "type": "text"},
}
REQUIRED = ["objective", "target_return", "benchmark", "horizon", "turnover", "risk", "max_drawdown", "equity_target"]
# 한국어로 답해도 고르는 항목을 알아듣게 — 키마다 더 받는 낱말
SYNONYMS: dict[str, dict[str, tuple[str, ...]]] = {
    "objective": {"absolute": ("절대",), "relative": ("벤치마크", "초과", "지수보다", "상대"), "growth": ("성장", "불리"),
                  "income": ("배당", "현금흐름", "현금 흐름", "인컴"), "preserve": ("보존", "안전", "잃지")},
    "benchmark": {"none": ("없음", "없어", "필요없", "안 써", "안써"), "KOSPI": ("코스피", "kospi"), "KOSDAQ": ("코스닥", "kosdaq")},
    "horizon": {"short": ("단기", "단투", "몇 주", "짧게"), "mid": ("중기", "1년 안", "몇 달"), "long": ("장기", "장투", "1년 이상", "오래")},
    "turnover": {"low": ("가끔", "주 1회", "거의 안", "드물"), "mid": ("보통", "하루 한", "하루 1"), "high": ("자주", "여러 번", "적극적으로 매매")},
    "risk": {"conservative": ("보수", "안정", "조심"), "balanced": ("중립", "중간", "균형"), "aggressive": ("공격", "적극", "과감")},
}


class PolicyError(Exception):
    pass


def _get(conn: sqlite3.Connection, key: str, default):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    try:
        return json.loads(row["value"]) if row else default
    except ValueError:
        return default


def _put(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, json.dumps(value, ensure_ascii=False)))
    conn.commit()


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"-?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(m.group()) if m else None


def clean(field: str, value: object) -> object:
    """그 항목의 값으로 바꾼다 (형식이 틀리면 PolicyError). None·'' 이면 지운다(None)."""
    spec = FIELDS.get(field)
    if spec is None:
        raise PolicyError(f"없는 항목이에요: {field} — {', '.join(FIELDS)}")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    kind = spec["type"]
    if kind == "enum":
        text = str(value).strip()
        if text in spec["choices"]:
            return text
        low = text.lower()
        for key, label in spec["choices"].items():
            if low == key.lower() or label.split(" —")[0] in text or any(w in low for w in SYNONYMS.get(field, {}).get(key, ())):
                return key
        raise PolicyError(f"{spec['label']}은(는) 다음 중 하나예요: " + ", ".join(f"{k}({v})" for k, v in spec["choices"].items()))
    if kind == "number":
        n = _number(value)
        if n is None or not spec["min"] <= n <= spec["max"]:
            raise PolicyError(f"{spec['label']}은(는) {spec['min']}~{spec['max']} 사이 숫자예요 ({spec['unit']})")
        return round(n, 1)
    if kind == "list":
        items = value if isinstance(value, list) else re.split(r"[,·/\n]|\s및\s", str(value))
        out = [str(x).strip()[:30] for x in items if str(x).strip()]
        return list(dict.fromkeys(out))[:8] or None
    return str(value).strip()[:300]


def parse(field: str, text: str) -> object | None:
    """관리자 답 글에서 그 항목 값을 찾는다 (자문 에이전트가 LLM 없이 받을 때). 못 찾으면 None."""
    try:
        return clean(field, text)
    except PolicyError:
        return None


def get(conn: sqlite3.Connection) -> dict:
    raw = _get(conn, S_POLICY, {})
    out = {}
    for k in FIELDS:
        try:
            v = clean(k, raw.get(k)) if isinstance(raw, dict) else None
        except PolicyError:
            v = None
        if v is not None:
            out[k] = v
    if isinstance(raw, dict) and raw.get("updated_at"):
        out["updated_at"] = raw["updated_at"]
        out["updated_by"] = raw.get("updated_by")
    return out


def history(conn: sqlite3.Connection, limit: int = HISTORY) -> list[dict]:
    raw = _get(conn, S_HISTORY, [])
    return [h for h in raw if isinstance(h, dict)][-limit:] if isinstance(raw, list) else []


def show(field: str, value: object) -> str:
    """화면·프롬프트용 값 글."""
    if value is None:
        return "없음"
    spec = FIELDS[field]
    if spec["type"] == "enum":
        return spec["choices"].get(str(value), str(value)).split(" —")[0]
    if spec["type"] == "number":
        unit = "%" if field != "target_return" else "% (1년)"
        return f"{value:g}{unit}"
    if spec["type"] == "list":
        return ", ".join(value)
    return str(value)


def save(conn: sqlite3.Connection, changes: dict, *, by: str, reason: str = "", now: datetime | None = None) -> dict:
    """항목들을 바꾼다 → {policy, diff: [{field, label, before, after}]}. 하나라도 형식이 틀리면 아무것도 바꾸지 않는다."""
    if not isinstance(changes, dict) or not changes:
        raise PolicyError("바꿀 항목이 없어요")
    cur = get(conn)
    nxt = {k: v for k, v in cur.items() if k in FIELDS}
    for field, value in changes.items():
        v = clean(field, value)
        if v is None:
            nxt.pop(field, None)
        else:
            nxt[field] = v
    diff = [{"field": k, "label": FIELDS[k]["label"], "before": show(k, cur.get(k)), "after": show(k, nxt.get(k))}
            for k in FIELDS if cur.get(k) != nxt.get(k)]
    if not diff:
        return {"policy": cur, "diff": []}
    now = now or market.now_kst()
    at = now.strftime("%Y-%m-%d %H:%M")
    _put(conn, S_POLICY, {**nxt, "updated_at": at, "updated_by": by})
    _put(conn, S_HISTORY, (history(conn) + [{"at": at, "by": by, "reason": reason.strip()[:300], "changes": diff}])[-HISTORY:])
    return {"policy": get(conn), "diff": diff}


def missing(p: dict) -> list[str]:
    return [k for k in REQUIRED if p.get(k) is None]


def text(p: dict) -> str:
    """프롬프트용 몇 줄 — 정한 항목만 ('' 이면 아직 없음)."""
    lines = [f"- {FIELDS[k]['label']}: {show(k, p[k])}" for k in FIELDS if p.get(k) is not None]
    if not lines:
        return ""
    if p.get("risk") and p.get("max_drawdown") is not None:
        lines.append(f"- 해석: 손실이 고점 대비 {p['max_drawdown']:g}%에 가까워지면 신규 매수를 줄이고 위험을 낮추세요.")
    return "\n".join(lines)


def view(conn: sqlite3.Connection) -> dict:
    """화면 — 항목(이름·값·고를 수 있는 값), 빠진 필수 항목, 최근 바뀐 기록."""
    p = get(conn)
    return {"policy": p, "missing": missing(p), "history": history(conn, 5)[::-1],
            "fields": [{"key": k, "label": s["label"], "type": s["type"], "unit": s.get("unit"), "required": k in REQUIRED,
                        "choices": s.get("choices"), "value": p.get(k), "text": show(k, p.get(k)) if p.get(k) is not None else None}
                       for k, s in FIELDS.items()],
            "updated_at": p.get("updated_at"), "updated_by": p.get("updated_by")}
