import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def runs_sync(monkeypatch):
    """작업을 백그라운드 스레드 대신 즉시 실행한다."""
    from app.runs import runner

    monkeypatch.setattr(runner, "SYNC", True)


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """테스트는 실제 data/invest.db 대신 임시 DB를 쓴다."""
    path = tmp_path / "test.db"
    monkeypatch.setenv("MIA_DB_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """실제 .env·셸의 키를 테스트에 끌어오지 않는다."""
    monkeypatch.setenv("MIA_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("MIA_SERVICES", "0")  # 백그라운드 서비스를 띄우지 않는다
    for key in ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET", "TOSS_ACCOUNT_SEQ", "MIA_TRADING_PIN", "ANTHROPIC_API_KEY",
                "MIA_VLLM_URL", "MIA_VLLM_API_KEY", "LANGSMITH_API_KEY", "MIA_MODELS_DIR"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c
