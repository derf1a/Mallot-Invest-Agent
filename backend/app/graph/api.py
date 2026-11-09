"""에이전트 · 구조 그래프 API — 에이전트 설정 보기, 흐름 그래프, 실제 트레이스의 관찰 그래프, LangGraph 내보내기."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.core.db import get_conn
from app.graph import catalog, langgraph_export, observed, topology

router = APIRouter(prefix="/api/graph", tags=["graph"])
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


@router.get("/agents")
def list_agents(conn: Conn) -> list[dict]:
    return catalog.all_agents(conn)


@router.get("/agents/{key}")
def read_agent(key: str, conn: Conn) -> dict:
    a = catalog.one(conn, key)
    if a is None:
        raise HTTPException(404, "그런 에이전트가 없어요")
    return a


@router.get("/flows")
def list_flows(conn: Conn) -> list[dict]:
    return topology.flows(conn)


@router.get("/flows/{key}")
def read_flow(key: str, conn: Conn) -> dict:
    f = topology.flow(conn, key)
    if f is None:
        raise HTTPException(404, "그런 흐름이 없어요")
    return f


@router.get("/observed/{trace_id}")
def read_observed(trace_id: str, conn: Conn) -> dict:
    g = observed.build(conn, trace_id)
    if g is None:
        raise HTTPException(404, "트레이스를 찾을 수 없어요")
    return g


@router.get("/langgraph/{key}")
def read_langgraph(key: str, conn: Conn) -> dict:
    out = langgraph_export.export(conn, key)
    if out is None:
        raise HTTPException(404, "그런 흐름이 없어요")
    return out
