"""아침 브리핑 — 매일 아침(기본 08:30) 비서가 한 페이지 보고서와 아침 의견(오늘 볼 것)을 AI Trading 채팅방에 남긴다.

- 일정: 서비스(service.py)가 자동 운용 일정과 같이 확인한다(tick). 하루 한 번, 정한 시각부터 LATE_MIN 분 안에만 보낸다 — 서버가
  늦게 켜져도 오전 안이면 보내고, 그보다 늦으면 그날은 건너뛴다. 휴장일(토스 장 운영에 정규장이 없음)은 보내지 않는다.
- 내용: briefing.snapshot(보유 종목·현황 · 회의 요약 · 장내 이슈, 숫자는 모두 코드) + 비서 의견 — LLM(기능 invest_assistant)이 보고서
  글만 보고 2~4문장(막힌 것, 보유 종목에서 볼 것, 지난 회의 결론, 오늘 장내 이슈). LLM 이 실패하면 코드가 짧게 쓴다. 채팅방 기록은 보고서
  카드와 같다(event kind=report, morning).
- 설정(settings invest_morning_briefing): 켜기(기본 켜짐)·시각(05:00~12:00)·요일(기본 매일)·매월 날짜(비우면 날짜 제한 없음)·
  휴장일 건너뛰기(기본 예 — 매월 1일처럼 날짜를 정했다면 끄면 휴장일에도 보낸다). 주문과 상관없어 PIN 은 받지 않는다.
  채팅으로 비서에게 말해도 바뀐다(assistant.update_briefing).
"""

import asyncio
import json
import re
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import date, datetime

from app.invest.advice import assistant
from app.invest.ops import briefing
from app.invest.marketdata import market
from app.invest.marketdata.market import MarketError
from app.invest.broker.toss import TossError
from app.llm.client import LLMError, structured
from app.system import issues

S_CONFIG = "invest_morning_briefing"
S_LAST = "invest_morning_briefing_last"  # 마지막으로 보낸(또는 휴장으로 건너뛴) 날짜
DEFAULT_CONFIG = {"enabled": True, "time": "08:30", "weekdays": [0, 1, 2, 3, 4, 5, 6], "month_days": [], "skip_holidays": True}
WEEKDAY_KO = "월화수목금토일"
LATE_MIN = 180
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

SCHEMA = {"type": "object", "properties": {"comment": {"type": "string"}}, "required": ["comment"]}
SYSTEM = f"""당신은 Mallot 의 투자 비서 {assistant.PROFILE['name']}입니다. 매일 아침 관리자에게 브리핑을 보냅니다.
주어진 보고서 글만 근거로 아침 의견을 2~4문장 쓰세요 — 관리자가 오늘 계속 참고할 것만: [주의]가 있으면 먼저, 보유 종목에서 눈여겨볼
것(손익·비중·오늘 등락·그 종목 뉴스), 지난 회의 결론과 다음에 볼 것, 장내 이슈(시황·섹터·뉴스) 가운데 보유 종목에 닿는 것.
설정·일정·시스템·개발 이야기는 하지 말고(시스템 문제는 시스템 관리자가 맡습니다, 관리자에게 확인을 요청하지 마세요), 보고서에 없는 숫자는 쓰지 말고, 수익을 장담하지 마세요. 한국어, 짧고 친근하게, 첫 문장은 아침 인사로."""

CommentLlm = Callable[[str, str, str, dict, str | None], Awaitable[dict]]


async def _default_llm(feature: str, system: str, prompt: str, schema: dict, model: str | None) -> dict:
    return await structured(feature, prompt, schema, model=model, system_prompt=system)


LLM: CommentLlm = _default_llm  # 테스트에서 가짜로 바꾼다


class MorningError(Exception):
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


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def get_config(conn: sqlite3.Connection) -> dict:
    raw = _get(conn, S_CONFIG, {})
    out = dict(DEFAULT_CONFIG)
    if isinstance(raw, dict):
        out["enabled"] = bool(raw.get("enabled", out["enabled"]))
        if isinstance(raw.get("time"), str) and _HHMM.match(raw["time"]):
            out["time"] = raw["time"]
        days = [d for d in raw.get("weekdays") or [] if isinstance(d, int) and 0 <= d <= 6]
        out["weekdays"] = sorted(set(days)) or out["weekdays"]
        out["month_days"] = sorted({d for d in raw.get("month_days") or [] if isinstance(d, int) and 1 <= d <= 31})
        out["skip_holidays"] = bool(raw.get("skip_holidays", True))
    return {**out, "last": _get(conn, S_LAST, None), "summary": describe(out)}


def describe(cfg: dict) -> str:
    """'매일 08:30' · '평일 08:30' · '매주 월·금 08:30' · '매월 1·15일 08:30' (+ 휴장일에도)."""
    days = cfg["weekdays"]
    when = (f"매월 {'·'.join(map(str, cfg['month_days']))}일" + ("" if len(days) == 7 else f"({'·'.join(WEEKDAY_KO[d] for d in days)}요일만)")
            if cfg["month_days"] else "매일" if len(days) == 7 else "평일" if days == [0, 1, 2, 3, 4]
            else f"매주 {'·'.join(WEEKDAY_KO[d] for d in days)}")
    return f"{when} {cfg['time']}" + ("" if cfg["skip_holidays"] else " · 휴장일에도")


def due_on(cfg: dict, day: date) -> bool:
    """그 날짜가 보고 날인지 (요일 · 매월 날짜)."""
    return day.weekday() in cfg["weekdays"] and (not cfg["month_days"] or day.day in cfg["month_days"])


def save_config(conn: sqlite3.Connection, *, enabled: bool | None = None, time: str | None = None,
                weekdays: list[int] | None = None, month_days: list[int] | None = None,
                skip_holidays: bool | None = None) -> dict:
    cfg = {k: v for k, v in get_config(conn).items() if k not in ("last", "summary")}
    if time is not None:
        if not _HHMM.match(time) or not 5 * 60 <= _minutes(time) <= 12 * 60:
            raise MorningError("아침 브리핑 시각은 05:00~12:00 사이 HH:MM 으로 적어 주세요")
        cfg["time"] = time
    if weekdays is not None:
        if not weekdays or any(not isinstance(d, int) or not 0 <= d <= 6 for d in weekdays):
            raise MorningError("요일은 0(월)~6(일) 중 하나 이상이에요")
        cfg["weekdays"] = sorted(set(weekdays))
    if month_days is not None:
        if any(not isinstance(d, int) or not 1 <= d <= 31 for d in month_days):
            raise MorningError("매월 날짜는 1~31 이에요 (비우면 날짜 제한 없음)")
        cfg["month_days"] = sorted(set(month_days))
    if skip_holidays is not None:
        cfg["skip_holidays"] = skip_holidays
    if enabled is not None:
        cfg["enabled"] = enabled
    _put(conn, S_CONFIG, cfg)
    return get_config(conn)


def _fallback(r: dict) -> str:
    """LLM 없이 — 막힌 것, 총 자산·수익률, 지난 회의 한 줄, 시황 한 줄."""
    parts = ["좋은 아침이에요."]
    parts += [f"먼저 확인할 것: {w}." for w in r.get("warnings") or []]
    pf, bl = r["performance"]["portfolio"], r["performance"].get("baseline")
    if pf:
        rate = (f", 운용 수익률 {briefing.pct(bl['return_rate'])}" if bl and bl.get("return_rate") is not None
                else f", 평가 손익 {briefing.pct(pf['return_rate'])}" if pf["return_rate"] is not None else "")
        parts.append(f"총 자산 {briefing.won(pf['total_assets_krw'])}{rate}이에요.")
    m = next((m for m in r["meetings"] if m.get("headline")), None)
    if m:
        parts.append(f"지난 회의({m['at'][5:]}): {m['headline']}.")
    v = r["market"].get("view")
    if v and v.get("headline"):
        parts.append(f"시황: {v['headline']}.")
    return " ".join(parts)


def send(conn: sqlite3.Connection, now: datetime | None = None, provider=None, model: str | None = None) -> dict:
    """지금 아침 브리핑을 만들어 채팅방에 남긴다. → 남긴 메시지"""
    now = now or market.now_kst()
    data = briefing.snapshot(conn, provider, now)
    data["title"] = "아침 브리핑"
    try:
        raw = asyncio.run(LLM("invest_assistant", SYSTEM, briefing.to_text(data), SCHEMA, model))
        comment = issues.strip_notes(str((raw or {}).get("comment") or ""))[:600]
    except (LLMError, TypeError, ValueError):
        comment = ""
    data["comment"] = comment or _fallback(data)
    msg = assistant.add(conn, "event", "아침 브리핑이 왔어요", kind="report", morning=True, report=data, body=briefing.to_text(data),
                        by="assistant" if comment else "code")
    _put(conn, S_LAST, now.date().isoformat())
    return msg


def tick(conn: sqlite3.Connection, now: datetime | None = None, provider=None) -> dict | None:
    """정한 시각이 지났고 오늘 아직 안 보냈으면 보낸다(휴장일은 건너뛴 날로 남김). 할 일이 없으면 None."""
    cfg = get_config(conn)
    if not cfg["enabled"]:
        return None
    now = now or market.now_kst()
    today = now.date().isoformat()
    minute = now.hour * 60 + now.minute
    start = _minutes(cfg["time"])
    if cfg["last"] == today or not due_on(cfg, now.date()) or not start <= minute <= start + LATE_MIN:
        return None
    p = provider or market.provider()
    closed = False
    if cfg["skip_holidays"]:
        try:
            closed = not p.session().get("start")
        except (MarketError, TossError):
            closed = False  # 장 운영을 모르면 보낸다 — 브리핑이 그 사실도 알려 준다
    if closed:
        _put(conn, S_LAST, today)
        return {"skipped": "휴장일"}
    return send(conn, now, p)
