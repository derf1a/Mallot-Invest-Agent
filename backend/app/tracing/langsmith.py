"""LangSmith 로 트레이스 내보내기 (선택).

켜는 조건 — 둘 다 있어야 한다: LANGSMITH_API_KEY + MIA_LANGSMITH_EXPORT=1.
내보내면 프롬프트(보유 종목 · 운용 목표 · 뉴스)와 답이 LangSmith 서버로 간다. 그래서 키만으로는 켜지지 않게 했다.

- 끝난 span 하나 = LangSmith run 하나. 우리 span 은 자식이 먼저 끝나므로, 부모의 시작 시각 · id 를 DB 에서 읽어
  dotted_order(조상 순서 문자열)를 직접 만들어 순서와 상관없이 트리가 맞게 한다.
- kind → run_type: llm → llm, tool → tool, 나머지(run · step · agent · event) → chain.
- LLM run 의 outputs 에 usage_metadata(토큰)와 metadata 에 ls_provider · ls_model_name 을 넣어 LangSmith 가 토큰 · 모델을 보여 주게.
- langsmith 패키지는 선택 설치 (`uv sync --extra langsmith`). 없으면 상태에 그렇게 적고 아무것도 하지 않는다.
"""

import logging
import os
import uuid
from datetime import UTC, datetime

from app.core import trace
from app.core.db import connect

log = logging.getLogger("trace.langsmith")

RUN_TYPES = {"llm": "llm", "tool": "tool"}
_state: dict = {"client": None, "error": None, "exported": 0}


def configured() -> bool:
    return bool(os.environ.get("LANGSMITH_API_KEY")) and os.environ.get("MIA_LANGSMITH_EXPORT") == "1"


def project() -> str:
    return os.environ.get("LANGSMITH_PROJECT") or "mallot-invest-agent"


def installed() -> bool:
    try:
        import langsmith  # noqa: F401
    except ImportError:
        return False
    return True


def status() -> dict:
    return {"configured": configured(), "installed": installed(), "active": _exporter in trace.EXPORTERS,
            "project": project(), "exported": _state["exported"], "error": _state["error"],
            "how": "LANGSMITH_API_KEY 와 MIA_LANGSMITH_EXPORT=1 을 .env 에 넣고 `uv sync --extra langsmith` 후 다시 시작"}


def _uuid(hex_id: str) -> uuid.UUID:
    return uuid.UUID(hex=hex_id)


def _time(ms: float) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _stamp(ms: float, hex_id: str) -> str:
    return _time(ms).strftime("%Y%m%dT%H%M%S%fZ") + str(_uuid(hex_id))


def ancestors(span_id: str) -> list[tuple[str, float]]:
    """뿌리부터 이 span 까지 (id, start_ms) — dotted_order 재료."""
    chain: list[tuple[str, float]] = []
    conn = connect()
    try:
        cur: str | None = span_id
        while cur:
            row = conn.execute("SELECT id, parent_id, start_ms FROM trace_spans WHERE id = ?", (cur,)).fetchone()
            if row is None:
                break
            chain.append((row["id"], row["start_ms"]))
            cur = row["parent_id"]
    finally:
        conn.close()
    return list(reversed(chain))


def to_run(span: dict, chain: list[tuple[str, float]]) -> dict:
    """우리 span → Client.create_run 인자."""
    a = span.get("attrs") or {}
    outputs: dict = {"output": span.get("output")}
    metadata = {"kind": span["kind"], "agent": span.get("agent"), "run_id": span.get("run_id"),
                **{k: v for k, v in a.items() if isinstance(v, (str, int, float, bool))}}
    if span["kind"] == "llm":
        tin = int(a.get("input_tokens") or 0) + int(a.get("cache_read_tokens") or 0)
        tout = int(a.get("output_tokens") or 0)
        outputs["usage_metadata"] = {"input_tokens": tin, "output_tokens": tout, "total_tokens": tin + tout}
        metadata.update(ls_provider=a.get("provider"), ls_model_name=a.get("model"))
    root_id = chain[0][0] if chain else span["id"]
    return {
        "id": _uuid(span["id"]),
        "trace_id": _uuid(root_id),
        "parent_run_id": _uuid(span["parent_id"]) if span.get("parent_id") else None,
        "dotted_order": ".".join(_stamp(ms, sid) for sid, ms in chain) if chain else _stamp(span["start_ms"], span["id"]),
        "name": span["name"] if span["kind"] != "agent" else f"agent:{span['name']}",
        "run_type": RUN_TYPES.get(span["kind"], "chain"),
        "inputs": span.get("input") if isinstance(span.get("input"), dict) else {"input": span.get("input")},
        "outputs": outputs,
        "error": span.get("error"),
        "start_time": _time(span["start_ms"]),
        "end_time": _time(span.get("end_ms") or span["start_ms"]),
        "extra": {"metadata": metadata},
        "tags": [span["kind"]] + ([span["agent"]] if span.get("agent") else []),
        "project_name": project(),
    }


def _exporter(span: dict) -> None:
    client = _state["client"]
    if client is None:
        return
    try:
        client.create_run(**to_run(span, ancestors(span["id"])))
        _state["exported"] += 1
        _state["error"] = None
    except Exception as e:  # noqa: BLE001 — 내보내기 실패가 앱을 멈추면 안 된다
        _state["error"] = f"{type(e).__name__}: {e}"[:300]
        log.warning("langsmith export failed: %s", _state["error"])


def start(client=None) -> bool:
    """켤 수 있으면 내보내기를 건다. client 를 주면 그것을 쓴다 (테스트)."""
    if client is None:
        if not configured() or not installed():
            return False
        from langsmith import Client

        client = Client()
    _state["client"] = client
    if _exporter not in trace.EXPORTERS:
        trace.EXPORTERS.append(_exporter)
    return True


def stop() -> None:
    if _exporter in trace.EXPORTERS:
        trace.EXPORTERS.remove(_exporter)
    client, _state["client"] = _state["client"], None
    flush = getattr(client, "flush", None)
    if callable(flush):
        try:
            flush()
        except Exception:  # noqa: BLE001
            log.exception("langsmith flush failed")
