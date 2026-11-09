"""서버 시작 · 종료 때 LangSmith 내보내기를 걸고 푼다 (켜는 조건은 app/tracing/langsmith.py)."""

import logging

from app.tracing import langsmith

log = logging.getLogger("uvicorn.error")


def start() -> None:
    if langsmith.start():
        log.info("트레이스를 LangSmith 프로젝트 '%s' 로 내보내요", langsmith.project())


def stop() -> None:
    langsmith.stop()
