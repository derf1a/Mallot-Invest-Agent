"""손님 API (/api/share) — 읽기만. guard 가 손님 · 관리자만 들인다."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.db import get_conn
from app.share import views

router = APIRouter(prefix="/api/share", tags=["share"])
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


@router.get("/status")
def read_status(conn: Conn) -> dict:
    return views.status(conn)


@router.get("/report")
def read_report(conn: Conn) -> dict:
    return views.report(conn)


@router.get("/reports")
def read_reports(conn: Conn, limit: Annotated[int, Query(ge=1, le=30)] = 10) -> list[dict]:
    return views.reports(conn, limit)


@router.get("/sessions")
def read_sessions(conn: Conn, limit: Annotated[int, Query(ge=1, le=50)] = 20) -> list[dict]:
    return views.sessions(conn, limit)


@router.get("/sessions/{run_id}/messages")
def read_messages(run_id: int, conn: Conn, after: int = 0) -> dict:
    msgs = views.messages(conn, run_id, after)
    if msgs is None:
        raise HTTPException(404, "회의를 찾을 수 없어요")
    return {"messages": msgs}
