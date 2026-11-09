"""요청마다 역할을 검사하는 미들웨어 (main.py 가 붙인다).

- 권한이 꺼져 있으면(비밀번호 없음 · 공개 아님) 지금처럼 열려 있다. 단, 터널 · 프록시를 거친 API 요청은 막는다.
- 켜져 있으면: 로그인 · 입장 · 상태 확인은 누구나. 관리자는 모두.
  손님(공유 링크)은 관리자 화면을 읽기만 한다 — GET 은 열고, 쓰기(POST · PUT · DELETE)는 모두 403.
  손님에게도 닫는 GET: 공유 링크 · 키(/api/auth/share), /docs. 설정의 키 값은 라우터가 빼고 보낸다 (core/access.is_viewer).
- 쓰기 요청(POST · PUT · DELETE)은 Origin 이 있으면 이 사이트여야 한다 (다른 사이트에서 몰래 보내는 요청 막기).
- 역할은 request.state.role 에 넣는다 (라우터가 손님용으로 값을 줄일 때).
"""

from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

from app.auth import security

OPEN_API = {"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/me", "/api/auth/share/enter"}
DOC_PATHS = ("/docs", "/redoc", "/openapi.json")
VIEWER_CLOSED = ("/api/auth/share", *DOC_PATHS)  # 손님에게는 읽기도 닫는다
READ_METHODS = ("GET", "HEAD", "OPTIONS")


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
        request.state.role = "admin"
        return await call_next(request)
    reading = request.method in READ_METHODS
    if not reading and not _same_origin(request):
        return _deny(403, "다른 사이트에서 온 요청이에요")
    if path in OPEN_API:
        return await call_next(request)
    role = security.role_of(request.cookies)
    request.state.role = role
    if role == "admin":
        return await call_next(request)
    if role != "viewer":
        return _deny(401, "로그인하거나 공유 링크로 들어와 주세요")
    if not reading:
        return _deny(403, "읽기 전용이에요 — 손님은 설정 · 주문 · 대화를 바꿀 수 없어요")
    if path.startswith(VIEWER_CLOSED):
        return _deny(403, "관리자만 볼 수 있어요")
    return await call_next(request)
