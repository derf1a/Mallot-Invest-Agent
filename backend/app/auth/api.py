"""로그인 · 로그아웃 · 내 역할 · 손님 입장 · 공유 링크 관리 (공유 관리는 관리자만 — guard 가 검사)."""

import hmac
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.auth import security
from app.core.db import get_conn

router = APIRouter(prefix="/api/auth", tags=["auth"])
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


class LoginIn(BaseModel):
    password: str


class EnterIn(BaseModel):
    key: str


class ShareIn(BaseModel):
    enabled: bool | None = None
    rotate: bool = False


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def _set(response: Response, request: Request, name: str, value: str, ttl: int) -> None:
    response.set_cookie(name, value, max_age=ttl, httponly=True, samesite="strict", secure=_secure(request), path="/")


@router.get("/me")
def me(request: Request, conn: Conn) -> dict:
    on = security.enabled()
    role = security.role_of(request.cookies) if on else "admin"
    return {"auth": on, "public": security.public(), "has_password": security.has_password(), "role": role,
            "share_enabled": security.share(conn)["enabled"]}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response) -> dict:
    if not security.has_password():
        raise HTTPException(403, "관리자 비밀번호가 정해지지 않았어요 (.env 의 MIA_ADMIN_PASSWORD_HASH)")
    try:
        ok = security.login(body.password)
    except security.Locked as e:
        raise HTTPException(429, str(e)) from None
    if not ok:
        raise HTTPException(401, "비밀번호가 맞지 않아요")
    _set(response, request, security.ADMIN_COOKIE, security.issue("admin"), security.ADMIN_TTL)
    return {"role": "admin"}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(security.ADMIN_COOKIE, path="/")
    response.delete_cookie(security.VIEWER_COOKIE, path="/")
    return {"role": None}


@router.post("/share/enter")
def enter(body: EnterIn, request: Request, response: Response, conn: Conn) -> dict:
    s = security.share(conn)
    if not s["enabled"] or not hmac.compare_digest(body.key.encode(), s["key"].encode()):
        raise HTTPException(403, "공유 링크가 맞지 않거나 공유가 꺼져 있어요")
    _set(response, request, security.VIEWER_COOKIE, security.issue("viewer", conn), security.VIEWER_TTL)
    return {"role": "viewer"}


def _share_view(s: dict) -> dict:
    return {"enabled": s["enabled"], "key": s["key"], "path": f"/share?k={s['key']}", "rotated_at": s.get("rotated_at")}


@router.get("/share")
def read_share(conn: Conn) -> dict:
    return _share_view(security.share(conn))


@router.put("/share")
def write_share(body: ShareIn, conn: Conn) -> dict:
    """켜기 · 끄기 · 새 키(이전 링크와 손님 쿠키가 모두 끊긴다)."""
    return _share_view(security.set_share(conn, enabled=body.enabled, rotate=body.rotate))
