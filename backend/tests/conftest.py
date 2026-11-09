import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def runs_sync(monkeypatch):
    """작업을 백그라운드 스레드 대신 즉시 실행한다."""
    from app.invest import assistant
    from app.runs import runner

    monkeypatch.setattr(runner, "SYNC", True)
    monkeypatch.setattr(assistant, "SYNC", True)  # 투자 비서 답도 바로


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    """테스트가 실제 LLM·뉴스를 부르지 않게 — 따로 가짜를 넣지 않은 투자 비서·회의록·일정 담당·운용 데스크는 실패로(코드 대체 경로를
    탄다), 뉴스 RSS 는 연결 실패로."""
    from app.invest import advisor, assistant, autopilot, desk_agents, morning
    from app.invest.debate import minutes
    from app.invest.analysis import screening
    from app.invest.marketdata import news
    from app.llm.client import LLMError

    async def offline(*_args, **_kwargs):
        raise LLMError("테스트에서는 실제 LLM 을 부르지 않습니다")

    for module, name in ((assistant, "LLM"), (minutes, "LLM"), (autopilot, "PLANNER"), (morning, "LLM"), (screening, "LLM"),
                         (desk_agents, "LLM"), (advisor, "LLM")):
        monkeypatch.setattr(module, name, offline)
    monkeypatch.setattr(news, "HTTP", lambda *_a, **_k: (0, b""))


@pytest.fixture(autouse=True)
def meetings_mode(monkeypatch):
    """테스트의 자동 운용 기본은 정기 회의 — 상시 운용 테스트만 mode=continuous 로 켠다. 상시 운용 채널이 실제로 기다리면(시계를
    돌리지 않은 테스트) 멈추지 않고 바로 실패하게."""
    from app.invest import autopilot
    from app.invest.debate import stream

    monkeypatch.setattr(autopilot, "DEFAULT_CONFIG", {**autopilot.DEFAULT_CONFIG, "mode": "meetings"})

    def no_sleep(_s):
        raise RuntimeError("상시 운용 채널이 테스트에서 기다리려 했어요 — stream.SLEEP 을 바꾸세요")

    monkeypatch.setattr(stream, "SLEEP", no_sleep)


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
                "DART_API_KEY", "MIA_VLLM_URL", "MIA_VLLM_API_KEY", "LANGSMITH_API_KEY", "MIA_MODELS_DIR"):
        monkeypatch.delenv(key, raising=False)
    from app.invest.marketdata import market
    from app.invest.broker import config, toss

    market.clear_cache()
    toss.clear_token_cache()
    config.reset_pin_failures()
    yield
    market.clear_cache()
    toss.clear_token_cache()


PIN = "2468"


@pytest.fixture
def broker(monkeypatch):
    """토스 키·주문 PIN 이 있는 상태 + 가짜 토스 계좌(tests/fake_broker.py). 시세는 샘플 시세 그대로."""
    from app.invest.marketdata import market
    from app.invest.broker import toss
    from app.invest.marketdata.market import SampleMarket
    from tests.fake_broker import FakeBroker

    monkeypatch.setenv("TOSS_CLIENT_ID", "cid")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "secret")
    monkeypatch.setenv("MIA_TRADING_PIN", PIN)
    fake = FakeBroker()
    monkeypatch.setattr(toss, "HTTP", fake)
    monkeypatch.setattr(market, "provider", lambda: SampleMarket())
    return fake


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c
