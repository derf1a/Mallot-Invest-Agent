"""손님 화면 데이터 — 읽기만. 운용 지시 글 · 비서와 나눈 대화 · 설정 · 키는 넣지 않는다.

- status: 자동 운용(켜짐 · 방식 · 오늘 일정 · 다음 판단 · 막힌 이유), 오늘 운용 데스크(시장 분석 · 섹터 · 발굴 · 경보)
- report: 지금 한 페이지 보고서(briefing.snapshot) — 손님이 여러 번 열어도 토스 API 를 두드리지 않게 60초 캐시
- reports: 저장된 보고서 카드(비서 보고서 · 아침 브리핑)
- sessions · messages: 회의(토론 세션) 목록 · 회의록 요약 · 주문, 회의방 대화(읽기만)
"""

import json
import sqlite3
import threading
import time

from app.core.db import data_dir
from app.invest.jobs.agent_action import read_messages
from app.invest.marketdata import market
from app.invest.ops import autopilot, briefing, desk
from app.runs.runner import run_dict

REPORT_TTL = 60
_cache: dict = {"at": 0.0, "data": None}
_cache_lock = threading.Lock()


def _desk(conn: sqlite3.Connection) -> dict:
    out = {}
    for stage in ("market", "sector", "ideas"):
        row = desk.latest(conn, stage)
        out[stage] = {"day": row["day"], "at": row["created_at"], "data": row["data"]} if row else None
    out["alerts"] = [{"at": a.get("created_at"), "text": a["data"].get("text", "")}
                     for a in desk.alerts(conn, market.now_kst().date())]
    out["people"] = {k: {x: p.get(x) for x in ("key", "title", "name", "full_name", "country")} for k, p in desk.PEOPLE.items()}
    return out


def status(conn: sqlite3.Connection) -> dict:
    st = autopilot.status(conn)
    cfg = st["config"]
    return {
        "autopilot": {"enabled": cfg.get("enabled"), "mode": cfg.get("mode"), "slots": cfg.get("slots"),
                      "flexible": cfg.get("flexible"), "today": st["today"], "window": st["window"],
                      "window_note": st["window_note"], "next": st["next"], "blocked": st["blocked"],
                      "intents": len(st["intents"])},
        "desk": _desk(conn),
        "latest": (sessions(conn, 1) or [None])[0],
    }


def report(conn: sqlite3.Connection) -> dict:
    with _cache_lock:
        if _cache["data"] is not None and time.time() - _cache["at"] < REPORT_TTL:
            return _cache["data"]
    data = briefing.snapshot(conn)
    with _cache_lock:
        _cache.update(at=time.time(), data=data)
    return data


def clear_cache() -> None:
    with _cache_lock:
        _cache.update(at=0.0, data=None)


def reports(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = conn.execute("SELECT id, created_at, text, meta FROM invest_assistant_messages "
                        "WHERE json_extract(meta, '$.kind') = 'report' ORDER BY id DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        meta = json.loads(r["meta"] or "{}")
        out.append({"id": r["id"], "created_at": r["created_at"], "title": r["text"], "morning": bool(meta.get("morning")),
                    "report": meta.get("report"), "body": meta.get("body") or ""})
    return out


def sessions(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute("SELECT r.*, NULL AS dataset_name, NULL AS parent_name FROM runs r WHERE kind = 'agent_action' "
                        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    runs = [run_dict(r) for r in rows]
    ids = [r["id"] for r in runs]
    orders: dict[int, list[dict]] = {}
    if ids:
        for o in conn.execute("SELECT run_id, symbol, name, side, quantity, price, status, created_at FROM invest_orders "
                              f"WHERE run_id IN ({','.join('?' * len(ids))}) ORDER BY id", ids):
            orders.setdefault(o["run_id"], []).append(dict(o))
    out = []
    for r in runs:
        summary = r.get("summary") or {}
        status = "interrupted" if summary.get("interrupted") else r["status"]  # 서버 재시작으로 끊김 — 실패와 가른다
        out.append({"id": r["id"], "name": r["name"], "status": status, "created_at": r["created_at"],
                    "finished_at": r["finished_at"], "scope": (r.get("params") or {}).get("scope"),
                    "autopilot": bool((r.get("params") or {}).get("autopilot")), "minutes": summary.get("minutes"),
                    "gate": summary.get("gate"), "decision": summary.get("decision"), "orders": orders.get(r["id"], [])})
    return out


def messages(conn: sqlite3.Connection, run_id: int, after: int = 0) -> list[dict] | None:
    row = conn.execute("SELECT artifact_path FROM runs WHERE id = ? AND kind = 'agent_action'", (run_id,)).fetchone()
    if row is None:
        return None
    return read_messages(data_dir() / row["artifact_path"], after) if row["artifact_path"] else []
