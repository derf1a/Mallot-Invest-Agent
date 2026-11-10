"""작업 실행기.

에이전트 토론 · 운용 데스크 리서치 · 뉴스 리포트는 모두 '실행(run)' 하나로 기록된다.
- 상태: queued → running → succeeded / failed / cancelled
- 진행률, 단계별 지표, 로그, 산출물 폴더(data/runs/<id>)
- 백그라운드 스레드 풀에서 돈다. 작업 함수는 RunContext 로 로그·지표·진행률을 남기고, 주기적으로 취소 요청을 확인한다.
- LLM을 부르는 작업은 작업 함수 안에서 asyncio.run 으로 돌린다 (스레드마다 이벤트 루프 하나).
"""

import json
import logging
import sqlite3
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from app.core import trace
from app.core.db import connect, data_dir
from app.llm.client import CURRENT_RUN

log = logging.getLogger("runs")


class Cancelled(Exception):
    """사용자가 취소한 실행."""


class JobError(Exception):
    """작업이 정상적으로 실패할 때 (사용자에게 보여줄 메시지)."""


@dataclass
class JobSpec:
    kind: str
    title: str
    description: str
    run: Callable[["RunContext", dict], dict]
    defaults: dict = field(default_factory=dict)
    uses_llm: bool = False


REGISTRY: dict[str, JobSpec] = {}


def register(spec: JobSpec) -> JobSpec:
    REGISTRY[spec.kind] = spec
    return spec


class RunContext:
    """작업 함수에 넘기는 실행 환경. 스레드마다 자기 DB 연결을 쓴다."""

    def __init__(self, run_id: int):
        self.run_id = run_id
        self.conn = connect()
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        self.parent_id: int | None = row["parent_id"]
        self._last_progress = -1.0

    # --- 기록 ---
    def log(self, message: str, level: str = "info") -> None:
        self.conn.execute("INSERT INTO run_logs (run_id, level, message) VALUES (?, ?, ?)",
                          (self.run_id, level, message))
        self.conn.commit()

    def metric(self, key: str, value: float, step: int) -> None:
        self.conn.execute("INSERT OR REPLACE INTO run_metrics (run_id, key, step, value) VALUES (?, ?, ?, ?)",
                          (self.run_id, key, step, float(value)))
        self.conn.commit()

    def metrics(self, step: int, **values: float) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO run_metrics (run_id, key, step, value) VALUES (?, ?, ?, ?)",
            [(self.run_id, k, step, float(v)) for k, v in values.items()],
        )
        self.conn.commit()

    def progress(self, p: float) -> None:
        p = max(0.0, min(1.0, p))
        if p - self._last_progress >= 0.01 or p == 1.0:  # 너무 잦은 쓰기 방지
            self._last_progress = p
            self.conn.execute("UPDATE runs SET progress = ? WHERE id = ?", (p, self.run_id))
            self.conn.commit()

    def check_cancel(self) -> None:
        row = self.conn.execute("SELECT cancel_requested FROM runs WHERE id = ?", (self.run_id,)).fetchone()
        if row and row[0]:
            raise Cancelled()

    # --- 산출물 ---
    @property
    def artifact_dir(self) -> Path:
        path = data_dir() / "runs" / str(self.run_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def set_artifact(self, path: Path) -> None:
        self.conn.execute("UPDATE runs SET artifact_path = ? WHERE id = ?",
                          (str(path.relative_to(data_dir())), self.run_id))
        self.conn.commit()

    def parent_artifact(self) -> Path | None:
        if self.parent_id is None:
            return None
        row = self.conn.execute("SELECT artifact_path, status FROM runs WHERE id = ?", (self.parent_id,)).fetchone()
        if row is None or row["status"] != "succeeded" or not row["artifact_path"]:
            raise JobError(f"부모 실행 #{self.parent_id}의 산출물이 없습니다")
        return data_dir() / row["artifact_path"]

    def close(self) -> None:
        self.conn.close()


# 테스트에서는 SYNC = True 로 두어 즉시 실행한다
SYNC = False
_executor: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="run")
    return _executor


def start_run(
    conn: sqlite3.Connection,
    kind: str,
    params: dict | None = None,
    *,
    name: str | None = None,
    parent_id: int | None = None,
) -> int:
    spec = REGISTRY.get(kind)
    if spec is None:
        raise JobError(f"알 수 없는 작업입니다: {kind}")
    merged = {**spec.defaults, **(params or {})}
    cur = conn.execute(
        "INSERT INTO runs (kind, name, params_json, parent_id) VALUES (?, ?, ?, ?)",
        (kind, name or spec.title, json.dumps(merged, ensure_ascii=False), parent_id),
    )
    conn.commit()
    run_id = cur.lastrowid
    if SYNC:
        execute(run_id)
    else:
        _pool().submit(execute, run_id)
    return run_id


def execute(run_id: int) -> None:
    ctx = RunContext(run_id)
    conn = ctx.conn
    row = conn.execute("SELECT kind, params_json, cancel_requested FROM runs WHERE id = ?", (run_id,)).fetchone()
    spec = REGISTRY[row["kind"]]
    if row["cancel_requested"]:
        _finish(conn, run_id, "cancelled")
        ctx.close()
        return
    conn.execute("UPDATE runs SET status = 'running', started_at = datetime('now', 'localtime') WHERE id = ?",
                 (run_id,))
    conn.commit()
    token = CURRENT_RUN.set(run_id)  # 이 실행에서 부른 LLM 사용량·트레이스에 실행 id 를 남긴다
    try:
        ctx.log(f"시작: {spec.title}")
        params = json.loads(row["params_json"])
        with trace.span("run", spec.title, run_id=run_id, input=params, attrs={"kind": row["kind"]}) as root:
            summary = spec.run(ctx, params)
            root.output = summary
        ctx.progress(1.0)
        ctx.log("완료")
        _finish(conn, run_id, "succeeded", summary=summary)
    except Cancelled:
        ctx.log("사용자가 취소했습니다", "warn")
        _finish(conn, run_id, "cancelled")
    except JobError as e:
        ctx.log(str(e), "error")
        _finish(conn, run_id, "failed", error=str(e))
    except Exception as e:  # noqa: BLE001 — 어떤 오류든 실행 기록에 남겨야 한다
        log.exception("run %s failed", run_id)
        ctx.log(f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=5)}", "error")
        _finish(conn, run_id, "failed", error=f"{type(e).__name__}: {e}")
    finally:
        CURRENT_RUN.reset(token)
        ctx.close()


def _finish(conn: sqlite3.Connection, run_id: int, status: str, summary: dict | None = None,
            error: str | None = None) -> None:
    conn.execute(
        "UPDATE runs SET status = ?, summary_json = ?, error = ?, finished_at = datetime('now', 'localtime') "
        "WHERE id = ?",
        (status, json.dumps(summary, ensure_ascii=False) if summary is not None else None, error, run_id),
    )
    conn.commit()


INTERRUPTED = "서버가 재시작되어 중단됨"  # 실행 자체의 실패와 가르는 데 쓴다 (자동 운용이 다시 열기 횟수에서 뺀다)


def recover_interrupted(conn: sqlite3.Connection) -> int:
    """서버가 꺼질 때 돌고 있던 실행을 실패로 정리한다 (시작 시 호출)."""
    cur = conn.execute(
        "UPDATE runs SET status = 'failed', error = ?, "
        "finished_at = datetime('now', 'localtime') WHERE status IN ('queued', 'running')", (INTERRUPTED,)
    )
    conn.commit()
    return cur.rowcount


# --- 화면용 실행 dict ---

def run_dict(row: sqlite3.Row) -> dict:
    out = {k: row[k] for k in row.keys() if k not in ("params_json", "summary_json")}
    out["params"] = json.loads(row["params_json"])
    out["summary"] = json.loads(row["summary_json"]) if row["summary_json"] else None
    out["cancel_requested"] = bool(row["cancel_requested"])
    spec = REGISTRY.get(row["kind"])
    out["kind_title"] = spec.title if spec else row["kind"]
    return out


def load_run(conn: sqlite3.Connection, run_id: int) -> dict | None:
    """실행 한 건 + 부모 실행 이름. 없으면 None."""
    row = conn.execute(
        "SELECT r.*, NULL AS dataset_name, p.name AS parent_name FROM runs r "
        "LEFT JOIN runs p ON p.id = r.parent_id WHERE r.id = ?",
        (run_id,),
    ).fetchone()
    return run_dict(row) if row else None
