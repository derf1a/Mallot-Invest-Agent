"""호출 추적 API — 트레이스 목록 · 한 트레이스(span 트리) · 에이전트별 통계 · LangSmith 내보내기 상태."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.db import get_conn
from app.tracing import langsmith, store

router = APIRouter(prefix="/api/traces", tags=["traces"])
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


@router.get("")
def list_traces(conn: Conn, limit: Annotated[int, Query(le=500)] = 50, run_id: int | None = None, agent: str | None = None,
                before_ms: float | None = None) -> list[dict]:
    return store.traces(conn, limit=limit, run_id=run_id, agent=agent or None, before_ms=before_ms)


@router.get("/stats/agents")
def agents(conn: Conn, days: Annotated[int, Query(ge=1, le=90)] = 7) -> list[dict]:
    return store.agent_stats(conn, days)


@router.get("/export/langsmith")
def langsmith_status() -> dict:
    return langsmith.status()


@router.get("/{trace_id}")
def read_trace(trace_id: str, conn: Conn) -> dict:
    out = store.trace(conn, trace_id)
    if out is None:
        raise HTTPException(404, "트레이스를 찾을 수 없어요")
    return out
