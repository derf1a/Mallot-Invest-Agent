"""운용 데스크 — 자산운용사처럼 역할이 나뉜 에이전트가 필요한 때 움직인다.

  시장 분석(시장 전략가) → 섹터 탐색(섹터 애널리스트) → 종목 발굴(종목 발굴 애널리스트)   ← 리서치, 장 시작 전 + 시장이 크게 움직이면
  → 투자위원회(분석 에이전트 토론, 이번 회의 후보는 포트폴리오 매니저) → 집행(트레이더)   ← 안건이 있을 때만
  → 리스크 감시(리스크 매니저, 장 중 WATCH_MIN 분마다 코드) → 급변하면 수시 회의

이 모듈은 가벼운 공용 부분: 사람(역할), 리서치 결과 기록(invest_desk, v19), 그날 상태(리서치·감시·경보), 회의 안건 판단(agenda),
회의 후보·에이전트·브리핑·비서가 읽는 리서치 요약(brief). 리서치 단계(LLM)·리스크 감시·일정 확인(tick)은 desk_agents.py.

- 회의는 필요할 때만(자동 운용 설정 flexible, 기본 켜짐): 정기 시각이 와도 지난 회의 뒤 새 안건(새 발굴 목록·새 운용 지시·리스크 경보)이
  없으면 건너뛴다. 오늘 첫 회의와 마지막 정기 회의(마감 전 보유 점검), AI 가 이유를 들어 더하거나 옮긴 회의는 연다. 리서치가 돌고 있으면
  끝날 때까지 기다린다.
- 리서치는 자동 운용이 켜져 있을 때 스스로 돌고, 꺼져 있어도 화면·비서로 지금 돌릴 수 있다(주문과 무관해 PIN 없음).
- 데스크 설정(settings invest_desk_config, get_config·save_config): 장 전 리서치 켜기·몇 분 전, 리스크 감시 켜기·간격·급락·급등 기준,
  경보 때 수시 회의, 시장 급변 때 리서치 다시. 화면(자동 운용 서랍)과 비서(update_desk)가 고친다. 주문은 여전히 place_order 가 검사해 PIN 없음.
"""

import json
import sqlite3
from datetime import date, datetime

from app.invest.marketdata import market
from app.invest.ops import autopilot
from app.runs.runner import run_dict, start_run

STAGES = ("market", "sector", "ideas", "alert")
S_STATE = "invest_desk_state"
S_CONFIG = "invest_desk_config"

PREP_MIN = 40            # 정규장 시작 이만큼 전부터 아침 리서치 (기본값 — 설정 prep_min)
MORNING_TRIES = 3        # 아침 리서치가 결과 없이 끝나면(서버 재시작 등) 다시 시도하는 하루 최대
RETRY_MIN = 10           # 다시 시도하기 전 기다리는 분
WATCH_MIN = 10           # 장 중 리스크 감시 간격 (기본값 — 설정 watch_min)
REFRESH_MOVE = 0.015     # 지수 등락이 아침 분석 때보다 이만큼(%p) 넘게 달라지면 리서치를 다시
MAX_REFRESH = 2          # 장 중 다시 하는 리서치 하루 최대
REFRESH_GAP_MIN = 60     # 리서치 사이 최소 간격

# 데스크 설정 — 퍼센트 값은 사람이 읽는 % 그대로 (4.0 = 4%)
DEFAULT_CONFIG = {"research": True, "prep_min": PREP_MIN, "watch": True, "watch_min": WATCH_MIN,
                  "drop_pct": 4.0, "jump_pct": 6.0, "breakout_pct": 5.0, "adhoc": True, "refresh": True}
BOUNDS = {"prep_min": (10, 120), "watch_min": (5, 60), "drop_pct": (1.0, 20.0), "jump_pct": (1.0, 30.0), "breakout_pct": (1.0, 30.0)}
CONFIG_KO = {"research": "장 전 리서치", "prep_min": "장 시작 몇 분 전 리서치", "watch": "리스크 감시", "watch_min": "리스크 감시 간격(분)",
             "drop_pct": "보유 급락 경보(%)", "jump_pct": "보유 급등 경보(%)", "breakout_pct": "발굴 종목 급등 경보(%)",
             "adhoc": "경보 때 수시 회의", "refresh": "시장 급변 때 리서치 다시"}

REGIME_KO = {"risk_on": "위험 선호", "neutral": "중립", "risk_off": "위험 회피"}
CASH_KO = {"raise": "현금 늘리기", "keep": "현금 유지", "lower": "현금 줄이기"}
TRIGGER_KO = {"morning": "장 전 리서치", "market_move": "시장 급변 — 다시 보기", "manual": "지금 리서치", "assistant": "비서 요청"}

# 리서치 담당 (토론 명단과 따로 — 회의에서 입장을 내지 않고 회의 전에 안건을 만든다). 프로필 포켓몬: 시장 전략가 야부엉(멀리 내다봄) ·
# 섹터 애널리스트 피죤투(넓게 날며 살핌) · 종목 발굴 가디(냄새로 찾아냄)
PEOPLE: dict[str, dict] = {
    "strategist": {"key": "strategist", "title": "시장 전략가", "name": "올리비아", "full_name": "Olivia Bennett", "country": "캐나다",
                   "pokemon": 164, "stage": "market",
                   "role": "장 시작 전 시장 국면(위험 선호·중립·위험 회피)과 시황·주목할 테마를 정하고, 장 중 지수가 크게 움직이면 다시 봐요"},
    "sector": {"key": "sector", "title": "섹터 애널리스트", "name": "닐스", "full_name": "Nils Andersson", "country": "스웨덴",
               "pokemon": 18, "stage": "sector",
               "role": "토스 섹터 전체의 기간 등락을 훑어 비중을 늘릴 섹터와 줄일 섹터를 골라요"},
    "scout": {"key": "scout", "title": "종목 발굴 애널리스트", "name": "탄디", "full_name": "Thandi Mokoena", "country": "남아프리카공화국",
              "pokemon": 58, "stage": "ideas",
              "role": "확대 섹터의 대표 종목·시장 랭킹·운용 지시·관심 종목을 점수로 훑어 발굴 목록을 만들어요"},
}
# 자문 에이전트 (advisor.py) — 관리자와 대화해 운용 목표를 정한다. 프로필 가디안(마음을 읽고 곁에서 돕는 역할)
ADVISOR = {"key": "advisor", "title": "자문 에이전트", "name": "에밀리아", "full_name": "Emilia Novák", "country": "체코", "pokemon": 282,
           "role": "운용 목표(목표 방식·수익률·벤치마크·기간·위험 등)를 대화로 여쭤보고 정리해 비서에게 넘겨요"}
# 회의 명단의 에이전트가 함께 맡는 일
DUTIES = {"portfolio": "회의 때 발굴 목록·보유·운용 지시를 보고 이번 회의 후보(안건)를 골라요",
          "risk": f"장 중 {WATCH_MIN}분마다 보유·발굴 종목을 살피고, 급변하면 수시 회의를 열어요",
          "trader": "회의에서 열린 종목만 한도 안에서 주문해요"}
PROCESS = [("market", "시장 분석", "strategist"), ("sector", "섹터 탐색", "sector"), ("ideas", "종목 발굴", "scout"),
           ("committee", "투자위원회", "portfolio"), ("execution", "집행", "trader"), ("risk", "리스크 감시", "risk")]


def person(key: str) -> dict:
    p = PEOPLE[key]
    return {k: p[k] for k in ("key", "title", "name", "full_name", "country", "pokemon")}


# ---- 기록 ----
def _row(r: sqlite3.Row) -> dict:
    try:
        data = json.loads(r["data"])
    except ValueError:
        data = {}
    return {"id": r["id"], "created_at": r["created_at"], "day": r["day"], "stage": r["stage"], "run_id": r["run_id"], "data": data}


def save(conn: sqlite3.Connection, stage: str, data: dict, *, run_id: int | None = None, now: datetime | None = None) -> dict:
    now = now or market.now_kst()
    cur = conn.execute("INSERT INTO invest_desk (created_at, day, stage, run_id, data) VALUES (?, ?, ?, ?, ?)",
                       (now.strftime("%Y-%m-%d %H:%M:%S"), now.date().isoformat(), stage, run_id,
                        json.dumps(data, ensure_ascii=False, default=str)))
    conn.commit()
    return _row(conn.execute("SELECT * FROM invest_desk WHERE id = ?", (cur.lastrowid,)).fetchone())


def latest(conn: sqlite3.Connection, stage: str, day: date | None = None) -> dict | None:
    """그 단계의 마지막 결과 (day 를 주면 그날 것만)."""
    if day is None:
        r = conn.execute("SELECT * FROM invest_desk WHERE stage = ? ORDER BY id DESC LIMIT 1", (stage,)).fetchone()
    else:
        r = conn.execute("SELECT * FROM invest_desk WHERE stage = ? AND day = ? ORDER BY id DESC LIMIT 1",
                         (stage, day.isoformat())).fetchone()
    return _row(r) if r else None


def regime(conn: sqlite3.Connection, day: date) -> str:
    """오늘 시장 국면 — 리서치 전이면 중립 (screening 가중치가 쓴다)."""
    m = latest(conn, "market", day)
    return (m["data"].get("regime") if m else None) or "neutral"


def alerts(conn: sqlite3.Connection, day: date) -> list[dict]:
    return [_row(r) for r in conn.execute("SELECT * FROM invest_desk WHERE stage = 'alert' AND day = ? ORDER BY id",
                                          (day.isoformat(),))]


# ---- 데스크 설정 ----
class DeskError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def get_config(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (S_CONFIG,)).fetchone()
    try:
        raw = json.loads(row["value"]) if row else {}
    except ValueError:
        raw = {}
    out = dict(DEFAULT_CONFIG)
    if isinstance(raw, dict):
        for k, default in DEFAULT_CONFIG.items():
            v = raw.get(k)
            if isinstance(default, bool):
                out[k] = v if isinstance(v, bool) else default
            elif isinstance(v, (int, float)) and not isinstance(v, bool) and BOUNDS[k][0] <= v <= BOUNDS[k][1]:
                out[k] = type(default)(v)
    return out


def save_config(conn: sqlite3.Connection, changes: dict) -> dict:
    """바꿀 값만 받아 검사 뒤 저장한다. 모르는 항목·범위 밖이면 DeskError — 아무것도 바꾸지 않는다."""
    cfg = get_config(conn)
    for k, v in changes.items():
        if v is None:
            continue
        if k not in DEFAULT_CONFIG:
            raise DeskError(f"모르는 데스크 설정이에요: {k} — {', '.join(DEFAULT_CONFIG)}")
        default = DEFAULT_CONFIG[k]
        if isinstance(default, bool):
            if not isinstance(v, bool):
                raise DeskError(f"{CONFIG_KO[k]}는 켜기(true)·끄기(false)예요")
            cfg[k] = v
            continue
        try:
            num = type(default)(v)
        except (TypeError, ValueError):
            raise DeskError(f"{CONFIG_KO[k]}는 숫자예요") from None
        lo, hi = BOUNDS[k]
        if isinstance(v, bool) or not lo <= num <= hi:
            raise DeskError(f"{CONFIG_KO[k]}는 {lo:g}~{hi:g} 사이예요")
        cfg[k] = num
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (S_CONFIG, json.dumps(cfg, ensure_ascii=False)))
    conn.commit()
    return cfg


def describe(cfg: dict) -> dict[str, str]:
    """설정 한 줄씩 — 비서가 바꾼 전후를 알릴 때."""
    return {"리서치": f"장 시작 {cfg['prep_min']}분 전" if cfg["research"] else "꺼짐",
            "리스크 감시": (f"{cfg['watch_min']}분마다 · 보유 −{cfg['drop_pct']:g}% / +{cfg['jump_pct']:g}% · 발굴 +{cfg['breakout_pct']:g}%"
                       if cfg["watch"] else "꺼짐"),
            "수시 회의": "경보 때 열기" if cfg["adhoc"] else "열지 않음",
            "급변 리서치": f"지수 ±{REFRESH_MOVE * 100:.1f}% 넘게 움직이면 (하루 {MAX_REFRESH}번)" if cfg["refresh"] else "꺼짐"}


# ---- 그날 상태 (리서치 시작·감시 시각·이미 알린 경보·미룬 수시 회의) ----
def state(conn: sqlite3.Connection, day: date) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (S_STATE,)).fetchone()
    try:
        raw = json.loads(row["value"]) if row else {}
    except ValueError:
        raw = {}
    if not isinstance(raw, dict) or raw.get("date") != day.isoformat():
        raw = {}
    return {"date": day.isoformat(), "research": list(raw.get("research") or []), "last_watch": raw.get("last_watch"),
            "alerted": list(raw.get("alerted") or []), "pending": raw.get("pending")}


def put_state(conn: sqlite3.Connection, st: dict) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (S_STATE, json.dumps(st, ensure_ascii=False)))
    conn.commit()


# ---- 리서치 실행 ----
def research_running(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT r.*, NULL AS dataset_name, NULL AS parent_name FROM runs r WHERE kind = 'desk_research' "
                       "AND status IN ('queued', 'running') ORDER BY id DESC LIMIT 1").fetchone()
    return run_dict(row) if row else None


def start_research(conn: sqlite3.Connection, trigger: str = "manual", note: str = "", now: datetime | None = None) -> int:
    """리서치(시장 분석 → 섹터 탐색 → 종목 발굴)를 실험실 작업으로 시작한다. 이미 돌고 있으면 409."""
    running = research_running(conn)
    if running:
        raise DeskError(f"리서치가 이미 진행 중이에요 (#{running['id']})", 409)
    now = now or market.now_kst()
    name = f"리서치 · {TRIGGER_KO.get(trigger, trigger)} · {now.strftime('%H:%M')}"
    run_id = start_run(conn, "desk_research", {"trigger": trigger, "note": note}, name=name)
    st = state(conn, now.date())
    st["research"].append({"at": now.strftime("%H:%M:%S"), "run_id": run_id, "trigger": trigger})
    put_state(conn, st)
    return run_id


# ---- 회의 안건 (필요할 때만 회의) ----
def agenda(conn: sqlite3.Connection, now: datetime, entry: dict, plan: dict) -> tuple[str, str]:
    """정기·AI 추가 회의 시각이 왔을 때 → ('meet' | 'skip' | 'wait', 이유)."""
    if research_running(conn):
        return "wait", "리서치가 끝나면 회의해요"
    if entry["kind"] == "extra" or entry.get("reason"):  # AI·비서가 이유를 들어 더하거나 옮긴 회의 — 그 안건(이유)으로 연다
        who = "비서가 정한 회의" if entry.get("by") == "assistant" else "AI 가 정한 회의"
        why = str(entry.get("agenda") or entry.get("reason") or "").strip()
        return "meet", f"{who} — {why[:80]}{'…' if len(why) > 80 else ''}" if why else who
    cfg = autopilot.get_config(conn)
    if not cfg["flexible"]:
        return "meet", "정기 회의"
    started = sorted(x["at"] for x in plan["log"] if x["status"] == "started" and x.get("at"))
    if not started:
        return "meet", "오늘 첫 회의"
    since = f"{plan['date']} {started[-1]}"
    ideas = latest(conn, "ideas", now.date())
    if ideas and ideas["created_at"] > since:
        return "meet", "새 발굴 목록"
    if any(str(i.get("at") or "") > since[:16] for i in autopilot.intents(conn)):
        return "meet", "새 운용 지시"
    if any(a["created_at"] > since for a in alerts(conn, now.date())):
        return "meet", "리스크 경보"
    rest = [e for e in autopilot.schedule(cfg, plan) if e["status"] == "pending" and e["key"] != entry["key"]
            and e["kind"] in ("fixed", "extra")]
    if not rest:
        return "meet", "오늘 마지막 회의 — 마감 전 보유 점검"
    return "skip", "새 안건이 없어 건너뛰었어요 (발굴 목록·운용 지시·경보가 지난 회의 그대로)"


# ---- 리서치 요약 (회의 후보 고르기·에이전트·브리핑·비서) ----
def today(conn: sqlite3.Connection, day: date) -> dict:
    return {"market": latest(conn, "market", day), "sector": latest(conn, "sector", day), "ideas": latest(conn, "ideas", day),
            "alerts": alerts(conn, day)}


def ideas_for_pool(conn: sqlite3.Connection, day: date) -> list[dict]:
    """오늘 발굴 목록 → 회의 후보를 고를 때 훑는 종목 (출처 '발굴')."""
    i = latest(conn, "ideas", day)
    return [{"symbol": x["symbol"], "name": x.get("name") or x["symbol"], "security_type": x.get("security_type") or "",
             "reason": x.get("reason") or ""} for x in (i["data"].get("picks") if i else None) or []]


def _who(key: str) -> str:
    return f"{PEOPLE[key]['name']}({PEOPLE[key]['title']})"


def brief(conn: sqlite3.Connection, day: date) -> str:
    """오늘 리서치를 몇 줄로 — 없으면 ''."""
    t = today(conn, day)
    lines = []
    if t["market"]:
        d = t["market"]["data"]
        lines.append(f"- 시장 {_who('strategist')} {t['market']['created_at'][11:16]}: {REGIME_KO.get(d.get('regime'), '중립')} — "
                     f"{d.get('headline') or ''}" + (f" · 주목 {', '.join(d['favor'])}" if d.get("favor") else "")
                     + (f" · 피할 것 {', '.join(d['avoid'])}" if d.get("avoid") else "")
                     + (f" · {CASH_KO[d['cash']]}" if d.get("cash") in CASH_KO else ""))
    if t["sector"]:
        d = t["sector"]["data"]
        over = "; ".join(f"{x['name']}({x['reason'][:60]})" for x in d.get("overweight") or [])
        under = ", ".join(x["name"] for x in d.get("underweight") or [])
        lines.append(f"- 섹터 {_who('sector')}: 비중 확대 {over or '없음'}" + (f" · 축소 {under}" if under else ""))
    if t["ideas"]:
        d = t["ideas"]["data"]
        lines.append(f"- 발굴 {_who('scout')} ({d.get('pool', 0)}종목 중 {len(d.get('picks') or [])}개): "
                     + "; ".join(f"{x.get('name') or x['symbol']} — {x.get('reason', '')[:70]}" for x in d.get("picks") or []))
    for a in t["alerts"][-3:]:
        lines.append(f"- 리스크 경보 {a['created_at'][11:16]}: {a['data'].get('text', '')}")
    return "\n".join(lines)


# ---- 화면 ----
def status(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    """운용 프로세스 — 단계마다 담당·마지막으로 한 일·한 줄 결과, 리서치 진행, 오늘 기록."""
    now = now or market.now_kst()
    day = now.date()
    t = today(conn, day)
    st = state(conn, day)
    cfg = autopilot.get_config(conn)
    dcfg = get_config(conn)
    plan = autopilot.load_plan(conn, day)
    entries = autopilot.schedule(cfg, plan)
    meetings = [e for e in entries if e["status"] == "started"]
    upcoming = next((e for e in entries if e["status"] == "pending"
                     and e["time"] >= now.strftime("%H:%M")), None) if cfg["enabled"] else None
    orders = conn.execute("SELECT COUNT(*) FROM invest_orders WHERE run_id IS NOT NULL AND substr(created_at, 1, 10) = ?",
                          (day.isoformat(),)).fetchone()[0]

    def at(rec: dict | None) -> str | None:
        return rec["created_at"][11:16] if rec else None

    m, s, i = t["market"], t["sector"], t["ideas"]
    steps = {
        "market": {"at": at(m), "text": (f"{REGIME_KO.get(m['data'].get('regime'), '중립')} · {m['data'].get('headline', '')}" if m else None),
                   "by": m["data"].get("by") if m else None},
        "sector": {"at": at(s), "text": ("확대 " + ", ".join(x["name"] for x in s["data"].get("overweight") or []) if s else None),
                   "by": s["data"].get("by") if s else None},
        "ideas": {"at": at(i), "text": (", ".join(x.get("name") or x["symbol"] for x in i["data"].get("picks") or []) if i else None),
                  "by": i["data"].get("by") if i else None},
        "committee": {"at": meetings[-1]["time"] if meetings else None,
                      "text": ("상시 운용 — 정기 회의 없이 장 내내 대화" if cfg["mode"] == "continuous" else
                               f"오늘 {len(meetings)}번" + (f" · 다음 {upcoming['time']}" if upcoming else "")
                               + (" · 안건이 있을 때만" if cfg["flexible"] else " · 정기 시각마다")), "by": None},
        "execution": {"at": None, "text": f"오늘 AI 주문 {orders}건" + (" · 언제든" if cfg["mode"] == "continuous" else ""), "by": None},
        "risk": {"at": (st["last_watch"] or "")[:5] or None,
                 "text": ("감시 꺼짐" if not dcfg["watch"] else f"{dcfg['watch_min']}분마다 · 오늘 경보 {len(t['alerts'])}건"
                          if cfg["enabled"] else "자동 운용이 켜지면 장 중에 살펴요"),
                 "by": None},
    }
    running = research_running(conn)
    return {"enabled": cfg["enabled"], "flexible": cfg["flexible"],
            "people": [{**person(k), "role": PEOPLE[k]["role"]} for k in PEOPLE],
            "duties": {**DUTIES, "risk": (f"장 중 {dcfg['watch_min']}분마다 보유·발굴 종목을 살피고, 급변하면 "
                                          + ("수시 회의를 열어요" if dcfg["adhoc"] else "알려요") if dcfg["watch"] else "리스크 감시는 꺼 뒀어요")},
            "process": [{"key": k, "title": title, "who": who, **steps[k]} for k, title, who in PROCESS],
            "running": {"id": running["id"], "name": running["name"]} if running else None,
            "research": st["research"], "alerts": [{"at": a["created_at"][11:16], **a["data"]} for a in t["alerts"]],
            "market": m["data"] if m else None, "sector": s["data"] if s else None, "ideas": i["data"] if i else None,
            "config": dcfg, "bounds": BOUNDS,
            "limits": {"prep_min": dcfg["prep_min"], "watch_min": dcfg["watch_min"], "refresh_move": REFRESH_MOVE, "max_refresh": MAX_REFRESH,
                       "max_adhoc": autopilot.MAX_ADHOC}}
