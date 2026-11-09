"""투자 설정 — API 키(.env), 주문 한도, 에이전트 주문 승인 생략, 전체 중지, 주문 PIN.

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
S_AGENT_AUTO = "invest_agent_auto_approve"  # 에이전트(트레이더) 주문을 사용자 승인 없이 바로 낼지 — 기본 꺼짐(승인 필요)
S_HALTED = "invest_halted"                  # 전체 주문 중지
S_AGENT_OPTIONS = "invest_agent_options"    # 에이전트 토론 설정 (실험실): 라운드·모델·후보 종목 수
DEFAULT_AGENT_OPTIONS = {"rounds": 2, "model": "", "max_candidates": 6}
_MODEL_RE = re.compile(r"^(claude-[a-z0-9.\-]{2,60}|local)?$")  # 비우면 설정 > LLM 의 기본 LLM, local = 로컬 LLM


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
