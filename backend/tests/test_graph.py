import pytest

from app.core import trace
from app.llm import routes


def test_agent_catalog_shows_prompts_tools_rules_routes_and_links(client):
    agents = client.get("/api/graph/agents").json()
    keys = [a["key"] for a in agents]
    assert keys[:7] == ["technical", "value", "news", "growth", "portfolio", "risk", "trader"]
    assert {"strategist", "sector", "scout", "assistant", "advisor", "scheduler"} <= set(keys)
    tech = agents[0]
    assert "'차티스트' 에이전트입니다" in tech["system_prompt"] and "예시 범위" in tech["system_prompt"]  # 예시 세션으로 그린 프롬프트
    assert {t["name"] for t in tech["tools"]} >= {"get_quote", "get_technicals"} and tech["tools"][0]["source"]
    assert any(r["title"] == "보유 종목 매도 타이밍" for r in tech["rules"])
    assert {x["to"] for x in tech["talks_to"]} >= {"value", "admin", "gate"}
    trader = client.get("/api/graph/agents/trader").json()
    assert trader["role"] == "executor" and any(t["name"] == "place_order" and not t["read_only"] for t in trader["tools"])
    assert any(r["ref"]["path"].endswith("broker/trading.py") for r in trader["rules"])
    assert any(p["title"] == "회의 후보 고르기" for p in client.get("/api/graph/agents/portfolio").json()["extra_prompts"])
    assert client.get("/api/graph/agents/assistant").json()["tools"]  # 비서 도구는 실제 build_tools 로
    # LLM 경로 — 에이전트 경로를 정하면 그대로 보인다
    routes.set_routes(agents={"risk": "vllm:qwen"})
    assert client.get("/api/graph/agents/risk").json()["route"]["source"] == "agent"
    assert client.get("/api/graph/agents/nope").status_code == 404


def test_flows_are_consistent_graphs(client):
    flows = client.get("/api/graph/flows").json()
    assert [f["key"] for f in flows] == ["meeting", "continuous", "desk", "assistant", "system"]
    for f in flows:
        g = client.get(f"/api/graph/flows/{f['key']}").json()
        ids = {n["id"] for n in g["nodes"]}
        assert len(ids) == len(g["nodes"]) and g["edges"]
        assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])
        assert g["mermaid"].startswith("flowchart LR")
    meeting = client.get("/api/graph/flows/meeting").json()
    agent_nodes = {n["agent"] for n in meeting["nodes"] if n["kind"] == "agent"}
    assert {"technical", "risk", "trader"} <= agent_nodes
    gate = next(n for n in meeting["nodes"] if n["id"] == "gate")
    assert gate["ref"]["path"].endswith("debate/agents.py") and gate["ref"]["line"] > 0


def test_observed_graph_counts_calls_and_replies(client):
    with trace.span("run", "에이전트 토론") as root:
        for key, to, agree in (("technical", "가치 투자자", False), ("value", "차티스트", True)):
            with routes.as_agent(key):
                with trace.span("llm", "agent_action") as llm:
                    trace.annotate(provider="claude-agent-sdk", model="claude-opus-5-5", input_tokens=10, output_tokens=5)
                    with trace.span("tool", "get_quote"):
                        pass
                    llm.output = {"responses": [{"to": to, "agree": agree, "point": "x"}, {"to": "관리자", "agree": True}]}
        trace.event("gate", {"status": "conflict"})
    g = client.get(f"/api/graph/observed/{root.id}").json()
    edges = {(e["source"], e["target"], e["kind"]): e for e in g["edges"]}
    assert edges[("root", "agent:technical", "control")]["count"] == 1
    assert edges[("agent:technical", "tool:get_quote", "tool")]["count"] == 1
    assert edges[("agent:technical", "model:claude-agent-sdk:claude-opus-5-5", "llm")]["tokens"] == 15
    assert edges[("agent:technical", "agent:value", "talk")]["disagree"] == 1
    assert edges[("agent:value", "agent:technical", "talk")]["agree"] == 1
    assert edges[("agent:value", "admin", "talk")]["count"] == 1 and ("root", "event:gate", "event") in edges
    assert client.get("/api/graph/observed/nope").status_code == 404


def test_langgraph_export(client):
    pytest.importorskip("langgraph")
    out = client.get("/api/graph/langgraph/meeting").json()
    assert out["installed"] and "gate" in out["nodes"] and "__start__" in out["nodes"] and "__end__" in out["nodes"]
    assert ["gate", "trader"] in out["edges"] and "graph TD" in out["mermaid"]
    assert client.get("/api/graph/langgraph/nope").status_code == 404
