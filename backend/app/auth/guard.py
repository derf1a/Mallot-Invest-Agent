"""요청마다 역할을 검사하는 미들웨어 (main.py 가 붙인다).

- 권한이 꺼져 있으면(비밀번호 없음 · 공개 아님) 지금처럼 열려 있다. 단, 터널 · 프록시를 거친 API 요청은 막는다.
- 켜져 있으면: /api/share/* 는 손님 · 관리자, 로그인 · 입장 · 상태 확인은 누구나, 나머지 API 와 /docs 는 관리자만.
- 쓰기 요청(POST · PUT · DELETE)은 Origin 이 있으면 이 사이트여야 한다 (다른 사이트에서 몰래 보내는 요청 막기).
"""

from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

from app.auth import security

OPEN_API = {"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/me", "/api/auth/share/enter"}
SHARE_PREFIX = "/api/share/"
DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return urlsplit(origin).netloc == host


def _deny(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


async def guard(request: Request, call_next):
    path = request.url.path
    guarded = path.startswith("/api/") or path.startswith(DOC_PATHS)
    if not guarded:
        return await call_next(request)
    if not security.enabled():
        if security.proxied(request.headers):
            return _deny(403, "관리자 비밀번호 없이 외부에서 열 수 없어요 — MIA_ADMIN_PASSWORD_HASH 를 정하세요")
        return await call_next(request)
    if request.method not in ("GET", "HEAD", "OPTIONS") and not _same_origin(request):
        return _deny(403, "다른 사이트에서 온 요청이에요")
    if path in OPEN_API:
        return await call_next(request)
    role = security.role_of(request.cookies)
    if path.startswith(SHARE_PREFIX):
        if role not in ("admin", "viewer"):
            return _deny(401, "공유 링크로 들어와 주세요")
    elif role != "admin":
        return _deny(401, "관리자 로그인이 필요해요")
    return await call_next(request)
