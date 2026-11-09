import asyncio
import json

import pytest

from app.llm import loop, vllm
from app.llm.client import LLMError, ToolDef
from tests.sdk_fakes import usage_rows


def _reply(content: str = "", tool_calls: list | None = None, finish: str = "stop", model: str = "qwen-32b") -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"model": model, "choices": [{"message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 40}}}


@pytest.fixture
def server(monkeypatch):
    """가짜 OpenAI 호환 서버: 보낸 요청을 남기고 준비한 답을 차례로 돌려준다."""
    monkeypatch.setenv("MIA_VLLM_URL", "http://127.0.0.1:8001/v1/")
    seen: list[dict] = []
    replies: list[dict] = []

    def http(method, url, body=None, timeout=0):
        seen.append({"method": method, "url": url, "body": body})
        if url.endswith("/models"):
            return 200, json.dumps({"data": [{"id": "qwen-32b"}]}).encode()
        return 200, json.dumps(replies.pop(0)).encode()

    monkeypatch.setattr(vllm, "HTTP", http)
    return seen, replies


def test_structured_uses_response_format_and_records_usage(client, server):
    seen, replies = server
    replies.append(_reply('{"answer": 7}'))
    out = asyncio.run(loop.structured("news_report", "hi", {"type": "object", "required": ["answer"]},
                                      backend=vllm.VllmBackend(), system_prompt="규칙"))
    assert out == {"answer": 7}
    body = seen[-1]["body"]
    assert seen[-1]["url"] == "http://127.0.0.1:8001/v1/chat/completions"
    assert body["model"] == "qwen-32b"  # 기본 모델이 없으면 서버의 첫 모델
    assert body["response_format"]["type"] == "json_schema" and body["messages"][0]["content"].startswith("규칙")
    row = usage_rows()[-1]
    assert (row["provider"], row["model"], row["input_tokens"], row["cache_read_tokens"], row["output_tokens"]) == (
        "vllm", "qwen-32b", 60, 40, 20)


def test_agentic_runs_tools_in_openai_format(client, server):
    seen, replies = server
    calls = []

    async def price(args):
        calls.append(args)
        return {"price": 71000}

    tools = [ToolDef("get_price", "현재가", {"type": "object", "properties": {"symbol": {"type": "string"}}}, price)]
    replies.append(_reply(tool_calls=[{"id": "c1", "type": "function",
                                       "function": {"name": "get_price", "arguments": '{"symbol": "005930"}'}}],
                          finish="tool_calls"))
    replies.append(_reply('{"stance": "buy"}'))
    out = asyncio.run(loop.agentic("agent_action", "판단", {"type": "object"}, tools=tools,
                                   backend=vllm.VllmBackend("qwen-32b")))
    assert out == {"stance": "buy"} and calls == [{"symbol": "005930"}]
    second = seen[-1]["body"]
    assert second["tools"][0]["function"]["name"] == "get_price" and "response_format" not in second
    assistant, tool = second["messages"][-2], second["messages"][-1]
    assert assistant["tool_calls"][0]["id"] == "c1"
    assert tool == {"role": "tool", "tool_call_id": "c1", "content": '{"price": 71000}'}


def test_bad_tool_arguments_are_reported_back(client, server):
    seen, replies = server

    async def never(_args):
        raise AssertionError("부르면 안 됨")

    tools = [ToolDef("get_price", "현재가", {"type": "object"}, never)]
    replies.append(_reply(tool_calls=[{"id": "c9", "type": "function",
                                       "function": {"name": "get_price", "arguments": "{oops"}}]))
    replies.append(_reply('{"ok": true}'))
    assert asyncio.run(loop.agentic("agent_action", "x", {"type": "object"}, tools=tools,
                                    backend=vllm.VllmBackend("m"))) == {"ok": True}
    assert seen[-1]["body"]["messages"][-1]["content"].startswith("오류: 도구 호출 형식이 틀렸어요")


def test_missing_url_and_server_errors_raise_llm_error(client, monkeypatch):
    monkeypatch.delenv("MIA_VLLM_URL", raising=False)
    with pytest.raises(LLMError, match="MIA_VLLM_URL"):
        asyncio.run(vllm.chat([{"role": "user", "content": "x"}], model="m"))
    assert vllm.status()["configured"] is False
    monkeypatch.setenv("MIA_VLLM_URL", "http://127.0.0.1:8001/v1")
    monkeypatch.setattr(vllm, "HTTP", lambda *a, **k: (400, b'{"error": {"message": "bad model"}}'))
    with pytest.raises(LLMError, match="bad model"):
        asyncio.run(vllm.chat([{"role": "user", "content": "x"}], model="m"))
    st = vllm.status()
    assert st["configured"] and not st["reachable"] and "bad model" in st["error"]
