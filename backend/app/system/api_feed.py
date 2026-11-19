"""Mallot 홈 통합 대시보드용 요약 — 로직은 system/feed, 열쇠 검사는 auth/guard."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.db import get_conn
from app.system import feed

router = APIRouter(prefix="/api/feed", tags=["feed"])
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


@router.get("/resources")
def resources(conn: Conn) -> dict:
    return feed.resources(conn)


@router.get("/summary")
def summary(conn: Conn) -> dict:
    return feed.summary(conn)


@router.get("/health")
def health(conn: Conn) -> dict:
    return feed.health(conn)
