"""로그인 · 로그아웃 · 내 역할 · 손님 입장 · 공유 링크 관리 (공유 관리는 관리자만 — guard 가 검사)."""

import hmac
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from app.auth import access_log, security
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
    if security.home_mode():
        # 홈 모드 — 역할은 guard 가 홈에 물어 둔 값. 앞의 필드는 프론트가 깨지지 않게 같이 둔다 (앱 로그인 · 공유는 없음)
        return {"auth": True, "public": False, "has_password": False, "role": getattr(request.state, "role", None),
                "share_enabled": False, "home": True, "login_url": security.HOME_LOGIN_URL,
                "logout_url": security.HOME_LOGOUT_URL}
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
        access_log.event(request, "login", False, "잠김")
        raise HTTPException(429, str(e)) from None
    access_log.event(request, "login", ok, "" if ok else "비밀번호 틀림")
    if not ok:
        raise HTTPException(401, "비밀번호가 맞지 않아요")
    _set(response, request, security.ADMIN_COOKIE, security.issue("admin"), security.ADMIN_TTL)
    return {"role": "admin"}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    if (role := security.role_of(request.cookies)) is not None:
        access_log.event(request, "logout", True, role)
    response.delete_cookie(security.ADMIN_COOKIE, path="/")
    response.delete_cookie(security.VIEWER_COOKIE, path="/")
    return {"role": None}


@router.post("/share/enter")
def enter(body: EnterIn, request: Request, response: Response, conn: Conn) -> dict:
    s = security.share(conn)
    if not s["enabled"] or not hmac.compare_digest(body.key.encode(), s["key"].encode()):
        access_log.event(request, "share_enter", False, "공유 꺼짐" if not s["enabled"] else "키 틀림")
        raise HTTPException(403, "공유 링크가 맞지 않거나 공유가 꺼져 있어요")
    access_log.event(request, "share_enter", True)
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


# ---- 접속 기록 (관리자만 — guard.VIEWER_CLOSED) ----
@router.get("/access")
def read_access(conn: Conn, days: Annotated[int, Query(ge=1, le=access_log.ACCESS_DAYS)] = 14) -> dict:
    return access_log.view(conn, days)


@router.get("/access/alerts")
def read_alerts(conn: Conn) -> list[dict]:
    return access_log.open_alerts(conn)


class AckIn(BaseModel):
    id: int | None = None  # 비우면 모두 확인


@router.post("/access/ack")
def ack_alerts(body: AckIn, conn: Conn) -> dict:
    return {"acked": access_log.ack(conn, body.id)}
