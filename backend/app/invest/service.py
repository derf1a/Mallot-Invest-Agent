"""백그라운드 서비스 — INTERVAL 초마다 운용 데스크(desk_agents.tick: 아침 리서치·리스크 감시·시장 급변 때 리서치), 자동 운용 일정
(autopilot.tick — 회의할 안건이 있는지는 desk.agenda), 아침 브리핑(morning.tick)을 차례로 확인한다. 데스크가 먼저라 리서치를 시작하면
같은 확인에서 회의가 그 결과를 기다린다.
app.registry 가 서버 시작·종료 때 start·stop.

환경변수 MIA_SERVICES=0 이면 띄우지 않는다(테스트). 확인할 때마다 DB 연결을 새로 열고 닫는다.
"""

import logging
import os
import threading

from app.core.db import connect
from app.invest import autopilot, desk, desk_agents, morning

INTERVAL = 20
log = logging.getLogger("uvicorn.error")
_stop = threading.Event()
_thread: threading.Thread | None = None


def _loop() -> None:
    while not _stop.wait(INTERVAL):
        conn = connect()
        try:
            did = desk_agents.tick(conn)
            if did:
                log.info("운용 데스크 %s", did)
        except Exception:  # noqa: BLE001
            log.exception("운용 데스크 확인 중 오류")
        try:
            rec = autopilot.tick(conn, agenda=desk.agenda)
            if rec:
                log.info("자동 운용 %s %s%s", rec["time"], rec["status"], f" — {rec['note']}" if rec.get("note") else "")
        except Exception:  # noqa: BLE001 — 서비스는 멈추지 않고 다음 확인 때 다시 본다
            log.exception("자동 운용 확인 중 오류")
        try:
            sent = morning.tick(conn)
            if sent:
                log.info("아침 브리핑 %s", sent.get("skipped") or "보냄")
        except Exception:  # noqa: BLE001
            log.exception("아침 브리핑 중 오류")
        finally:
            conn.close()


def start() -> None:
    global _thread
    if os.environ.get("MIA_SERVICES") == "0" or (_thread and _thread.is_alive()):
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="autopilot", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
    if _thread:
        _thread.join(timeout=5)
