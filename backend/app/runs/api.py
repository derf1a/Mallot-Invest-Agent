"""실행(run) 조회 · 취소 · 삭제. 실행을 새로 만드는 일은 각 기능의 엔드포인트가 한다
(예: 에이전트 토론은 주문 PIN 을 확인하는 /api/invest/agent-sessions — 여기서 아무 작업이나 시작할 수 없게)."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.db import get_conn
from app.runs.runner import REGISTRY, load_run, run_dict

router = APIRouter(prefix="/api/runs", tags=["runs"])
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def _run(conn: sqlite3.Connection, run_id: int) -> dict:
    run = load_run(conn, run_id)
    if run is None:
        raise HTTPException(404, "실행을 찾을 수 없습니다")
    return run


@router.get("/jobs")
def list_jobs() -> list[dict]:
    return [{"kind": s.kind, "title": s.title, "description": s.description, "defaults": s.defaults,
             "uses_llm": s.uses_llm} for s in REGISTRY.values()]


@router.get("")
def list_runs(conn: Conn, kind: str | None = None, limit: Annotated[int, Query(le=500)] = 50) -> list[dict]:
    sql = "SELECT r.*, NULL AS dataset_name, p.name AS parent_name FROM runs r LEFT JOIN runs p ON p.id = r.parent_id"
    args: list = []
    if kind:
        sql += " WHERE r.kind = ?"
        args.append(kind)
    return [run_dict(r) for r in conn.execute(sql + " ORDER BY r.id DESC LIMIT ?", (*args, limit))]


@router.get("/{run_id}")
def read_run(run_id: int, conn: Conn) -> dict:
    return _run(conn, run_id)


@router.get("/{run_id}/metrics")
def read_metrics(run_id: int, conn: Conn) -> dict[str, list[list[float]]]:
    """지표별 [[step, value], ...]"""
    _run(conn, run_id)
    out: dict[str, list[list[float]]] = {}
    for r in conn.execute("SELECT key, step, value FROM run_metrics WHERE run_id = ? ORDER BY key, step", (run_id,)):
        out.setdefault(r["key"], []).append([r["step"], r["value"]])
    return out


@router.get("/{run_id}/logs")
def read_logs(run_id: int, conn: Conn, after_id: int = 0, limit: Annotated[int, Query(le=2000)] = 500) -> list[dict]:
    _run(conn, run_id)
    rows = conn.execute("SELECT * FROM run_logs WHERE run_id = ? AND id > ? ORDER BY id LIMIT ?", (run_id, after_id, limit))
    return [dict(r) for r in rows]


@router.post("/{run_id}/cancel")
def cancel_run(run_id: int, conn: Conn) -> dict:
    run = _run(conn, run_id)
    if run["status"] not in ("queued", "running"):
        raise HTTPException(409, "이미 끝난 실행입니다")
    conn.execute("UPDATE runs SET cancel_requested = 1 WHERE id = ?", (run_id,))
    conn.commit()
    return _run(conn, run_id)


@router.delete("/{run_id}", status_code=204)
def delete_run(run_id: int, conn: Conn) -> None:
    if _run(conn, run_id)["status"] in ("queued", "running"):
        raise HTTPException(409, "실행 중에는 삭제할 수 없습니다. 먼저 취소하세요")
    # 에이전트 세션이 만든 주문은 그 토론이 근거다 — 실계좌 주문 기록이 남아 있으면 지우지 않는다
    if conn.execute("SELECT 1 FROM invest_orders WHERE run_id = ? LIMIT 1", (run_id,)).fetchone():
        raise HTTPException(409, "이 세션에서 만든 주문이 있어 삭제할 수 없어요 (주문의 근거가 사라짐)")
    conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
    conn.commit()
