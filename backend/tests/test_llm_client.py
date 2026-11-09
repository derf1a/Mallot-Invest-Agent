import asyncio

import claude_agent_sdk
import pytest

from app.core.db import connect
from app.llm import client as llm
from tests.sdk_fakes import fake_query, ok_result, usage_rows


def test_structured_records_usage_on_success(client, monkeypatch):
    seen = []
    monkeypatch.setattr(claude_agent_sdk, "query", fake_query(ok_result(), seen=seen))
    out = asyncio.run(llm.structured("news_report", "hi", {"type": "object"}, model="claude-opus-5-5"))
    assert out == {"answer": 1}
    row = usage_rows()[0]
    assert (row["feature"], row["model"], row["input_tokens"], row["output_tokens"]) == ("news_report", "claude-opus-5-5", 1500, 300)
    assert (row["cache_read_tokens"], row["cache_write_tokens"], row["ok"], row["turns"]) == (50, 10, 1, 1)
    assert row["cost_usd"] == pytest.approx(0.0123) and row["api_duration_ms"] == 900
    # 넘겨준 자료만 근거로: 웹 검색 끔, CLAUDE.md·스킬 등 설정을 불러오지 않음
    assert "WebSearch" in seen[0].disallowed_tools and seen[0].setting_sources == []


def test_structured_records_failures_and_run_id(client, monkeypatch):
    monkeypatch.setattr(claude_agent_sdk, "query", fake_query(raises=FileNotFoundError("claude 없음")))
    with connect() as conn:
        run_id = conn.execute("INSERT INTO runs (kind, name) VALUES ('agent_action', 'x')").lastrowid
        conn.commit()
    token = llm.CURRENT_RUN.set(run_id)
    try:
        with pytest.raises(llm.LLMError, match="FileNotFoundError"):
            asyncio.run(llm.structured("agent_action", "hi", {"type": "object"}))
    finally:
        llm.CURRENT_RUN.reset(token)
    monkeypatch.setattr(claude_agent_sdk, "query", fake_query(ok_result(is_error=True, structured_output=None,
                                                                         errors=["rate limit"])))
    with pytest.raises(llm.LLMError, match="rate limit"):
        asyncio.run(llm.structured("news_report", "hi", {"type": "object"}))
    assert [(r["feature"], r["ok"], r["run_id"]) for r in usage_rows()] == [("agent_action", 0, run_id), ("news_report", 0, None)]


def test_agentic_turns_off_builtin_tools_and_other_mcp(client, monkeypatch):
    seen = []
    monkeypatch.setattr(claude_agent_sdk, "query", fake_query(ok_result(), seen=seen))

    async def handler(_args):
        return {"price": 1}

    tools = [llm.ToolDef("get_price", "현재가", {"type": "object", "properties": {}}, handler)]
    assert asyncio.run(llm.agentic("agent_action", "hi", {"type": "object"}, tools=tools)) == {"answer": 1}
    opts = seen[0]
    assert opts.tools == [] and opts.strict_mcp_config is True
    assert opts.allowed_tools == [llm.tool_id("get_price")] and list(opts.mcp_servers) == [llm.MCP_SERVER]
