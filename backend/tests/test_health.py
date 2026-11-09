from app.core.migrations import MIGRATIONS


def test_health_reports_db_version(client) -> None:
    assert client.get("/api/health").json() == {"ok": True, "db_version": len(MIGRATIONS)}
