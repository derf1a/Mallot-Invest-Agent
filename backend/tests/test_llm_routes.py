import asyncio
import json

import claude_agent_sdk
import pytest

from app.llm import client as llm, local, routes, vllm
from app.llm.routes import Route, as_agent, parse, resolve
from tests.sdk_fakes import fake_query, ok_result, usage_rows


def test_parse_route_text():
    assert parse("") is None and parse(None) is None
    assert parse("claude") == Route("claude", "", "call")
    assert parse("claude-sonnet-5-5") == Route("claude", "claude-sonnet-5-5", "call")
    assert parse("claude:claude-haiku-5-5").model == "claude-haiku-5-5"
    assert parse("local") == parse("mlx") == Route("mlx", "", "call")
    assert parse("vllm:Qwen/Qwen3-32B").model == "Qwen/Qwen3-32B" and parse("vllm").model == ""
    for bad in ("gpt-4", "mlx:qwen", "claude:gpt", "vllm:" + "x" * 300):
        with pytest.raises(ValueError):
            parse(bad)


def test_resolve_order_agent_then_call_then_feature_then_default(client):
    assert resolve("agent_action") == Route("claude", "", "default")  # 아무것도 안 정하면 Claude
    routes.set_routes(default="vllm")
    assert resolve("agent_action").text == "vllm"
    routes.set_routes(features={"agent_action": "claude-sonnet-5-5"})
    assert resolve("agent_action") == Route("claude", "claude-sonnet-5-5", "feature")
    assert resolve("agent_action", "local").source == "call"  # 토론 설정의 모델이 기능 경로를 이긴다
    routes.set_routes(agents={"chartist": "vllm:qwen-32b"})
    with as_agent("chartist"):
        assert resolve("agent_action", "local") == Route("vllm", "qwen-32b", "agent")  # 에이전트 경로가 가장 세다
    with as_agent("value"):
        assert resolve("agent_action").source == "feature"
    assert resolve("agent_action", has_image=True).provider == "claude"
    # 빈 값은 지우기, 틀린 값은 아무것도 바꾸지 않는다
    routes.set_routes(agents={"chartist": ""})
    with pytest.raises(ValueError):
        routes.set_routes(default="claude", features={"x": "gpt-4"})
    assert routes.routes() == {"default": "vllm", "features": {"agent_action": "claude-sonnet-5-5"}, "agents": {}}


def test_legacy_local_provider_is_the_default_when_unset(client, monkeypatch):
    monkeypatch.setattr(local, "provider", lambda: "local")
    assert resolve("news_report") == Route("mlx", "", "default")
    assert llm.uses_local() is True


def test_agent_route_sends_the_call_to_vllm(client, monkeypatch):
    monkeypatch.setenv("MIA_VLLM_URL", "http://127.0.0.1:8001/v1")
    bodies = []

    def http(method, url, body=None, timeout=0):
        bodies.append(body)
        return 200, json.dumps({"model": "qwen-32b", "choices": [{"message": {"content": '{"answer": 2}'},
                                                                   "finish_reason": "stop"}], "usage": {}}).encode()

    monkeypatch.setattr(vllm, "HTTP", http)
    monkeypatch.setattr(claude_agent_sdk, "query", fake_query(ok_result()))
    routes.set_routes(agents={"chartist": "vllm:qwen-32b"})
    with as_agent("chartist"):
        assert asyncio.run(llm.structured("agent_action", "hi", {"type": "object"})) == {"answer": 2}
    assert asyncio.run(llm.structured("agent_action", "hi", {"type": "object"})) == {"answer": 1}  # 다른 에이전트는 Claude
    assert [r["provider"] for r in usage_rows()] == ["vllm", "claude-agent-sdk"]
    assert bodies[0]["model"] == "qwen-32b"
