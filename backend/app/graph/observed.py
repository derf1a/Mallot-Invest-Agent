"""관찰 그래프 — 실제 트레이스 하나에서 누가 무엇을 몇 번 불렀고 누가 누구에게 말했는지.

노드: 실행 · 에이전트 · 도구 · 모델(공급자:모델) · 사건. 선: 실행/단계 → 에이전트(차례 수), 에이전트 → 모델(LLM 호출 · 토큰),
에이전트 → 도구(호출 · 실패), 에이전트 → 에이전트(발언의 responses[].to — 동의/반박 수), 에이전트 → 사건(게이트 · 주문).
"""

import sqlite3

from app.graph import catalog
from app.invest.debate import agents
from app.tracing import store


def _titles(conn: sqlite3.Connection) -> tuple[dict[str, str], dict[str, str]]:
    by_key = {a["key"]: a["title"] for a in catalog.all_agents(conn)}
    by_title = {v: k for k, v in by_key.items()}
    by_title[agents.ADMIN] = "admin"
    return by_key, by_title


def build(conn: sqlite3.Connection, trace_id: str) -> dict | None:
    t = store.trace(conn, trace_id)
    if t is None:
        return None
    by_key, by_title = _titles(conn)
    spans = {s["id"]: s for s in t["spans"]}
    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str, str], dict] = {}

    def node(id_: str, label: str, kind: str, **extra) -> str:
        n = nodes.setdefault(id_, {"id": id_, "label": label, "kind": kind, "count": 0, **extra})
        n["count"] += 1
        return id_

    def edge(a: str, b: str, kind: str, **add) -> None:
        e = edges.setdefault((a, b, kind), {"source": a, "target": b, "kind": kind, "count": 0, "agree": 0, "disagree": 0,
                                            "tokens": 0, "errors": 0})
        e["count"] += 1
        for k, v in add.items():
            e[k] += v

    def owner(s: dict) -> str | None:
        """이 span 을 일으킨 쪽 노드: 가장 가까운 agent span, 없으면 실행."""
        cur = s
        while cur is not None:
            if cur["kind"] == "agent":
                return f"agent:{cur['agent']}"
            if cur["parent_id"] is None:
                return "root"
            cur = spans.get(cur["parent_id"])
        return "root"

    root = t["root"]
    node("root", root["name"], "run")
    for s in t["spans"]:
        if s["kind"] == "agent":
            key = s["agent"] or s["name"]
            node(f"agent:{key}", by_key.get(key, key), "agent", agent=key)
            parent = spans.get(s["parent_id"]) if s["parent_id"] else None
            src = owner(parent) if parent else "root"
            edge(src if src != f"agent:{key}" else "root", f"agent:{key}", "control", errors=int(s["status"] == "error"))
        elif s["kind"] == "llm":
            a = s["attrs"]
            model = f"{a.get('provider') or a.get('route') or 'llm'}:{a.get('model') or ''}".rstrip(":")
            node(f"model:{model}", model, "model")
            tokens = int(a.get("input_tokens") or 0) + int(a.get("cache_read_tokens") or 0) + int(a.get("output_tokens") or 0)
            src = owner(s)
            edge(src, f"model:{model}", "llm", tokens=tokens, errors=int(s["status"] == "error"))
            out = s.get("output")
            if isinstance(out, dict) and src.startswith("agent:"):
                for r in out.get("responses") or []:
                    to = by_title.get(str(r.get("to") or "").strip()) if isinstance(r, dict) else None
                    if not to:
                        continue
                    target = "admin" if to == "admin" else f"agent:{to}"
                    if target == "admin":
                        node("admin", agents.ADMIN, "human")
                    elif target not in nodes:
                        node(target, by_key.get(to, to), "agent", agent=to)
                    edge(src, target, "talk", agree=int(bool(r.get("agree"))), disagree=int(not r.get("agree")))
        elif s["kind"] == "tool":
            node(f"tool:{s['name']}", s["name"], "tool", source=catalog.TOOL_SOURCES.get(s["name"], "app"))
            edge(owner(s), f"tool:{s['name']}", "tool", errors=int(s["status"] == "error"))
        elif s["kind"] == "event":
            node(f"event:{s['name']}", s["name"], "event")
            edge(owner(s), f"event:{s['name']}", "event")
    # 처음 노드를 만들 때 count 1 을 넣었으므로 차례 · 호출 수는 선 쪽이 정확하다 — 노드는 등장 횟수
    return {"trace_id": trace_id, "root": {k: root[k] for k in ("id", "name", "kind", "status", "run_id", "start_ms", "end_ms")},
            "summary": t["summary"], "nodes": list(nodes.values()), "edges": list(edges.values())}
