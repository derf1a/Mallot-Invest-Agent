import asyncio
import json
import subprocess
import time

import claude_agent_sdk
import pytest

from app.core.db import connect
from app.llm import client as llm, local, local_run
from app.llm.mlx_worker import kv_plan, looping, split_reasoning, tool_blocks
from tests.sdk_fakes import fake_query, ok_result, usage_rows

KEY = "qwen3.8-27b-2bit"
REAL_LOAD = local.load


@pytest.fixture(autouse=True)
def no_device(monkeypatch):
    """Metal 정보는 실제 mlx 를 불러오지 않고, 기본 LLM 을 로컬로 바꿔도 작업 프로세스를 띄우지 않는다."""
    monkeypatch.setattr(local, "device_info", lambda: {"max_recommended_working_set_size": 12 * 1024**3})
    monkeypatch.setattr(local, "load", lambda key=None: None)


def fake_download(key: str = KEY) -> None:
    d = local.model_dir(key)
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text("{}")
    (d / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"a": "model-1.safetensors"}}))
    (d / "model-1.safetensors").write_bytes(b"0" * 10)


def reply(content: str, *, calls=None, prompt=100, cached=0, output=20) -> dict:
    return {"content": content, "text": content, "reasoning": "", "tool_calls": calls or [], "tool_call_errors": [],
            "usage": {"prompt_tokens": prompt, "cached_tokens": cached, "output_tokens": output},
            "timing": {"prefill_ms": 300, "generate_ms": 700}}


def script(monkeypatch, replies: list[dict]) -> list:
    """local.chat 을 정해 둔 답 순서로 바꾸고, 받은 메시지를 기록한다."""
    seen = []

    async def chat(messages, *, tools=None, max_tokens=4096, temperature=None):
        seen.append({"messages": [dict(m) for m in messages], "tools": tools})
        return replies.pop(0)

    monkeypatch.setattr(local, "chat", chat)
    return seen


def use_local(client):
    fake_download()
    assert client.put("/api/llm/provider", json={"provider": "local"}).status_code == 200


# ---------- 계산 함수 ----------

def test_kv_plan_budget_and_context_cap():
    gib = 1024**3
    plan = kv_plan(12 * gib, int(7.83 * gib), 150 * 1024**2, 65536, 262144, gib)
    assert plan["kv_tokens"] == (12 * gib - int(7.83 * gib) - 150 * 1024**2 - gib) // 65536
    assert plan["kv_budget_bytes"] == plan["kv_tokens"] * 65536
    assert kv_plan(64 * gib, gib, 0, 65536, 32768, gib)["kv_tokens"] == 32768  # 모델 최대 길이를 넘지 않는다
    assert kv_plan(8 * gib, 9 * gib, 0, 65536, 32768, gib)["kv_tokens"] == 0


def test_loop_guard():
    assert looping([7, 8, 9] * 40)
    assert looping([1] * 50 + [5] * 96)
    assert not looping(list(range(200)))
    assert not looping([7, 8, 9] * 20)  # 아직 짧다


def test_reasoning_and_tool_blocks():
    assert split_reasoning("따져 보면…\n</think>\n\n답", True) == ("따져 보면…", "답")
    assert split_reasoning("생각만 하다 끊김", True) == ("생각만 하다 끊김", "")
    assert split_reasoning(" 답 ", False) == ("", "답")
    head, blocks = tool_blocks("확인할게요\n\n<tool_call>\n<function=a>\n</function>\n</tool_call>\n<tool_call>\n<function=b>")
    assert head == "확인할게요" and blocks == ["<function=a>\n</function>", "<function=b>"]
    assert tool_blocks("그냥 답") == ("그냥 답", [])


def test_extract_json_and_schema_check():
    assert local_run.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert local_run.extract_json('결과입니다: {"a": [1, 2]} 끝') == {"a": [1, 2]}
    assert local_run.extract_json('{"s": "첫 줄\n둘째 줄"}'.replace("\\n", "\n")) == {"s": "첫 줄\n둘째 줄"}
    with pytest.raises(ValueError):
        local_run.extract_json("JSON 없음")
    schema = {"type": "object", "additionalProperties": False, "required": ["kind", "n"],
              "properties": {"kind": {"type": "string", "enum": ["buy", "sell"]}, "n": {"type": "integer", "minimum": 1},
                             "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 2}}}
    value = {"kind": "buy", "n": 2.0, "extra": "x"}
    assert local_run.check(value, schema) == [] and "extra" not in value  # 모르는 키는 지운다
    errors = local_run.check({"kind": "hold", "tags": ["a", "b", 3]}, schema)
    assert any("$.n: 빠짐" in e for e in errors) and any("enum" in e or "중 하나" in e for e in errors)
    assert any("$.tags" in e for e in errors)
    assert local_run.check({"n": None}, {"properties": {"n": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}}) == []


# ---------- 공급자 고르기 ----------

def test_default_is_claude_and_explicit_model_wins(client, monkeypatch):
    monkeypatch.setattr(claude_agent_sdk, "query", fake_query(ok_result()))
    seen = script(monkeypatch, [reply('{"answer": 2}')])
    assert asyncio.run(llm.structured("news_report", "hi", {"type": "object"})) == {"answer": 1}  # 기본: Claude
    assert asyncio.run(llm.structured("news_report", "hi", {"type": "object"}, model="local")) == {"answer": 2}
    assert len(seen) == 1
    rows = usage_rows()
    assert [r["provider"] for r in rows] == ["claude-agent-sdk", "mlx-lm"]
    assert rows[1]["model"] == local.model_name() and rows[1]["cost_usd"] is None


def test_local_provider_routes_text_but_images_go_to_claude(client, monkeypatch):
    use_local(client)
    sdk_seen = []
    monkeypatch.setattr(claude_agent_sdk, "query", fake_query(ok_result(), seen=sdk_seen))
    seen = script(monkeypatch, [reply('{"answer": 3}', prompt=500, cached=120, output=40)])
    assert asyncio.run(llm.structured("news_report", "hi", {"type": "object"}, system_prompt="규칙")) == {"answer": 3}
    system = seen[0]["messages"][0]["content"]
    assert system.startswith("규칙") and "JSON Schema" in system
    image = [llm.image_block(b"x", "image/png"), {"type": "text", "text": "판독"}]
    assert asyncio.run(llm.structured("news_analysis", image, {"type": "object"})) == {"answer": 1}
    assert sdk_seen[0].model == llm.DEFAULT_MODEL
    assert asyncio.run(llm.structured("news_report", "hi", {"type": "object"}, model="claude-sonnet-5-5")) == {"answer": 1}
    row = usage_rows()[0]
    assert (row["provider"], row["input_tokens"], row["cache_read_tokens"], row["output_tokens"], row["turns"]) == (
        "mlx-lm", 380, 120, 40, 1)
    assert llm.model_label().endswith("(로컬)") and llm.model_label("claude-sonnet-5-5") == "claude-sonnet-5-5"


def test_structured_retries_until_schema_fits(client, monkeypatch):
    use_local(client)
    schema = {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}
    looped = {**reply("하나 하나 하나"), "finish_reason": "repetition"}
    seen = script(monkeypatch, [looped, reply('{"n": "셋"}'), reply('{"n": 3}')])
    assert asyncio.run(llm.structured("news_report", "세어 봐", schema)) == {"n": 3}
    assert "되풀이" in seen[1]["messages"][-1]["content"] and "JSON 객체가 없어요" in seen[1]["messages"][-1]["content"]
    assert "$.n: integer" in seen[2]["messages"][-1]["content"]
    assert usage_rows()[0]["turns"] == 3

    script(monkeypatch, [reply("x"), reply("y")])
    with pytest.raises(llm.LLMError, match="형식에 맞는 답"):
        asyncio.run(llm.structured("news_report", "세어 봐", schema, max_turns=2))
    assert usage_rows()[-1]["ok"] == 0


def test_agentic_runs_app_tools_and_returns_final_json(client, monkeypatch):
    use_local(client)
    calls = []

    async def price(args):
        calls.append(args)
        return {"symbol": args["symbol"], "price": 71200}

    async def broken(args):
        raise RuntimeError("시세 없음")

    tools = [llm.ToolDef("get_price", "현재가", {"type": "object", "properties": {"symbol": {"type": "string"}}}, price),
             llm.ToolDef("get_news", "뉴스", {"type": "object", "properties": {}}, broken)]
    raw = "<tool_call>\n<function=get_price>\n<parameter=symbol>\n005930\n</parameter>\n</function>\n</tool_call>"
    seen = script(monkeypatch, [
        reply(raw, calls=[{"name": "get_price", "arguments": {"symbol": "005930"}}, {"name": "get_news", "arguments": {}},
                          {"name": "nope", "arguments": {}}]),
        reply('{"stance": "hold"}', cached=150),
    ])
    out = asyncio.run(llm.agentic("agent_action", "판단해", {"type": "object", "required": ["stance"]}, tools=tools,
                                  system_prompt="너는 차티스트"))
    assert out == {"stance": "hold"} and calls == [{"symbol": "005930"}]
    assert [t["function"]["name"] for t in seen[0]["tools"]] == ["get_price", "get_news"]
    second = seen[1]["messages"]
    assert second[2] == {"role": "assistant", "content": raw, "reasoning_content": ""}  # 생성된 글 그대로 (KV 캐시 재사용)
    assert json.loads(second[3]["content"])["price"] == 71200
    assert second[4]["content"] == "오류: 시세 없음" and "없는 도구" in second[5]["content"]
    assert usage_rows()[0]["turns"] == 2


def test_agentic_asks_for_final_answer_on_last_turn(client, monkeypatch):
    use_local(client)
    tools = [llm.ToolDef("t", "도구", {"type": "object", "properties": {}}, lambda a: asyncio.sleep(0, {"ok": 1}))]
    seen = script(monkeypatch, [reply("", calls=[{"name": "t", "arguments": {}}]), reply("", calls=[{"name": "t"}]),
                                reply('{"done": true}')])
    assert asyncio.run(llm.agentic("agent_action", "p", {"type": "object"}, tools=tools, max_turns=3)) == {"done": True}
    assert "최종 JSON 만" in seen[2]["messages"][-1]["content"]


def test_cancel_stops_worker_generation(client, monkeypatch):
    posted = []

    def request(path, body=None, timeout=2.0):
        posted.append(path)
        if path == "/chat":
            time.sleep(0.3)
        return {}

    async def ready():
        return None

    monkeypatch.setattr(local, "ensure_ready", ready)
    monkeypatch.setattr(local, "_request", request)

    async def run():
        task = asyncio.ensure_future(local.chat([{"role": "user", "content": "hi"}]))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert posted == ["/chat", "/cancel"]


# ---------- 설정 API ----------

def test_llm_settings_api(client, monkeypatch):
    st = client.get("/api/llm").json()
    assert st["provider"] == "claude" and st["options"] == local.DEFAULT_LOCAL and st["worker"]["state"] == "stopped"
    assert {m["key"] for m in st["models"]} == set(local.CATALOG) and not st["models"][0]["downloaded"]
    assert st["memory"]["gpu_limit_bytes"] > 0
    assert client.put("/api/llm/provider", json={"provider": "local"}).status_code == 409  # 모델 파일 없음
    assert client.put("/api/llm/provider", json={"provider": "gpt"}).status_code == 422
    assert client.put("/api/llm/local", json={"thinking": "max"}).status_code == 409
    assert client.put("/api/llm/local", json={"model": "llama"}).status_code == 409
    assert client.put("/api/llm/local", json={"thinking": "low"}).json()["options"]["thinking"] == "low"
    with pytest.raises(ValueError, match="내려받"):
        REAL_LOAD()  # 받지 않은 모델은 올리지 않는다 (작업 프로세스를 띄우기 전에 거절)

    loads = []
    monkeypatch.setattr(local, "load", lambda key=None: loads.append(key))
    fake_download()
    st = client.put("/api/llm/provider", json={"provider": "local"}).json()
    assert st["provider"] == "local" and loads == [None] and st["models"][0]["downloaded"]
    # 로컬을 쓰는 중에는 받지 않은 모델로 못 바꾸고, 쓰는 모델은 못 지운다
    assert client.put("/api/llm/local", json={"model": "qwen3.5-9b-4bit"}).status_code == 409
    assert client.delete(f"/api/llm/local/models/{KEY}").status_code == 409
    assert client.get("/api/llm/local/models/x").status_code in (404, 405)

    client.put("/api/llm/provider", json={"provider": "claude"})
    assert client.delete(f"/api/llm/local/models/{KEY}").json()["downloaded"] is False


def test_download_runs_in_background_and_can_be_cancelled(client, monkeypatch):
    started = []
    real_popen = subprocess.Popen

    def popen(cmd, **kw):
        started.append(cmd)
        return real_popen(["sleep", "30"])

    monkeypatch.setattr(local.subprocess, "Popen", popen)
    st = client.post(f"/api/llm/local/models/{KEY}/download").json()
    assert st["downloading"] and not st["downloaded"] and st["total_bytes"] == local.CATALOG[KEY]["bytes"]
    assert started[0][2:4] == ["app.llm.mlx_worker", "download"] and local.CATALOG[KEY]["repo"] in started[0]
    client.post(f"/api/llm/local/models/{KEY}/download")
    assert len(started) == 1  # 받는 중이면 다시 띄우지 않는다
    st = client.post(f"/api/llm/local/models/{KEY}/cancel").json()
    assert not st["downloading"] and st["error"] is None


def test_agent_options_accept_local_model(client, broker):
    from tests.conftest import PIN

    r = client.put("/api/invest/agents/options", json={"model": "local"}, headers={"X-Trading-Pin": PIN})
    assert r.status_code == 200 and r.json()["options"]["model"] == "local"
    assert client.put("/api/invest/agents/options", json={"model": "gpt-9"}, headers={"X-Trading-Pin": PIN}).status_code == 400
    with connect() as conn:
        assert json.loads(conn.execute("SELECT value FROM settings WHERE key = 'invest_agent_options'").fetchone()[0])[
            "model"] == "local"
