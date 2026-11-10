"""시스템 관리자 서비스 — TRIAGE_SEC 마다 대기 요청이 있으면 정리하고(agent.run), SYNC_SEC 마다 GitHub 이슈 열림·닫힘을 맞춘다.
대기 요청이 없으면 LLM 을 부르지 않는다. 환경변수 MIA_SERVICES=0 이면 띄우지 않는다(테스트). app.registry 가 시작·종료 때 부른다.
"""

import logging
import os
import threading
import time

from app.core.db import connect
from app.system import agent, issues

TRIAGE_SEC = 600
SYNC_SEC = 1800
FIRST_SEC = 60  # 서버가 뜬 뒤 첫 확인까지
log = logging.getLogger("uvicorn.error")
_stop = threading.Event()
_thread: threading.Thread | None = None


def _loop() -> None:
    last_sync = 0.0
    wait = FIRST_SEC
    while not _stop.wait(wait):
        wait = TRIAGE_SEC
        conn = connect()
        try:
            due = time.monotonic() - last_sync >= SYNC_SEC
            if not issues.counts(conn)["pending"] and not issues.unpublished(conn) and not due:
                continue
            out = agent.run(conn, sync_github=due)
            if due:
                last_sync = time.monotonic()
            log.info("시스템 관리자 %s", out)
        except Exception:  # noqa: BLE001 — 다음 확인 때 다시
            log.exception("시스템 관리자 정리 중 오류")
        finally:
            conn.close()


def start() -> None:
    global _thread
    if os.environ.get("MIA_SERVICES") == "0" or (_thread and _thread.is_alive()):
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="system-admin", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
    if _thread:
        _thread.join(timeout=5)
