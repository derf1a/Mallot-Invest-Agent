"""접속 기록 — IP 마다 하루 묶음(나라 · 역할 · 요청 수 · 처음 · 마지막 · 마지막 경로 · 브라우저)과 로그인 사건, 한국 밖 접속 경보.

- IP · 나라: Cloudflare 터널로 온 요청은 CF-Connecting-IP · CF-IPCountry(나라 코드, Cloudflare 가 붙인다)를 쓴다. 서버는
  127.0.0.1 에서만 듣기 때문에 바깥 요청은 언제나 터널을 지난다. 헤더가 없으면 이 컴퓨터(local).
- 요청 기록: API 요청만, (날짜, IP) 한 줄에 더한다 — 화면이 몇 초마다 다시 불러도 줄이 늘지 않는다. /api/health 는 세지 않는다.
- 사건: 로그인 성공 · 실패, 손님 입장(성공 · 실패), 로그아웃 — 한 건씩.
- 경보: 나라가 KR 이 아니면(알 수 없음 XX · Tor T1 포함) (날짜, IP)마다 한 번 경보를 남기고 서버 로그에 경고를 쓴다. 관리자 화면 위에
  띠로 뜨고 '확인'하면 사라진다. 이 컴퓨터(local)는 경보하지 않는다.
- ACCESS_DAYS(90일)보다 오래된 기록은 하루 한 번 지운다. 기록이 실패해도 요청은 그대로 처리한다.
"""

import logging
import sqlite3
import threading
from datetime import date, datetime, timedelta

from fastapi import Request

from app.core.db import connect

ACCESS_DAYS = 90
HOME_COUNTRY = "KR"
SKIP = ("/api/health",)
log = logging.getLogger("uvicorn.error")
_lock = threading.Lock()
_pruned: dict = {"day": None}


def client(request: Request) -> tuple[str, str | None]:
    """(IP, 나라 코드). 터널을 거치지 않은 요청은 ('local', None)."""
    h = request.headers
    ip = (h.get("cf-connecting-ip") or "").strip()
    if not ip:
        fwd = (h.get("x-forwarded-for") or "").split(",")[0].strip()
        ip = fwd
    if not ip:
        return "local", None
    country = (h.get("cf-ipcountry") or "").strip().upper() or None
    return ip[:64], country


def foreign(country: str | None) -> bool:
    return country is not None and country != HOME_COUNTRY


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _alert(conn: sqlite3.Connection, day: str, ip: str, country: str | None, why: str) -> None:
    cur = conn.execute("INSERT OR IGNORE INTO access_alerts (day, ip, country, reason) VALUES (?, ?, ?, ?)", (day, ip, country, why))
    if cur.rowcount:
        log.warning("한국 밖 접속 감지: %s (%s) — %s", ip, country, why)


def _prune(conn: sqlite3.Connection, today: str) -> None:
    if _pruned["day"] == today:
        return
    cut = (date.fromisoformat(today) - timedelta(days=ACCESS_DAYS)).isoformat()
    for t in ("access_ips", "access_events", "access_alerts"):
        conn.execute(f"DELETE FROM {t} WHERE day < ?", (cut,))
    _pruned["day"] = today


def record_request(request: Request, status: int) -> None:
    path = request.url.path
    if not path.startswith("/api/") or path in SKIP:
        return
    ip, country = client(request)
    role = getattr(request.state, "role", None) or "none"
    day, now = date.today().isoformat(), _now()
    ua = (request.headers.get("user-agent") or "")[:200]
    try:
        with _lock:
            conn = connect()
            try:
                conn.execute(
                    "INSERT INTO access_ips (day, ip, country, roles, requests, denied, first_at, last_at, last_path, user_agent) "
                    "VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?) ON CONFLICT(day, ip) DO UPDATE SET requests = requests + 1, "
                    "denied = denied + excluded.denied, last_at = excluded.last_at, last_path = excluded.last_path, "
                    "user_agent = excluded.user_agent, country = COALESCE(excluded.country, country), "
                    "roles = CASE WHEN instr(',' || roles || ',', ',' || excluded.roles || ',') THEN roles "
                    "ELSE roles || ',' || excluded.roles END",
                    (day, ip, country, role, 1 if status in (401, 403, 429) else 0, now, now, f"{request.method} {path}"[:200], ua))
                if foreign(country):
                    _alert(conn, day, ip, country, f"{role} · {request.method} {path} → {status}")
                _prune(conn, day)
                conn.commit()
            finally:
                conn.close()
    except sqlite3.Error:
        log.exception("접속 기록 실패")


def event(request: Request, kind: str, ok: bool, note: str = "") -> None:
    """로그인 · 손님 입장 · 로그아웃 한 건."""
    ip, country = client(request)
    day = date.today().isoformat()
    try:
        with _lock:
            conn = connect()
            try:
                conn.execute("INSERT INTO access_events (day, at, ip, country, kind, ok, note, user_agent) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             (day, _now(), ip, country, kind, int(ok), note[:200], (request.headers.get("user-agent") or "")[:200]))
                if foreign(country):
                    _alert(conn, day, ip, country, f"{KIND_KO.get(kind, kind)} {'성공' if ok else '실패'}")
                conn.commit()
            finally:
                conn.close()
    except sqlite3.Error:
        log.exception("접속 사건 기록 실패")


KIND_KO = {"login": "관리자 로그인", "share_enter": "손님 입장", "logout": "로그아웃"}


async def middleware(request: Request, call_next):
    """맨 바깥 미들웨어 — 응답이 정해진 뒤 기록한다 (거절된 요청도)."""
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        record_request(request, status)


# ---- 관리자 화면 ----
def view(conn: sqlite3.Connection, days: int = 14) -> dict:
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    ips = [dict(r) for r in conn.execute("SELECT * FROM access_ips WHERE day >= ? ORDER BY last_at DESC LIMIT 300", (since,))]
    events = [dict(r) for r in conn.execute("SELECT * FROM access_events WHERE day >= ? ORDER BY id DESC LIMIT 200", (since,))]
    alerts = [dict(r) for r in conn.execute("SELECT * FROM access_alerts ORDER BY acked_at IS NOT NULL, id DESC LIMIT 100")]
    for e in events:
        e["kind_ko"] = KIND_KO.get(e["kind"], e["kind"])
        e["foreign"] = foreign(e["country"])
    for r in ips:
        r["foreign"] = foreign(r["country"])
    return {"days": days, "home_country": HOME_COUNTRY, "ips": ips, "events": events, "alerts": alerts,
            "open_alerts": sum(1 for a in alerts if not a["acked_at"]),
            "note": "IP · 나라는 Cloudflare 가 붙인 값(CF-Connecting-IP · CF-IPCountry)이에요. local = 이 서버 안에서 온 요청"}


def open_alerts(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM access_alerts WHERE acked_at IS NULL ORDER BY id DESC LIMIT 20")]


def ack(conn: sqlite3.Connection, alert_id: int | None = None) -> int:
    where, args = ("id = ?", (alert_id,)) if alert_id else ("1 = 1", ())
    cur = conn.execute(f"UPDATE access_alerts SET acked_at = ? WHERE acked_at IS NULL AND {where}", (_now(), *args))
    conn.commit()
    return cur.rowcount
