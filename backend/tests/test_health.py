from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    with TestClient(app) as c:
        assert c.get("/api/health").json() == {"ok": True}
