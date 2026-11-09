from app.core import trace
from app.tracing import langsmith


def _sample() -> str:
    with trace.span("run", "에이전트 토론", input={"symbol": "005930"}) as root:
        with trace.span("step", "라운드 1"):
            with trace.span("agent", "technical", agent="technical"):
                with trace.span("llm", "agent_action") as llm:
                    trace.annotate(provider="vllm", model="q", input_tokens=100, cache_read_tokens=20, output_tokens=10,
                                   cost_usd=0.01)
                    llm.output = {"stance": "buy"}
                    with trace.span("tool", "get_price", input={"symbol": "005930"}) as tool:
                        tool.output = {"price": 1}
        trace.event("gate", {"status": "aligned"})
    return root.id


def test_list_detail_and_agent_stats(client):
    tid = _sample()
    listed = client.get("/api/traces").json()
    assert [t["trace_id"] for t in listed] == [tid]
    s = listed[0]["summary"]
    assert (s["spans"], s["llm_calls"], s["tool_calls"], s["input_tokens"], s["output_tokens"], s["agents"]) == (
        6, 1, 1, 120, 10, ["technical"])
    detail = client.get(f"/api/traces/{tid}").json()
    assert detail["root"]["name"] == "에이전트 토론" and len(detail["spans"]) == 6
    llm = next(sp for sp in detail["spans"] if sp["kind"] == "llm")
    assert llm["output"] == {"stance": "buy"} and llm["attrs"]["model"] == "q"
    assert client.get("/api/traces", params={"agent": "risk"}).json() == []
    stats = client.get("/api/traces/stats/agents").json()
    assert stats[0]["agent"] == "technical" and stats[0]["turns"] == 1 and stats[0]["tool_calls"] == 1
    assert client.get("/api/traces/nope").status_code == 404


class FakeClient:
    def __init__(self):
        self.runs = []

    def create_run(self, **kw):
        self.runs.append(kw)


def test_langsmith_export_builds_a_consistent_tree(client, monkeypatch):
    assert client.get("/api/traces/export/langsmith").json()["configured"] is False  # 키만으로는 켜지지 않는다
    fake = FakeClient()
    assert langsmith.start(client=fake)
    try:
        _sample()
    finally:
        langsmith.stop()
    by_name = {r["name"]: r for r in fake.runs}
    root, llm, tool = by_name["에이전트 토론"], by_name["agent_action"], by_name["get_price"]
    assert root["parent_run_id"] is None and {r["trace_id"] for r in fake.runs} == {root["id"]}
    assert tool["parent_run_id"] == llm["id"] and tool["run_type"] == "tool" and llm["run_type"] == "llm"
    assert tool["dotted_order"].startswith(root["dotted_order"]) and tool["dotted_order"].count(".") == 4
    assert llm["outputs"]["usage_metadata"] == {"input_tokens": 120, "output_tokens": 10, "total_tokens": 130}
    assert llm["extra"]["metadata"]["ls_model_name"] == "q" and by_name["agent:technical"]["tags"] == ["agent", "technical"]
    assert len(fake.runs) == 6 and langsmith._exporter not in trace.EXPORTERS
