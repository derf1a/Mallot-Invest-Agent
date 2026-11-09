"""자동 운용 — 보유 종목과 사용자가 채팅으로 남긴 운용 지시를 바탕으로, 매일 정해진 시각에 에이전트 토론을 스스로 돌리고
트레이더 주문을 승인 없이 낸다(사용자가 PIN 으로 켰을 때만). 판단 시각은 AI 가 더하거나 옮길 수 있다.

- 일정: 고정 시각(기본 09:10 장 시작 · 12:30 장 중 · 15:00 마감 전) + AI 가 더한 시각(하루 max_extra 번) + AI 가 옮긴 고정 시각.
  토스 장 운영(휴장일·정규장 시각) 안에서만 — 정규장 시작 OPEN_AFTER_MIN 분 뒤부터 끝나기 CLOSE_BEFORE_MIN 분 전까지(동시호가 전).
- 실행: 서비스(service.py)가 20초마다 tick → 때가 된 일정을 실험실 agent_action 실행(scope=autopilot)으로 시작한다. 그 세션은
  후보 = 운용 지시에 나온 종목 → 보유 → 운용 지시의 섹터(scopes.py), 운용 지시는 관리자 메시지로 들어가고, 게이트는 supermajority
  (과반 + 반대 극소수), 주문은 auto_submit. GRACE_MIN 분 넘게 늦은 일정(서버가 꺼져 있었음)은 '놓침'으로 남기고 건너뛴다.
  다른 토론이 진행 중이면 다음 확인 때 다시 본다.
- 안전: 켜기·시각 바꾸기·운용 지시·지금 실행은 주문 PIN, 끄기는 PIN 없이(더 안전해지는 쪽). 전체 주문 중지나 주문 조건(토스 키·PIN)이
  안 맞으면 건너뛰고 기록한다. 주문은 trading.place_order 가 한도(건당·하루·종목 비중)·잔고를 그대로 다시 검사한다.
- 다음 판단 시각: 세션이 끝나면 작은 LLM 호출(plan_next)이 오늘 일정·장 시각·게이트·결정·운용 지시를 보고 하나를 더하거나(add)
  다음 고정 시각을 옮기거나(move) 그대로 둔다(none). 코드가 장 시간·LEAD_MIN 분 뒤부터·다른 판단과 MIN_GAP_MIN 분 간격·
  하루 추가 횟수를 검사한다(apply_proposal).
- 필요할 때만 회의(flexible, 기본 켜짐): tick 이 받은 agenda(운용 데스크 desk.agenda)가 새 안건이 없다고 하면 그 시각은 건너뛰고,
  리서치가 돌고 있으면 기다린다. 수시 회의(run_adhoc, kind=risk): 리스크 감시가 급변을 찾으면 그 종목으로 바로 — 하루 MAX_ADHOC 번.
- 운용 방식(mode): continuous(상시 운용, 기본) — 정기 회의 없이 자동 판단 시간 동안 상시 운용 채널(stream.py, agent_action
  params.stream) 하나가 돌며 에이전트들이 계속 이야기하고 트레이더가 언제든 주문한다. tick 은 채널이 없으면 열고(서버 재시작으로
  끊기거나 꺼졌다 다시 켜지면 다시 — 하루 MAX_STREAMS 번, 사용자가 중지하면 그날은 다시 열지 않음), 지금 판단·비서 지시는 그
  채널에 관리자 말로 넣는다. meetings(정기 회의) — 아래 고정 시각·AI 추가·수시 회의.
- 회의마다 안건(agenda)·꼭 볼 종목(focus)을 붙일 수 있다 — 일정 담당 AI(add·move)와 비서(schedule_meeting, 하루 MAX_ASSISTANT 번)가
  정하고, 남은 회의를 취소(cancel)할 수도 있다. 안건은 그 회의의 모든 에이전트 프롬프트·후보 고르기에 들어간다.
"""

import json
import re
import sqlite3
import threading
from collections.abc import Awaitable, Callable
from datetime import date, datetime

from app.invest.advice import policy
from app.invest.marketdata import market
from app.invest.broker import config
from app.invest.marketdata.market import MarketError
from app.invest.broker.toss import TossError
from app.runs.runner import run_dict, start_run
from app.llm.client import structured

S_CONFIG = "invest_autopilot"
S_INTENTS = "invest_autopilot_intents"
S_PLAN = "invest_autopilot_plan"
DEFAULT_CONFIG = {"enabled": False, "slots": ["09:10", "12:30", "15:00"], "max_extra": 3, "flexible": True, "mode": "continuous"}
MODES = {"continuous": "상시 운용 — 정기 회의 없이 장 내내 대화, 트레이더는 언제든 주문", "meetings": "정기 회의 — 정해 둔 시각에 토론"}
MAX_STREAMS = 3        # 상시 운용 채널을 하루에 여는 최대 (끊긴 뒤 다시 열기 포함)
MAX_SLOTS = 6
MAX_EXTRA = 6
MAX_INTENTS = 20
MAX_ADHOC = 3          # 리스크 경보로 여는 수시 회의 하루 최대
MAX_ASSISTANT = 4      # 비서가 안건을 정해 더하는 회의 하루 최대 (AI 추가 max_extra 와 따로)
ASSIST_LEAD_MIN = 2    # 비서가 정하는 회의는 지금부터 이만큼 뒤부터
WHO = {"ai": "AI", "assistant": "비서"}
OPEN_AFTER_MIN = 5     # 정규장 시작 뒤 이만큼 지나서부터
CLOSE_BEFORE_MIN = 15  # 정규장 끝나기 이만큼 전까지 (마감 동시호가 전)
GRACE_MIN = 20         # 이만큼 넘게 늦은 일정은 따라잡지 않는다
MIN_GAP_MIN = 20       # AI 가 정하는 시각은 다른 판단과 이만큼 떨어져야
LEAD_MIN = 10          # AI 가 정하는 시각은 지금부터 이만큼 뒤부터
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

SESSION_LOCK = threading.Lock()  # 토론은 한 번에 하나 — 수동 시작(라우터)과 자동 시작(tick)이 같이 쓴다


class AutopilotError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---- 설정 저장 ----
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


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def get_config(conn: sqlite3.Connection) -> dict:
    raw = _get(conn, S_CONFIG, {})
    out = dict(DEFAULT_CONFIG)
    if isinstance(raw, dict):
        out["enabled"] = bool(raw.get("enabled", False))
        slots = [s for s in raw.get("slots") or [] if isinstance(s, str) and _HHMM.match(s)]
        out["slots"] = sorted(set(slots)) or out["slots"]
        try:
            out["max_extra"] = max(0, min(int(raw.get("max_extra", out["max_extra"])), MAX_EXTRA))
        except (TypeError, ValueError):
            pass
        out["flexible"] = bool(raw.get("flexible", True))
        out["mode"] = raw.get("mode") if raw.get("mode") in MODES else out["mode"]
    return out


def save_config(conn: sqlite3.Connection, *, enabled: bool | None = None, slots: list[str] | None = None,
                max_extra: int | None = None, flexible: bool | None = None, mode: str | None = None) -> dict:
    cfg = get_config(conn)
    if slots is not None:
        bad = [s for s in slots if not _HHMM.match(s)]
        if bad:
            raise AutopilotError(f"시각은 HH:MM 으로 적어 주세요: {', '.join(bad)}")
        slots = sorted(set(slots))
        if not 1 <= len(slots) <= MAX_SLOTS:
            raise AutopilotError(f"판단 시각은 1~{MAX_SLOTS}개입니다")
        if any(not 9 * 60 <= _minutes(s) <= 15 * 60 + 30 for s in slots):
            raise AutopilotError("판단 시각은 정규장(09:00~15:30) 안이어야 해요")
        cfg["slots"] = slots
    if max_extra is not None:
        if not 0 <= max_extra <= MAX_EXTRA:
            raise AutopilotError(f"AI 가 더할 수 있는 횟수는 0~{MAX_EXTRA}번입니다")
        cfg["max_extra"] = max_extra
    if enabled is not None:
        cfg["enabled"] = enabled
    if flexible is not None:
        cfg["flexible"] = flexible
    if mode is not None:
        if mode not in MODES:
            raise AutopilotError(f"운용 방식은 {', '.join(MODES)} 중 하나예요")
        cfg["mode"] = mode
    _put(conn, S_CONFIG, cfg)
    return cfg


# ---- 운용 지시 ----
def intents(conn: sqlite3.Connection) -> list[dict]:
    raw = _get(conn, S_INTENTS, [])
    return [i for i in raw if isinstance(i, dict) and i.get("text")] if isinstance(raw, list) else []


def add_intent(conn: sqlite3.Connection, text: str, now: datetime | None = None, once: bool = False) -> list[dict]:
    """운용 지시 하나. once = 다음 자동 판단 한 번에만 쓰고 지운다(이번만)."""
    text = text.strip()[:500]
    if not text:
        raise AutopilotError("내용을 입력하세요")
    items = intents(conn)
    if len(items) >= MAX_INTENTS:
        raise AutopilotError(f"운용 지시는 {MAX_INTENTS}개까지예요 — 지난 지시를 지운 뒤 남겨 주세요")
    now = now or market.now_kst()
    items.append({"id": max((int(i.get("id") or 0) for i in items), default=0) + 1,
                  "at": now.strftime("%Y-%m-%d %H:%M"), "text": text, "once": bool(once)})
    _put(conn, S_INTENTS, items)
    return items


def update_intent(conn: sqlite3.Connection, intent_id: int, text: str, once: bool | None = None) -> list[dict]:
    text = text.strip()[:500]
    if not text:
        raise AutopilotError("내용을 입력하세요")
    items = intents(conn)
    hit = next((i for i in items if i.get("id") == intent_id), None)
    if hit is None:
        raise AutopilotError("없는 운용 지시예요", 404)
    hit["text"] = text
    if once is not None:
        hit["once"] = bool(once)
    _put(conn, S_INTENTS, items)
    return items


def consume_once(conn: sqlite3.Connection, ids: list[int]) -> None:
    """자동 판단이 쓴 '이번만' 지시를 지운다."""
    if ids:
        _put(conn, S_INTENTS, [i for i in intents(conn) if not (i.get("once") and i.get("id") in ids)])


def delete_intent(conn: sqlite3.Connection, intent_id: int) -> list[dict]:
    items = intents(conn)
    rest = [i for i in items if i.get("id") != intent_id]
    if len(rest) == len(items):
        raise AutopilotError("없는 운용 지시예요", 404)
    _put(conn, S_INTENTS, rest)
    return rest


# ---- 오늘 일정 ----
def load_plan(conn: sqlite3.Connection, day: date) -> dict:
    raw = _get(conn, S_PLAN, {})
    if not isinstance(raw, dict) or raw.get("date") != day.isoformat():
        return {"date": day.isoformat(), "moved": {}, "extra": [], "log": []}
    return {"date": raw["date"], "moved": dict(raw.get("moved") or {}), "extra": list(raw.get("extra") or []),
            "log": list(raw.get("log") or [])}


def schedule(cfg: dict, plan: dict) -> list[dict]:
    """오늘 판단 일정 (시각순) — 고정(옮겼으면 옮긴 시각) + AI 추가, 기록이 있으면 그 상태."""
    log = {x["key"]: x for x in plan["log"]}
    out = []
    for slot in cfg["slots"]:
        moved = plan["moved"].get(slot) or {}
        out.append({"key": f"fixed:{slot}", "time": moved.get("time", slot), "kind": "fixed", "origin": slot,
                    "reason": moved.get("reason"), "agenda": moved.get("agenda"), "focus": moved.get("focus") or [],
                    "by": moved.get("by", "ai") if moved else None})
    for x in plan["extra"]:
        out.append({"key": f"extra:{x['time']}", "time": x["time"], "kind": "extra", "origin": None, "reason": x.get("reason"),
                    "agenda": x.get("agenda"), "focus": x.get("focus") or [], "by": x.get("by", "ai")})
    for x in plan["log"]:
        if x["kind"] in ("manual", "risk", "stream"):
            out.append({"key": x["key"], "time": x["time"], "kind": x["kind"], "origin": None, "reason": None, "agenda": None,
                        "focus": [], "by": None})
    for e in out:
        rec = log.get(e["key"])
        e.update(status=rec["status"] if rec else "pending", run_id=rec.get("run_id") if rec else None,
                 note=rec.get("note") if rec else None)
    if cfg.get("mode") == "continuous":  # 상시 운용: 정해 둔 회의는 쓰지 않는다 — 이미 지난 기록만 남긴다
        out = [e for e in out if e["kind"] in ("manual", "risk", "stream") or e["status"] != "pending"]
    return sorted(out, key=lambda e: (e["time"], e["key"]))


def _record(conn: sqlite3.Connection, entry: dict, status: str, *, now: datetime, run_id: int | None = None,
            note: str | None = None) -> dict:
    """일정 하나의 결과를 남긴다 — 그사이 세션이 일정을 바꿨을 수 있어(AI 추가·이동) 저장 직전에 다시 읽는다."""
    rec = {"key": entry["key"], "time": entry["time"], "kind": entry["kind"], "status": status, "run_id": run_id,
           "note": note, "at": now.strftime("%H:%M:%S")}
    plan = load_plan(conn, now.date())
    plan["log"] = [x for x in plan["log"] if x["key"] != entry["key"]] + [rec]
    _put(conn, S_PLAN, plan)
    return rec


def trading_window(p) -> tuple[str, str] | None:
    """오늘 자동 판단을 시작할 수 있는 시각 (정규장 시작 + 5분 ~ 끝 − 15분). 휴장일이면 None."""
    sess = p.session()
    start, end = sess.get("start"), sess.get("end")
    if not start or not end:
        return None
    return _hhmm(_minutes(start) + OPEN_AFTER_MIN), _hhmm(_minutes(end) - CLOSE_BEFORE_MIN)


def blocked(conn: sqlite3.Connection) -> str | None:
    """지금 자동 판단을 할 수 없는 이유 (없으면 None)."""
    if config.halted(conn):
        return "전체 주문 중지 중"
    blockers = config.trading_blockers()
    if blockers:
        return "주문할 수 없음: " + " · ".join(blockers)
    return None


def active_session(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT r.*, NULL AS dataset_name, NULL AS parent_name FROM runs r WHERE kind = 'agent_action' "
                       "AND status IN ('queued', 'running') ORDER BY id DESC LIMIT 1").fetchone()
    return run_dict(row) if row else None


def _start(conn: sqlite3.Connection, entry: dict, note: str = "", trigger: str | None = None, focus: dict[str, str] | None = None,
           agenda: str | None = None) -> int:
    """focus = 리스크 경보 종목 → 경보 문장 (후보 앞에 꼭 넣는다)."""
    name = f"자동 운용 · {entry['time']}" + {"extra": " (AI 추가)", "risk": " (수시 회의)"}.get(entry["kind"], "")
    return start_run(conn, "agent_action", {"scope": "autopilot", "autopilot": True, "slot": entry["key"],
                                            "trigger": trigger or entry["kind"], "note": note, "label": name,
                                            "focus": list(focus or {}), "focus_why": focus or {}, "agenda": agenda or ""}, name=name)


Agenda = Callable[[sqlite3.Connection, datetime, dict, dict], tuple[str, str]]


def tick(conn: sqlite3.Connection, now: datetime | None = None, provider=None, agenda: Agenda | None = None) -> dict | None:
    """때가 된 일정 하나를 처리한다 (시작·건너뜀·놓침을 기록). 할 일이 없거나 다른 토론을 기다리면 None.
    agenda(conn, now, 일정, 오늘 계획) → ('meet'|'skip'|'wait', 이유): 필요할 때만 회의 (없으면 언제나 회의)."""
    cfg = get_config(conn)
    if not cfg["enabled"]:
        return None
    now = now or market.now_kst()
    if cfg["mode"] == "continuous":
        return _stream_tick(conn, now, provider)
    plan = load_plan(conn, now.date())
    minute = now.hour * 60 + now.minute
    entry = next((e for e in schedule(cfg, plan) if e["status"] == "pending" and _minutes(e["time"]) <= minute), None)
    if entry is None:
        return None
    if minute - _minutes(entry["time"]) > GRACE_MIN:
        return _record(conn, entry, "missed", now=now,
                       note=f"{GRACE_MIN}분 넘게 늦어 건너뛰었어요 (서버가 꺼져 있었거나 다른 토론이 길어짐)")
    why = blocked(conn)
    if why:
        return _record(conn, entry, "skipped", now=now, note=f"{why} — 건너뛰었어요")
    try:
        win = trading_window(provider or market.provider())
    except (MarketError, TossError) as e:
        return _record(conn, entry, "skipped", now=now, note=f"장 운영 시간을 확인하지 못했어요: {e}")
    if win is None:
        return _record(conn, entry, "skipped", now=now, note="휴장일이에요")
    if not _minutes(win[0]) <= minute <= _minutes(win[1]):
        return _record(conn, entry, "skipped", now=now, note=f"자동 판단 시간({win[0]}~{win[1]})이 아니에요")
    action, why = agenda(conn, now, entry, plan) if agenda else ("meet", "")
    if action == "wait":
        return None  # 리서치가 끝나면 (GRACE_MIN 안에서) 다시 본다
    if action == "skip":
        return _record(conn, entry, "skipped", now=now, note=why)
    with SESSION_LOCK:
        if active_session(conn):
            return None  # 다른 토론이 끝나면 (GRACE_MIN 안에서) 다시 본다
        run_id = _start(conn, entry, agenda=why, focus={s: why for s in entry.get("focus") or []})
    return _record(conn, entry, "started", now=now, run_id=run_id, note=f"안건: {why}" if why else None)


# ---- 상시 운용 채널 ----
def is_stream(run: dict | None) -> bool:
    return bool(run and (run.get("params") or {}).get("stream"))


def _start_stream(conn: sqlite3.Connection, now: datetime, note: str = "") -> int:
    entry = {"key": f"stream:{now.strftime('%H:%M:%S')}", "time": now.strftime("%H:%M"), "kind": "stream"}
    name = f"상시 운용 · {now.strftime('%m-%d')}"
    run_id = start_run(conn, "agent_action", {"scope": "autopilot", "autopilot": True, "stream": True, "slot": entry["key"],
                                              "trigger": "stream", "note": note, "label": "상시 운용"}, name=name)
    _record(conn, entry, "started", now=now, run_id=run_id, note="정기 회의 없이 장 내내 대화 · 트레이더는 언제든 주문")
    return run_id


def _stream_tick(conn: sqlite3.Connection, now: datetime, provider=None) -> dict | None:
    """상시 운용: 자동 판단 시간 안인데 채널이 돌고 있지 않으면 연다. 끊겼으면(실패·꺼졌다 켜짐) 다시, 사용자가 중지했으면 그날은 그대로."""
    plan = load_plan(conn, now.date())
    opened = [x for x in plan["log"] if x["kind"] == "stream" and x["status"] == "started"]
    if opened:
        last = conn.execute("SELECT status, summary_json FROM runs WHERE id = ?", (opened[-1]["run_id"],)).fetchone()
        if last is None or last["status"] in ("queued", "running") or last["status"] == "cancelled":
            return None
        try:
            ended = json.loads(last["summary_json"] or "{}").get("end")
        except ValueError:
            ended = None
        if ended == "closed" or len(opened) >= MAX_STREAMS:
            return None  # 오늘 장 끝까지 했거나 다시 열 수 있는 횟수를 다 씀
    if blocked(conn):
        return None
    try:
        win = trading_window(provider or market.provider())
    except (MarketError, TossError):
        return None
    minute = now.hour * 60 + now.minute
    if win is None or not _minutes(win[0]) <= minute < _minutes(win[1]):
        return None
    with SESSION_LOCK:
        if active_session(conn):
            return None  # 다른 토론이 끝나면 연다
        run_id = _start_stream(conn, now)
    return {"time": now.strftime("%H:%M"), "status": "started", "run_id": run_id, "note": "상시 운용 채널을 열었어요"}


def post_to_stream(conn: sqlite3.Connection, text: str) -> int | None:
    """돌고 있는 상시 운용 채널에 관리자 말로 넣는다 → 그 실행 id (채널이 없으면 None)."""
    from app.invest.jobs import agent_action  # agent_action 이 이 모듈을 불러서 늦게
    run = active_session(conn)
    if not is_stream(run) or agent_action.post_user_message(run["id"], text) is None:
        return None
    return run["id"]


def _check_now(conn: sqlite3.Connection, now: datetime, provider=None) -> None:
    """지금 판단을 시작해도 되는지 — 주문 가능, 자동 판단 시간 안."""
    why = blocked(conn)
    if why:
        raise AutopilotError(why, 409)
    try:
        win = trading_window(provider or market.provider())
    except (MarketError, TossError) as e:
        raise AutopilotError(f"장 운영 시간을 확인하지 못했어요: {e}", 502) from None
    minute = now.hour * 60 + now.minute
    if win is None or not _minutes(win[0]) <= minute <= _minutes(win[1]):
        raise AutopilotError("휴장일이에요" if win is None else f"자동 판단 시간({win[0]}~{win[1]})이 아니에요", 409)


def run_now(conn: sqlite3.Connection, now: datetime | None = None, provider=None, note: str = "",
            trigger: str = "manual") -> int:
    """지금 바로 한 번 (화면의 '지금 판단', 비서의 즉시 지시). 일정과 같은 검사. note = 관리자 메시지로 먼저 들어갈 지시."""
    cfg = get_config(conn)
    if not cfg["enabled"]:
        raise AutopilotError("자동 운용이 꺼져 있어요 — 먼저 켜 주세요", 409)
    now = now or market.now_kst()
    if cfg["mode"] == "continuous":  # 상시 운용: 채널에 말로 넣고, 채널이 없으면 연다
        posted = post_to_stream(conn, f"[지금 판단] {note}".strip() if note else "[지금 판단] 지금 계좌와 입장판을 보고 판단해 주세요")
        if posted:
            return posted
        _check_now(conn, now, provider)
        with SESSION_LOCK:
            running = active_session(conn)
            if running:
                raise AutopilotError(f"이미 진행 중인 토론이 있습니다 (#{running['id']})", 409)
            return _start_stream(conn, now, note=note)
    _check_now(conn, now, provider)
    entry = {"key": f"manual:{now.strftime('%H:%M:%S')}", "time": now.strftime("%H:%M"), "kind": "manual"}
    with SESSION_LOCK:
        running = active_session(conn)
        if running:
            raise AutopilotError(f"이미 진행 중인 토론이 있습니다 (#{running['id']})", 409)
        run_id = _start(conn, entry, note=note, trigger=trigger)
    _record(conn, entry, "started", now=now, run_id=run_id)
    return run_id


def run_adhoc(conn: sqlite3.Connection, why: dict[str, str], now: datetime | None = None, provider=None) -> int:
    """리스크 경보로 여는 수시 회의 — why(종목 → 경보 문장)의 종목을 먼저 후보로. 자동 운용이 켜져 있고 일정과 같은 검사, 하루 MAX_ADHOC 번."""
    cfg = get_config(conn)
    if not cfg["enabled"]:
        raise AutopilotError("자동 운용이 꺼져 있어요", 409)
    if cfg["mode"] == "continuous":
        raise AutopilotError("상시 운용 중이라 경보는 채널에서 바로 다뤄요", 409)
    now = now or market.now_kst()
    plan = load_plan(conn, now.date())
    if sum(1 for x in plan["log"] if x["kind"] == "risk" and x["status"] == "started") >= MAX_ADHOC:
        raise AutopilotError(f"오늘 수시 회의({MAX_ADHOC}번)를 다 썼어요", 409)
    _check_now(conn, now, provider)
    entry = {"key": f"risk:{now.strftime('%H:%M:%S')}", "time": now.strftime("%H:%M"), "kind": "risk"}
    with SESSION_LOCK:
        running = active_session(conn)
        if running:
            raise AutopilotError(f"이미 진행 중인 토론이 있습니다 (#{running['id']})", 409)
        run_id = _start(conn, entry, trigger="risk", focus=why, agenda="리스크 경보")
    _record(conn, entry, "started", now=now, run_id=run_id, note="안건: 리스크 경보")
    return run_id


def start_now(conn: sqlite3.Connection, instruction: str, now: datetime | None = None, provider=None) -> dict:
    """비서가 받은 즉시 지시로 지금 판단을 시작한다. 자동 운용이 켜져 있으면 자동 운용 판단(과반 게이트·승인 없이),
    꺼져 있으면 보유 + 운용 지시 + 이 지시로 후보를 정한 보통 토론(만장일치 게이트·관리자 승인)."""
    instruction = instruction.strip()[:500]
    if get_config(conn)["enabled"]:
        return {"run_id": run_now(conn, now, provider, note=instruction, trigger="assistant"), "autopilot": True}
    now = now or market.now_kst()
    _check_now(conn, now, provider)
    name = f"비서 요청 · {now.strftime('%H:%M')}"
    with SESSION_LOCK:
        running = active_session(conn)
        if running:
            raise AutopilotError(f"이미 진행 중인 토론이 있습니다 (#{running['id']})", 409)
        run_id = start_run(conn, "agent_action", {"scope": "autopilot", "intents": [i["text"] for i in intents(conn)] + [instruction],
                                                  "note": instruction, "trigger": "assistant", "label": name}, name=name)
    return {"run_id": run_id, "autopilot": False}


# ---- AI 가 정하는 다음 판단 ----
def apply_proposal(conn: sqlite3.Connection, proposal: dict, now: datetime, window: tuple[str, str] | None,
                   run_id: int | None = None, by: str = "ai") -> dict:
    """회의 일정 제안을 검사해 오늘 일정에 반영한다 — 일정 담당 AI(by=ai: add·move·cancel·none)와 비서(by=assistant: add·cancel).
    agenda(안건)·symbols(꼭 볼 종목)는 그 회의에 붙는다. → {accepted, action, time, reason, agenda, note}"""
    action = proposal.get("action") if proposal.get("action") in ("add", "move", "cancel") else "none"
    t = str(proposal.get("time") or "").strip()
    reason = str(proposal.get("reason") or "").strip()[:300]
    agenda = str(proposal.get("agenda") or "").strip()[:200]
    symbols = [x for x in (str(v).strip() for v in proposal.get("symbols") or []) if x.isdigit() and len(x) == 6][:4]
    out = {"accepted": False, "action": action, "time": t or None, "reason": reason, "agenda": agenda or None, "by": by}
    if action == "none":
        return {**out, "note": "오늘 남은 판단 시각은 그대로예요"}
    cfg = get_config(conn)
    plan = load_plan(conn, now.date())
    entries = schedule(cfg, plan)
    minute = now.hour * 60 + now.minute
    if action == "cancel":
        target = next((e for e in entries if e["status"] == "pending" and e["kind"] in ("fixed", "extra")
                       and (e["time"] == t if t else _minutes(e["time"]) >= minute)), None)
        if target is None:
            return {**out, "note": f"취소할 남은 회의가 없어 그대로 둬요{f' ({t})' if t else ''}"}
        _record(conn, target, "skipped", now=now, note=f"{WHO[by]}가 취소: {reason or '이유 없음'}")
        return {**out, "accepted": True, "time": target["time"], "note": f"{target['time']} 회의를 취소했어요"}
    if not _HHMM.match(t):
        return {**out, "note": f"시각 형식이 맞지 않아 그대로 둬요 ({t or '없음'})"}
    if window is None:
        return {**out, "note": "오늘은 휴장일이라 그대로 둬요"}
    lead = LEAD_MIN if by == "ai" else ASSIST_LEAD_MIN
    if _minutes(t) < minute + lead:
        return {**out, "note": f"{lead}분 뒤부터만 정할 수 있어 그대로 둬요"}
    if not _minutes(window[0]) <= _minutes(t) <= _minutes(window[1]):
        return {**out, "note": f"자동 판단 시간({window[0]}~{window[1]}) 밖이라 그대로 둬요"}
    target = None
    if action == "move":
        target = next((e for e in entries if e["kind"] == "fixed" and e["status"] == "pending" and _minutes(e["time"]) > minute), None)
        if target is None:
            return {**out, "note": "옮길 남은 고정 시각이 없어 그대로 둬요"}
    elif by == "ai" and sum(1 for x in plan["extra"] if x.get("by", "ai") == "ai") >= cfg["max_extra"]:
        return {**out, "note": f"오늘 AI 추가 판단({cfg['max_extra']}번)을 다 써서 그대로 둬요"}
    elif by == "assistant" and sum(1 for x in plan["extra"] if x.get("by") == "assistant") >= MAX_ASSISTANT:
        return {**out, "note": f"오늘 비서가 정할 수 있는 회의({MAX_ASSISTANT}번)를 다 썼어요"}
    near = [e for e in entries if e is not target and e["status"] in ("pending", "started")
            and abs(_minutes(e["time"]) - _minutes(t)) < MIN_GAP_MIN]
    if near:
        return {**out, "note": f"{near[0]['time']} 판단과 {MIN_GAP_MIN}분 안이라 그대로 둬요"}
    rec = {"time": t, "reason": reason or agenda, "run_id": run_id, "agenda": agenda or None, "focus": symbols, "by": by}
    if target is not None:
        plan["moved"][target["origin"]] = rec
        note = f"{target['time']} 판단을 {t}로 옮겼어요"
    else:
        plan["extra"].append(rec)
        note = f"{t}에 판단을 하나 더했어요" + (f" — 안건: {agenda}" if agenda else "")
    _put(conn, S_PLAN, plan)
    return {**out, "accepted": True, "note": note, "from": target["time"] if target else None}


PLAN_SCHEMA = {
    "type": "object",
    "properties": {"action": {"type": "string", "enum": ["none", "add", "move", "cancel"]}, "time": {"type": "string"},
                   "reason": {"type": "string"}, "agenda": {"type": "string"},
                   "symbols": {"type": "array", "items": {"type": "string"}}},
    "required": ["action", "time", "reason"],
}
PLAN_SYSTEM = f"""당신은 자동 매매의 일정 담당입니다. 방금 끝난 판단(토론·게이트·주문)과 오늘 남은 판단 시각, 운용 목표를 보고, 다음 판단을 언제
무엇을 안건으로 할지 정합니다. 일정은 당신에게 맡겨져 있습니다.
- add: 남은 시각 사이에 판단을 하나 더함 / move: 다음 고정 시각을 다른 시각으로 옮김 / cancel: 다음 회의(time 을 주면 그 시각)가
  필요 없으면 취소 / none: 그대로.
- add·move 에는 agenda(그 회의에서 다룰 것, 한 문장)와 symbols(꼭 볼 6자리 종목 코드, 4개까지)를 붙이세요 — 그 회의의 에이전트들이
  안건으로 받습니다.
- 운용 목표의 매매 빈도·투자 기간에 맞추세요: 장기·가끔이면 회의를 줄이고(cancel), 단기·자주면 필요한 때 더하세요.
- 이유가 뚜렷할 때만 add·move 하세요 — 예: 큰 변동 직후 다시 볼 필요, 운용 지시가 특정 시간대를 원함, 주문이 체결되지 않아 다시 확인.
  애매하면 none. 판단마다 비용이 들고 잦은 매매는 수수료·실수를 늘립니다.
- time 은 HH:MM (한국 시각), 자동 판단 시간 안, 지금부터 {LEAD_MIN}분 뒤부터, 다른 판단과 {MIN_GAP_MIN}분 이상 떨어지게. none 이면 time 은 "",
  cancel 은 비우면 다음 회의.
- '안건이 있을 때만' 회의면 정기(고정) 시각은 새 발굴 목록·운용 지시·리스크 경보가 없을 때 건너뜁니다. 그 시각에 꼭 다시 봐야 하면
  (예: 목표 비중까지 남은 현금 배분, 보류한 주문 조건 확인) add·move 로 정하세요 — AI 가 정한 회의는 언제나 열립니다.
- reason 은 한 문장, 한국어."""

KIND_KO = {"fixed": "고정", "extra": "AI 추가", "manual": "지금 판단", "risk": "수시 회의(리스크 경보)", "stream": "상시 운용"}
PlanLlm = Callable[[str, str, str, dict, str | None], Awaitable[dict]]


async def _default_planner(feature: str, system: str, prompt: str, schema: dict, model: str | None) -> dict:
    return await structured(feature, prompt, schema, model=model, system_prompt=system)


PLANNER: PlanLlm = _default_planner  # 테스트에서 가짜로 바꾼다


def plan_prompt(conn: sqlite3.Connection, now: datetime, window: tuple[str, str], summary: str) -> str:
    cfg = get_config(conn)
    plan = load_plan(conn, now.date())
    status_ko = {"pending": "예정", "started": "함", "skipped": "건너뜀", "missed": "놓침"}
    lines = [f"- {e['time']} {KIND_KO[e['kind']]} · {status_ko[e['status']]}"
             + (f" ({e['reason']})" if e.get("reason") else "") for e in schedule(cfg, plan)]
    texts = [i["text"] for i in intents(conn)]
    goal = policy.text(policy.get(conn))
    return (f"지금 {now.strftime('%H:%M')} · 자동 판단 시간 {window[0]}~{window[1]} · 오늘 AI 추가 {len(plan['extra'])}/{cfg['max_extra']}번 · "
            f"회의 방식 {'안건이 있을 때만(정기 시각도 새 안건이 없으면 건너뜀)' if cfg['flexible'] else '정기 시각마다'}\n"
            f"오늘 판단 일정:\n" + "\n".join(lines) + "\n\n방금 끝난 판단:\n" + summary
            + ("\n\n운용 지시:\n" + "\n".join(f"- {t}" for t in texts) if texts else "")
            + (f"\n\n운용 목표:\n{goal}" if goal else "")
            + "\n\n다음 판단을 더하거나(add) 다음 고정 시각을 옮기거나(move) 남은 회의를 취소할지(cancel), 그대로 둘지(none) 정하세요.")


async def plan_next(conn: sqlite3.Connection, now: datetime, window: tuple[str, str], summary: str,
                    model: str | None = None) -> dict:
    raw = await PLANNER("agent_action", PLAN_SYSTEM, plan_prompt(conn, now, window, summary), PLAN_SCHEMA, model)
    return raw if isinstance(raw, dict) else {}


# ---- 화면 ----
def status(conn: sqlite3.Connection, now: datetime | None = None, provider=None) -> dict:
    now = now or market.now_kst()
    cfg = get_config(conn)
    plan = load_plan(conn, now.date())
    window, window_note = None, None
    try:
        window = trading_window(provider or market.provider())
        if window is None:
            window_note = "오늘은 휴장일이에요"
    except (MarketError, TossError) as e:
        window_note = f"장 운영 시간을 확인하지 못했어요: {e}"
    entries = schedule(cfg, plan)
    minute = now.hour * 60 + now.minute
    upcoming = next((e for e in entries if e["status"] == "pending" and _minutes(e["time"]) >= minute), None)
    return {"config": cfg, "intents": intents(conn), "today": {"date": plan["date"], "entries": entries,
            "extra_used": len(plan["extra"])}, "window": window, "window_note": window_note,
            "next": upcoming if cfg["enabled"] and cfg["mode"] != "continuous" else None,
            "blocked": f"{why} — 일정이 와도 건너뛰어요" if (why := blocked(conn)) else None,
            "limits": {"open_after_min": OPEN_AFTER_MIN, "close_before_min": CLOSE_BEFORE_MIN, "grace_min": GRACE_MIN,
                       "min_gap_min": MIN_GAP_MIN, "lead_min": LEAD_MIN, "max_intents": MAX_INTENTS, "max_adhoc": MAX_ADHOC,
                       "max_assistant": MAX_ASSISTANT}}
