"""SQLite 연결·마이그레이션 실행·데이터 폴더. 스키마 단계 목록은 app.core.migrations."""

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from app.core.migrations import MIGRATIONS

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "invest.db"


def db_path() -> Path:
    return Path(os.environ.get("MIA_DB_PATH", DEFAULT_DB_PATH))


def data_dir() -> Path:
    """DB 파일이 있는 폴더 (실행 산출물·로컬 모델도 여기 아래에 둔다)."""
    return db_path().parent


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # 연결은 요청마다 새로 만들고 공유하지 않는다. async 엔드포인트와 sync 의존성이 다른 스레드에서 돌 수 있어
    # 동일 스레드 검사를 끈다.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        # 테이블 재작성 중 DROP TABLE 이 ON DELETE SET NULL 을 일으켜 연결이 끊기지 않도록 FK 검사를 끈다.
        # (트랜잭션 밖에서만 바꿀 수 있다)
        conn.execute("PRAGMA foreign_keys = OFF")
        for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.executescript(f"BEGIN; {sql} PRAGMA user_version = {i}; COMMIT;")
            broken = conn.execute("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise RuntimeError(f"migration v{i} left broken foreign keys: {[tuple(r) for r in broken]}")
        conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()


def get_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI 의존성: 요청마다 연결을 열고 닫는다. 쓰기 엔드포인트는 직접 commit 한다."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
