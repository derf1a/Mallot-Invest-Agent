from app.core.db import connect, init_db
from app.core.migrations import MIGRATIONS


def test_migrations_reach_latest_and_are_idempotent():
    init_db()
    init_db()  # 두 번 돌려도 이미 실행된 단계는 다시 돌지 않는다
    with connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"settings", "runs", "run_logs", "run_metrics", "llm_usage", "invest_orders", "invest_watchlist"} <= tables
    assert not any(t.startswith("lab_") for t in tables)


def test_default_watch_group_exists():
    init_db()
    with connect() as conn:
        assert [tuple(r) for r in conn.execute("SELECT id, name FROM invest_watch_groups")] == [(1, "관심")]
