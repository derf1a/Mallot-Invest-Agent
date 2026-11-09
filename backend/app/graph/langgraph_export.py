"""LangGraph 로 옮기기 — 구조 그래프(topology)의 흐름을 LangGraph StateGraph 로 만들어 컴파일한다.

지금 하는 일: 각 흐름의 차례(control) · 말(talk) 선으로 StateGraph 를 만들고 compile → get_graph().draw_mermaid().
LangGraph Studio · LangGraph 의 그래프 그림과 같은 모양을 얻는다. 노드는 실행하지 않는 자리표시(no-op)다.

실제 실행 엔진을 LangGraph 로 바꾸려면 agents.run_session(라운드 → 게이트 → 트레이더)을 노드 함수 셋으로 나눠야 한다.
그 단계에서도 주문 검사(trading.place_order) · 게이트(코드)는 그대로 노드 안의 코드로 남긴다 — docs/langgraph.md.
langgraph 는 선택 설치 (`uv sync --extra langgraph`).
"""

import sqlite3
from typing import TypedDict

from app.graph import topology

FLOW_KINDS = ("control", "talk", "order")


def installed() -> bool:
    try:
        import langgraph  # noqa: F401
    except ImportError:
        return False
    return True


class _State(TypedDict, total=False):
    log: list[str]


def build(f: dict):
    """흐름 → 컴파일된 LangGraph. 들어오는 선이 없는 노드는 START 에서, 나가는 선이 없는 노드는 END 로."""
    from langgraph.graph import END, START, StateGraph

    g = StateGraph(_State)
    ids = [n["id"] for n in f["nodes"] if n["kind"] != "data"]
    for nid in ids:
        g.add_node(nid, lambda state, _n=nid: {"log": [*state.get("log", []), _n]})
    edges = [(e["source"], e["target"]) for e in f["edges"]
             if e["kind"] in FLOW_KINDS and e["source"] in ids and e["target"] in ids and e["source"] != e["target"]]
    edges = list(dict.fromkeys(edges))
    has_in = {b for _, b in edges}
    has_out = {a for a, _ in edges}
    for nid in ids:
        if nid not in has_in:
            g.add_edge(START, nid)
        if nid not in has_out:
            g.add_edge(nid, END)
    for a, b in edges:
        g.add_edge(a, b)
    return g.compile()


def export(conn: sqlite3.Connection, key: str) -> dict | None:
    f = topology.flow(conn, key)
    if f is None:
        return None
    if not installed():
        return {"key": key, "installed": False, "mermaid": None, "nodes": None,
                "how": "uv sync --extra langgraph 후 다시 시작"}
    graph = build(f).get_graph()
    return {"key": key, "installed": True, "mermaid": graph.draw_mermaid(), "nodes": sorted(graph.nodes),
            "edges": [[e.source, e.target] for e in graph.edges]}
