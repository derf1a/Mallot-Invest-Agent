import asyncio
import json

import pytest

from app.core import trace
from app.core.db import connect, init_db


def rows() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM trace_spans ORDER BY start_ms, rowid")]


@pytest.fixture(autouse=True)
def db():
    init_db()


def test_nested_spans_share_a_trace_and_record_output():
    with trace.span("run", "토론", run_id=None, input={"symbol": "005930"}) as root:
        with trace.span("agent", "technical", agent="technical"):
            with trace.span("llm", "agent_action") as llm:
                trace.annotate(input_tokens=10, model="m")
                llm.output = {"stance": "buy"}
        trace.event("gate", {"status": "aligned"})
    got = {r["name"]: r for r in rows()}
    assert {r["trace_id"] for r in got.values()} == {root.id}
    assert got["technical"]["parent_id"] == root.id and got["agent_action"]["parent_id"] == got["technical"]["id"]
    assert got["agent_action"]["agent"] == "technical"  # 에이전트는 부모에게서 물려받는다
    assert json.loads(got["agent_action"]["attrs_json"]) == {"input_tokens": 10, "model": "m"}
    assert json.loads(got["agent_action"]["output_json"]) == {"stance": "buy"}
    assert got["gate"]["kind"] == "event" and got["gate"]["parent_id"] == root.id
    assert all(r["status"] == "ok" and r["duration_ms"] is not None for r in got.values())


def test_errors_are_recorded_and_reraised():
    with pytest.raises(ValueError):
        with trace.span("tool", "get_price"):
            raise ValueError("시세 없음")
    r = rows()[0]
    assert r["status"] == "error" and "시세 없음" in r["error"]


def test_parallel_tasks_keep_their_own_parent():
    async def speak(key: str) -> None:
        with trace.span("agent", key, agent=key):
            await asyncio.sleep(0)
            with trace.span("llm", "agent_action"):
                await asyncio.sleep(0)

    async def round_() -> None:
        with trace.span("step", "라운드 1"):
            await asyncio.gather(speak("a"), speak("b"))

    asyncio.run(round_())
    by_id = {r["id"]: r for r in rows()}
    for r in by_id.values():
        if r["kind"] == "llm":
            assert by_id[r["parent_id"]]["agent"] == r["agent"]


def test_traced_decorator_disable_switch_and_prune(monkeypatch):
    @trace.traced("step", lambda x: f"대화 {x}")
    async def talk(x: int) -> int:
        return x * 2

    assert asyncio.run(talk(3)) == 6 and rows()[0]["name"] == "대화 3"
    monkeypatch.setenv("MIA_TRACE", "0")
    with trace.span("llm", "x"):
        pass
    assert len(rows()) == 1
    with connect() as conn:
        conn.execute("UPDATE trace_spans SET start_ms = 0")
        conn.commit()
    assert trace.prune(days=1) == 1 and rows() == []


def test_long_values_are_clipped():
    with trace.span("tool", "big", input="x" * (trace.CLIP + 10)):
        pass
    assert json.loads(rows()[0]["input_json"])["clipped"] is True
