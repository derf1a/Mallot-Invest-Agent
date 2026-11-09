"""앱 조립: 시작할 때 .env · 마이그레이션 · 멈춘 실행 정리, 기능 패키지 자동 등록, 빌드된 화면 서빙."""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import registry
from app.core import envfile, metrics
from app.core.db import connect, init_db
from app.llm import local
from app.runs.runner import recover_interrupted

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    envfile.load()  # .env (API 키 등) — 셸 환경 변수가 우선
    init_db()
    conn = connect()
    try:
        if stopped := recover_interrupted(conn):
            log.info("서버가 꺼질 때 돌던 실행 %d건을 실패로 정리했어요", stopped)
    finally:
        conn.close()
    services = registry.services()  # 기능 패키지의 service.py (예: 자동 운용 일정)
    for s in services:
        s.start()
    yield
    for s in services:
        s.stop()
    local.unload()  # 로컬 LLM 작업 프로세스가 모델(수 GB)을 쥔 채 남지 않게


app = FastAPI(title="Mallot Invest Agent", lifespan=lifespan)


@app.middleware("http")
async def request_metrics(request: Request, call_next):
    """API 요청 통계. 경로 템플릿 단위로 묶고, 정적 파일·화면은 세지 않는다."""
    started = time.perf_counter()
    status = 500  # 처리되지 않은 예외도 서버 오류로 센다
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        if request.url.path.startswith("/api/"):
            route = request.scope.get("route")
            metrics.observe(request.method, getattr(route, "path", request.url.path), status,
                            (time.perf_counter() - started) * 1000)


@app.get("/api/health")
def health() -> dict:
    conn = connect()
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    return {"ok": True, "db_version": version}


# 기능 패키지(app/<기능>/)의 api*.py 라우터와 jobs 의 작업을 자동으로 붙인다 (기능을 더해도 main 은 그대로)
for _router in registry.routers():
    app.include_router(_router)
registry.load_jobs()


# 빌드된 React 앱을 같은 URL에서 서빙한다 (frontend/dist 가 있을 때만).
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        file = FRONTEND_DIST / path
        if path and file.is_file() and FRONTEND_DIST in file.resolve().parents:
            return FileResponse(file)
        return FileResponse(FRONTEND_DIST / "index.html")
