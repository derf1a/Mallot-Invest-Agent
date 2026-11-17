"""경로 접두사 MIA_BASE_PATH (예: /invest) — 한 도메인의 경로(<도메인>/invest/)로 열 때 앞에서 뗀다.

- Mallot 앱들을 같은 origin 의 경로로 모은다 (iOS 홈 화면 앱이 다른 origin 으로 갈 때 Safari 창을 띄우지 않게).
  cloudflared 는 경로를 떼지 않고 그대로 보내므로 앱이 뗀다.
- 맨 바깥 순수 ASGI 미들웨어라 guard · 접속 기록 · 라우터는 모두 뗀 경로를 본다.
  - 경로가 base 와 같으면 base + "/" 로 307 (쿼리 유지).
  - base + "/" 로 시작하면 접두사를 떼고 raw_path 도 맞춘다.
  - 접두사가 없으면 그대로 — 127.0.0.1:포트로 직접 여는 요청과 홈의 /api/feed/* 호출.
- 비어 있으면(기본 · 개발 · 테스트) 아무것도 하지 않는다. 값은 요청마다 읽는다 (.env 는 시작할 때 늦게 읽혀서).
"""

import os

ENV = "MIA_BASE_PATH"


def base_path() -> str:
    """'/invest' 모양으로 맞춘 값. 없거나 '/' 이면 ''."""
    raw = os.environ.get(ENV, "").strip().strip("/")
    return f"/{raw}" if raw else ""


class BasePathMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        base = base_path()
        if not base or scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path: str = scope["path"]
        if path == base:
            if scope["type"] == "http":
                return await _redirect(scope, send, base + "/")
            return await self.app(scope, receive, send)  # websocket 은 옮길 수 없다 — 그대로 (라우트가 없어 닫힌다)
        if path.startswith(base + "/"):
            scope = dict(scope)
            scope["path"] = path[len(base):]
            raw = scope.get("raw_path")
            prefix = base.encode()
            scope["raw_path"] = raw[len(prefix):] if raw and raw.startswith(prefix + b"/") else scope["path"].encode()
        return await self.app(scope, receive, send)


async def _redirect(scope, send, location: str) -> None:
    if query := scope.get("query_string", b""):
        location += "?" + query.decode("latin-1")
    await send({"type": "http.response.start", "status": 307,
                "headers": [(b"location", location.encode("latin-1")), (b"content-length", b"0")]})
    await send({"type": "http.response.body", "body": b""})
