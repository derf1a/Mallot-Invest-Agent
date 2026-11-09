"""trace_spans 읽기: 트레이스 목록(맨 위 span + 요약), 한 트레이스의 span 전부, 에이전트별 통계."""

import json
import sqlite3
import time


def _loads(text: str | None):
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def span_dict(r: sqlite3.Row) -> dict:
    out = {k: r[k] for k in r.keys() if not k.endswith("_json")}
    out["input"] = _loads(r["input_json"])
    out["output"] = _loads(r["output_json"])
    out["attrs"] = _loads(r["attrs_json"]) or {}
    return out


def _summaries(conn: sqlite3.Connection, trace_ids: list[str]) -> dict[str, dict]:
    if not trace_ids:
        return {}
    marks = ",".join("?" * len(trace_ids))
    out: dict[str, dict] = {t: {"spans": 0, "llm_calls": 0, "tool_calls": 0, "errors": 0, "agents": [], "input_tokens": 0,
                                "output_tokens": 0, "cost_usd": 0.0, "running": 0} for t in trace_ids}
    rows = conn.execute(f"SELECT trace_id, kind, agent, status, attrs_json FROM trace_spans WHERE trace_id IN ({marks})", trace_ids)
    for r in rows:
        s = out[r["trace_id"]]
        s["spans"] += 1
        s["llm_calls"] += r["kind"] == "llm"
        s["tool_calls"] += r["kind"] == "tool"
        s["errors"] += r["status"] == "error"
        s["running"] += r["status"] == "running"
        if r["agent"] and r["agent"] not in s["agents"]:
            s["agents"].append(r["agent"])
        if r["kind"] == "llm":
            a = _loads(r["attrs_json"]) or {}
            s["input_tokens"] += int(a.get("input_tokens") or 0) + int(a.get("cache_read_tokens") or 0)
            s["output_tokens"] += int(a.get("output_tokens") or 0)
            s["cost_usd"] += float(a.get("cost_usd") or 0)
    for s in out.values():
        s["cost_usd"] = round(s["cost_usd"], 6)
    return out


def traces(conn: sqlite3.Connection, *, limit: int = 50, run_id: int | None = None, agent: str | None = None,
           before_ms: float | None = None) -> list[dict]:
    """맨 위 span(부모 없음) 최근 순 + 요약. agent 를 주면 그 에이전트가 한 번이라도 나온 트레이스만."""
    sql = "SELECT * FROM trace_spans WHERE parent_id IS NULL"
    args: list = []
    if run_id is not None:
        sql += " AND run_id = ?"
        args.append(run_id)
    if agent:
        sql += " AND trace_id IN (SELECT trace_id FROM trace_spans WHERE agent = ?)"
        args.append(agent)
    if before_ms is not None:
        sql += " AND start_ms < ?"
        args.append(before_ms)
    rows = [span_dict(r) for r in conn.execute(sql + " ORDER BY start_ms DESC LIMIT ?", (*args, limit))]
    sums = _summaries(conn, [r["trace_id"] for r in rows])
    for r in rows:
        r.pop("input", None)
        r.pop("output", None)
        r["summary"] = sums[r["trace_id"]]
    return rows


def trace(conn: sqlite3.Connection, trace_id: str) -> dict | None:
    rows = [span_dict(r) for r in conn.execute("SELECT * FROM trace_spans WHERE trace_id = ? ORDER BY start_ms, rowid", (trace_id,))]
    if not rows:
        return None
    root = next((r for r in rows if r["parent_id"] is None), rows[0])
    return {"trace_id": trace_id, "root": root, "spans": rows, "summary": _summaries(conn, [trace_id])[trace_id]}


def agent_stats(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    """에이전트마다: 차례 수 · LLM 호출 · 도구 호출 · 토큰 · 요금 · 실패 · 평균 지연 (최근 days 일)."""
    since = time.time() * 1000 - days * 86_400_000
    out: dict[str, dict] = {}
    for r in conn.execute("SELECT agent, kind, status, duration_ms, attrs_json FROM trace_spans WHERE agent IS NOT NULL "
                          "AND start_ms >= ?", (since,)):
        s = out.setdefault(r["agent"], {"agent": r["agent"], "turns": 0, "llm_calls": 0, "tool_calls": 0, "errors": 0,
                                        "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "_ms": []})
        if r["kind"] == "agent":
            s["turns"] += 1
            if r["duration_ms"] is not None:
                s["_ms"].append(r["duration_ms"])
        elif r["kind"] == "llm":
            s["llm_calls"] += 1
            a = _loads(r["attrs_json"]) or {}
            s["input_tokens"] += int(a.get("input_tokens") or 0) + int(a.get("cache_read_tokens") or 0)
            s["output_tokens"] += int(a.get("output_tokens") or 0)
            s["cost_usd"] += float(a.get("cost_usd") or 0)
        elif r["kind"] == "tool":
            s["tool_calls"] += 1
        s["errors"] += r["status"] == "error"
    for s in out.values():
        ms = s.pop("_ms")
        s["avg_turn_ms"] = int(sum(ms) / len(ms)) if ms else None
        s["cost_usd"] = round(s["cost_usd"], 6)
    return sorted(out.values(), key=lambda s: -s["turns"])
