"""시스템 이슈 API (/api/system) — 설정 > 시스템 이슈 화면. 읽기는 손님도, 정리 · 해결 · 다시 열기는 관리자만(guard)."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.db import get_conn
from app.system import agent, github, issues

router = APIRouter(prefix="/api/system", tags=["system"])
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def _overview(conn: sqlite3.Connection, status: str | None) -> dict:
    return {"counts": issues.counts(conn), "issues": issues.listing(conn, status), "answers": issues.answers(conn),
            "pending": [{k: r[k] for k in ("id", "at", "by", "text", "seen")} for r in issues.pending(conn, 50)],
            "github": {"configured": github.configured(), "repo": github.repo(), "token_env": github.TOKEN_ENV},
            "admin": {k: issues.PROFILE[k] for k in ("key", "title", "name", "full_name", "country", "pokemon", "role")}}


@router.get("/issues")
def read_issues(conn: Conn, status: Annotated[str | None, Query(pattern="^(open|resolved)$")] = None) -> dict:
    """요약 수 · 이슈 목록 · 시스템 관리자의 답 · 대기 요청 · GitHub 연결 여부."""
    return _overview(conn, status)


@router.get("/issues/{issue_id}")
def read_issue(issue_id: int, conn: Conn) -> dict:
    """이슈 하나 — 본문 · 이어진 요청 · 사건 기록."""
    out = issues.detail(conn, issue_id)
    if out is None:
        raise HTTPException(404, "없는 이슈예요")
    return out


@router.post("/triage")
def run_triage(conn: Conn) -> dict:
    """지금 정리 — 대기 요청 정리 → GitHub 올리기 → 열림 · 닫힘 맞추기. 이미 돌고 있으면 409."""
    out = agent.run(conn)
    if out.get("busy"):
        raise HTTPException(409, "시스템 관리자가 이미 정리하고 있어요")
    return {"result": out, **_overview(conn, None)}


class ResolveIn(BaseModel):
    text: str = Field(default="", max_length=500)


@router.post("/issues/{issue_id}/resolve")
def resolve_issue(issue_id: int, body: ResolveIn, conn: Conn) -> dict:
    """관리자가 해결로 닫는다 (GitHub 이슈는 따로 — 다음 맞추기 때 GitHub 가 열려 있으면 다시 열린다)."""
    if issues.get(conn, issue_id) is None:
        raise HTTPException(404, "없는 이슈예요")
    issues.resolve(conn, issue_id, body.text.strip() or "관리자가 해결로 닫음", "관리자")
    return issues.detail(conn, issue_id)


@router.post("/issues/{issue_id}/reopen")
def reopen_issue(issue_id: int, body: ResolveIn, conn: Conn) -> dict:
    if issues.get(conn, issue_id) is None:
        raise HTTPException(404, "없는 이슈예요")
    issues.reopen(conn, issue_id, body.text.strip() or "관리자가 다시 엶", "관리자")
    return issues.detail(conn, issue_id)
