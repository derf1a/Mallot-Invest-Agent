"""투자 설정 — API 키(.env), 주문 한도, 에이전트가 정하는 비중 한도(종목당 · 섹터별), 에이전트 주문 승인 생략, 전체 중지, 주문 PIN.

모의 투자는 없다 — 주문은 언제나 토스증권 실계좌로 나간다. 키는 .env 로만 관리하고(설정 화면에서 쓰면 .env 를 고친다),
나머지는 settings 테이블(JSON)에 둔다. 주문 PIN(MIA_TRADING_PIN)은 화면에서 바꿀 수 없는 환경 변수다 — 로그인 없는 앱이라
같은 네트워크의 다른 기기가 API 로 주문하지 못하게 하려는 것. PIN 을 바꾸려면 Mac 에서 .env 를 직접 고친다.
"""

import hmac
import json
import math
import os
import re
import sqlite3
import threading
import time
from datetime import datetime

from app.core import envfile

# 설정 화면에서 쓸 수 있는 환경 변수 (이 목록 밖은 화면에서 못 바꾼다)
KEYS: dict[str, dict] = {
    "TOSS_CLIENT_ID": {"label": "클라이언트 ID", "secret": False},
    "TOSS_CLIENT_SECRET": {"label": "클라이언트 시크릿", "secret": True},
    "TOSS_ACCOUNT_SEQ": {"label": "계좌 순번(accountSeq)", "secret": False},
    "DART_API_KEY": {"label": "OpenDART 인증키 (가치평가 재무)", "secret": True},
}
PIN_ENV = "MIA_TRADING_PIN"   # 주문·승인·에이전트 실행·설정 변경에 필요한 PIN (화면에서 못 바꿈)
PIN_MAX_FAILS = 5
PIN_LOCK_SECONDS = 300

DEFAULT_LIMITS = {"max_order_krw": 1_000_000, "daily_max_krw": 3_000_000, "max_position_pct": 30.0}

S_LIMITS = "invest_limits"
S_RISK = "invest_risk_limits"                 # 에이전트(리스크 · 포트폴리오 매니저)가 스스로 정하는 비중 한도
S_RISK_HISTORY = "invest_risk_limits_history"
RISK_HISTORY = 30
# 에이전트가 고를 수 있는 범위 — 코드가 지키는 바깥 울타리 (한 종목에 절반 넘게는 못 싣는다)
POSITION_RANGE = (5.0, 50.0)
SECTOR_RANGE = (10.0, 100.0)
DEFAULT_SECTOR_PCT = 60.0                      # 에이전트가 아직 정하지 않았을 때 섹터 하나의 최대 비중
EQUITY_TARGET_PCT = 100.0                      # 주식 비중 목표 — 고정. 예수금 · 채권 · 현금성 상품에 돈을 두지 않는다
# 채권 · 현금성 상품 — 주식 비중 100% 원칙이라 사지 않는다 (종목 이름으로 가린다)
NON_EQUITY_WORDS = ("채권", "국채", "국고채", "회사채", "통안채", "단기채", "금리", "머니마켓", "MMF", "KOFR", "SOFR",
                    "파킹", "달러선물", "TDF", "TRF")
S_AGENT_AUTO = "invest_agent_auto_approve"  # 에이전트(트레이더) 주문을 사용자 승인 없이 바로 낼지 — 기본 꺼짐(승인 필요)
S_HALTED = "invest_halted"                  # 전체 주문 중지
S_AGENT_OPTIONS = "invest_agent_options"    # 에이전트 토론 설정 (실험실): 라운드·모델·후보 종목 수
DEFAULT_AGENT_OPTIONS = {"rounds": 2, "model": "", "max_candidates": 6}
_MODEL_RE = re.compile(r"^(claude-[a-z0-9.\-]{2,60}|local|mlx|vllm(:[A-Za-z0-9._/\-]{1,120})?)?$")  # 비우면 LLM 경로 설정, local·mlx = 로컬, vllm[:모델]


class ConfigError(Exception):
    pass


class PinError(Exception):
    """PIN 이 없거나 틀림·설정 안 됨 (403) / 잠김 (429)."""

    def __init__(self, message: str, code: str, status: int = 403):
        super().__init__(message)
        self.code = code
        self.status = status


_pin_lock = threading.Lock()
_pin_fails = {"count": 0, "until": 0.0}


def pin_configured() -> bool:
    return len(os.environ.get(PIN_ENV, "").strip()) >= 4


def check_pin(provided: str | None, *, required: bool = True) -> None:
    """X-Trading-Pin 확인. 5번 틀리면 5분 잠금.

    required=True(주문·승인·에이전트 실행): .env 에 PIN 이 없으면 거절. required=False(키·한도·명단 변경): PIN 이
    설정돼 있을 때만 확인 — 처음 키를 넣는 단계는 PIN 없이도 되지만, PIN 이 없으면 어차피 주문은 못 낸다.
    """
    if not pin_configured():
        if required:
            raise PinError(f".env 에 {PIN_ENV}(4자리 이상)가 없어 주문할 수 없습니다 — Mac 에서 직접 추가하세요",
                           "pin_not_configured")
        return
    with _pin_lock:
        now = time.monotonic()
        if _pin_fails["until"] > now:
            raise PinError(f"PIN 을 여러 번 틀려 잠겼습니다 — {int(_pin_fails['until'] - now) + 1}초 뒤 다시 시도하세요",
                           "pin_locked", 429)
        if not provided:
            raise PinError("주문 PIN 이 필요합니다", "pin_required")
        if not hmac.compare_digest(provided.strip().encode(), os.environ.get(PIN_ENV, "").strip().encode()):
            _pin_fails["count"] += 1
            if _pin_fails["count"] >= PIN_MAX_FAILS:
                _pin_fails.update(count=0, until=now + PIN_LOCK_SECONDS)
            raise PinError("주문 PIN 이 틀렸습니다", "pin_invalid")
        _pin_fails.update(count=0, until=0.0)


def reset_pin_failures() -> None:
    with _pin_lock:
        _pin_fails.update(count=0, until=0.0)


def _get(conn: sqlite3.Connection, key: str, default):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return default


def _put(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, json.dumps(value, ensure_ascii=False)))


def toss_configured() -> bool:
    return bool(os.environ.get("TOSS_CLIENT_ID") and os.environ.get("TOSS_CLIENT_SECRET"))


def trading_blockers() -> list[str]:
    """주문을 낼 수 없는 이유 (없으면 주문 가능)."""
    reasons = []
    if not toss_configured():
        reasons.append("토스증권 API 키가 없습니다 (설정 > 투자)")
    if not pin_configured():
        reasons.append(f".env 에 {PIN_ENV}(4자리 이상, 주문 확인용)가 없습니다 (Mac 에서 직접 추가)")
    return reasons


def limits(conn: sqlite3.Connection) -> dict:
    raw = _get(conn, S_LIMITS, {})
    out = dict(DEFAULT_LIMITS)
    if isinstance(raw, dict):
        for k in out:
            try:
                v = float(raw.get(k, out[k]))
                if math.isfinite(v) and v > 0:
                    out[k] = v
            except (TypeError, ValueError):
                pass
    return out


def _pct(value: object, lo: float, hi: float, what: str) -> float:
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ConfigError(f"{what}은(는) 숫자(%)입니다") from None
    if not math.isfinite(v) or not lo <= v <= hi:
        raise ConfigError(f"{what}은(는) {lo:g}~{hi:g}% 사이입니다")
    return round(v, 1)


def _risk_raw(conn: sqlite3.Connection) -> dict:
    raw = _get(conn, S_RISK, {})
    return raw if isinstance(raw, dict) else {}


def risk_limits(conn: sqlite3.Connection) -> dict:
    """지금 쓰는 비중 한도 — 종목당(에이전트가 정했으면 그 값, 아니면 설정 화면의 max_position_pct) · 섹터 기본 · 섹터별.

    주문 검사(trading.check_order)와 에이전트 도구가 같이 쓴다. 범위를 벗어난 저장값은 무시한다."""
    raw = _risk_raw(conn)
    admin = limits(conn)["max_position_pct"]
    out = {"max_position_pct": admin, "position_set_by": "관리자 설정", "default_sector_pct": DEFAULT_SECTOR_PCT,
           "sectors": {}, "equity_target_pct": EQUITY_TARGET_PCT, "updated_at": raw.get("updated_at"),
           "updated_by": raw.get("updated_by"), "reason": raw.get("reason") or "",
           "ranges": {"max_position_pct": list(POSITION_RANGE), "sector_pct": list(SECTOR_RANGE)}}
    try:
        if raw.get("max_position_pct") is not None:
            out["max_position_pct"] = _pct(raw["max_position_pct"], *POSITION_RANGE, "종목당 한도")
            out["position_set_by"] = raw.get("updated_by") or "에이전트"
    except ConfigError:
        pass
    try:
        if raw.get("default_sector_pct") is not None:
            out["default_sector_pct"] = _pct(raw["default_sector_pct"], *SECTOR_RANGE, "섹터 기본 한도")
    except ConfigError:
        pass
    for name, v in (raw.get("sectors") or {}).items() if isinstance(raw.get("sectors"), dict) else []:
        try:
            out["sectors"][str(name)] = _pct(v, *SECTOR_RANGE, "섹터 한도")
        except ConfigError:
            pass
    return out


def sector_limit(lim: dict, sector: str | None) -> float:
    return lim["sectors"].get(sector or "", lim["default_sector_pct"])


def risk_history(conn: sqlite3.Connection, limit: int = RISK_HISTORY) -> list[dict]:
    raw = _get(conn, S_RISK_HISTORY, [])
    return [h for h in raw if isinstance(h, dict)][-limit:] if isinstance(raw, list) else []


def save_risk_limits(conn: sqlite3.Connection, *, by: str, reason: str, max_position_pct: object = None,
                     default_sector_pct: object = None, sectors: dict | None = None, at: str = "") -> dict:
    """에이전트가 비중 한도를 고친다 → {limits, changes}. 하나라도 범위를 벗어나면 아무것도 바꾸지 않는다.
    sectors 의 값이 None 이면 그 섹터 한도를 지운다(섹터 기본 한도로 돌아감). 이유는 꼭 남긴다."""
    reason = (reason or "").strip()
    at = at or datetime.now().strftime("%Y-%m-%d %H:%M")
    if len(reason) < 5:
        raise ConfigError("한도를 바꾸는 이유(reason)를 적어 주세요")
    before = risk_limits(conn)
    raw = _risk_raw(conn)
    nxt = {"max_position_pct": raw.get("max_position_pct"), "default_sector_pct": raw.get("default_sector_pct"),
           "sectors": dict(raw.get("sectors") or {}) if isinstance(raw.get("sectors"), dict) else {}}
    if max_position_pct is not None:
        nxt["max_position_pct"] = _pct(max_position_pct, *POSITION_RANGE, "종목당 한도")
    if default_sector_pct is not None:
        nxt["default_sector_pct"] = _pct(default_sector_pct, *SECTOR_RANGE, "섹터 기본 한도")
    for name, v in (sectors or {}).items():
        name = str(name).strip()[:30]
        if not name:
            continue
        if v is None:
            nxt["sectors"].pop(name, None)
        else:
            nxt["sectors"][name] = _pct(v, *SECTOR_RANGE, f"섹터 한도({name})")
    if len(nxt["sectors"]) > 40:
        raise ConfigError("섹터별 한도는 40개까지입니다")
    _put(conn, S_RISK, {**nxt, "updated_at": at, "updated_by": by, "reason": reason[:300]})
    after = risk_limits(conn)
    changes = []
    if before["max_position_pct"] != after["max_position_pct"]:
        changes.append({"what": "종목당 한도", "before": before["max_position_pct"], "after": after["max_position_pct"]})
    if before["default_sector_pct"] != after["default_sector_pct"]:
        changes.append({"what": "섹터 기본 한도", "before": before["default_sector_pct"], "after": after["default_sector_pct"]})
    for name in sorted(set(before["sectors"]) | set(after["sectors"])):
        b, a = before["sectors"].get(name), after["sectors"].get(name)
        if b != a:
            changes.append({"what": f"섹터 {name}", "before": b, "after": a})
    if not changes:
        conn.rollback()
        return {"limits": before, "changes": []}
    _put(conn, S_RISK_HISTORY, (risk_history(conn) + [{"at": at, "by": by, "reason": reason[:300], "changes": changes}])[-RISK_HISTORY:])
    conn.commit()
    return {"limits": risk_limits(conn), "changes": changes}


def non_equity(name: str, security_type: str = "") -> bool:
    """채권 · 현금성 상품인가 (ETF · ETN 이름으로) — 주식 비중 100% 원칙이라 매수하지 않는다. 일반 주식은 아니다."""
    if (security_type or "").upper() == "STOCK":
        return False
    text = (name or "").upper()
    return any(w.upper() in text for w in NON_EQUITY_WORDS)


def halted(conn: sqlite3.Connection) -> bool:
    return bool(_get(conn, S_HALTED, False))


def agent_auto_approve(conn: sqlite3.Connection) -> bool:
    return bool(_get(conn, S_AGENT_AUTO, False))


def agent_options(conn: sqlite3.Connection) -> dict:
    raw = _get(conn, S_AGENT_OPTIONS, {})
    out = dict(DEFAULT_AGENT_OPTIONS)
    if isinstance(raw, dict):
        try:
            out["rounds"] = max(1, min(int(raw.get("rounds", out["rounds"])), 3))
            out["max_candidates"] = max(2, min(int(raw.get("max_candidates", out["max_candidates"])), 10))
        except (TypeError, ValueError):
            pass
        model = str(raw.get("model") or "")
        out["model"] = model if _MODEL_RE.match(model) else ""
    return out


def save_agent_options(conn: sqlite3.Connection, opts: dict) -> dict:
    merged = {**agent_options(conn), **{k: v for k, v in opts.items() if k in DEFAULT_AGENT_OPTIONS and v is not None}}
    try:
        rounds, cands = int(merged["rounds"]), int(merged["max_candidates"])
    except (TypeError, ValueError):
        raise ConfigError("라운드·후보 수는 숫자입니다") from None
    if not 1 <= rounds <= 3:
        raise ConfigError("라운드는 1~3 입니다")
    if not 2 <= cands <= 10:
        raise ConfigError("후보 종목 수는 2~10 입니다")
    if not _MODEL_RE.match(str(merged["model"] or "")):
        raise ConfigError("모델 이름이 올바르지 않습니다 (claude-…, local 또는 비워 두기)")
    _put(conn, S_AGENT_OPTIONS, {"rounds": rounds, "model": str(merged["model"] or ""), "max_candidates": cands})
    conn.commit()
    return agent_options(conn)


def keys_status() -> list[dict]:
    file_values = envfile.read()
    out = []
    for key, meta in KEYS.items():
        value = os.environ.get(key)
        out.append({"key": key, "label": meta["label"], "secret": meta["secret"], "set": bool(value),
                    "masked": envfile.mask(value, meta["secret"]),
                    "source": "file" if key in file_values else ("env" if value else None)})
    return out


def snapshot(conn: sqlite3.Connection) -> dict:
    return {
        "keys": keys_status(),
        "env_file": str(envfile.env_path()),
        "toss_configured": toss_configured(),
        "pin_env_name": PIN_ENV,
        "pin_configured": pin_configured(),
        "trading_blockers": trading_blockers(),
        "limits": limits(conn),
        "risk_limits": risk_limits(conn),
        "agent_auto_approve": agent_auto_approve(conn),
        "halted": halted(conn),
    }


def save_keys(values: dict[str, str | None]) -> None:
    unknown = set(values) - set(KEYS)
    if unknown:
        raise ConfigError(f"화면에서 바꿀 수 없는 키입니다: {', '.join(sorted(unknown))}")
    seq = values.get("TOSS_ACCOUNT_SEQ")
    if seq:
        # 순번(accountSeq) 대신 계좌번호를 넣어도 된다 — 토스 계좌 목록과 맞춰 순번으로 바꿔 쓴다 (toss.match_account)
        digits = seq.replace("-", "").replace(" ", "")
        if not digits.isdigit():
            raise ConfigError("계좌 순번(accountSeq) 또는 계좌번호는 숫자입니다")
        values = {**values, "TOSS_ACCOUNT_SEQ": digits}
    try:
        envfile.update(values)
    except ValueError as e:
        raise ConfigError(str(e)) from None


def save_trading(conn: sqlite3.Connection, *, limits_: dict | None = None, agent_auto: bool | None = None,
                 halt: bool | None = None) -> None:
    if limits_ is not None:
        merged = limits(conn)
        for k in DEFAULT_LIMITS:
            if k in limits_ and limits_[k] is not None:
                v = float(limits_[k])
                if not math.isfinite(v) or v <= 0:
                    raise ConfigError("한도는 0보다 커야 합니다")
                if k == "max_position_pct" and v > 100:
                    raise ConfigError("종목 비중 한도는 100% 이하입니다")
                merged[k] = v
        _put(conn, S_LIMITS, merged)
    if agent_auto is not None:
        _put(conn, S_AGENT_AUTO, bool(agent_auto))
    if halt is not None:
        _put(conn, S_HALTED, bool(halt))
    conn.commit()
