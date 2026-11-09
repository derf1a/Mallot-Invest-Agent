"""호출 추적 — 실행(run) · 단계(라운드 · 대화) · 에이전트 차례 · LLM 호출 · 도구 호출 · 사건(게이트 · 주문)을
부모-자식 span 으로 남긴다. LangSmith 의 run tree 와 같은 모양이라 그대로 내보낼 수도 있다 (app/tracing/langsmith.py).

- span 은 시작할 때 한 줄(status running)을 쓰고 끝날 때 고친다 → 화면에서 진행 중인 호출도 보인다.
- 부모는 contextvars 로 이어진다: asyncio 태스크 · asyncio.to_thread · asyncio.run 은 문맥을 복사하므로 병렬 발언도 각자 부모를 안다.
  문맥이 끊기는 곳(도구 콜백 등)은 parent= 로 직접 넘긴다.
- 기록 실패는 본 기능을 막지 않는다 (로그만). MIA_TRACE=0 이면 아무것도 남기지 않는다.
- 입력 · 출력은 잘라서 남긴다 (CLIP 자). 비밀값은 애초에 프롬프트에 넣지 않는다.
"""

import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from app.core.db import connect

log = logging.getLogger("trace")

CLIP = 20_000
KINDS = ("run", "step", "agent", "llm", "tool", "event")


@dataclass
class Span:
    id: str
    trace_id: str
    parent_id: str | None
    kind: str
    name: str
    agent: str | None = None
    run_id: int | None = None
    start_ms: float = 0.0
    input: Any = None
    output: Any = None
    attrs: dict = field(default_factory=dict)
    status: str = "running"
    error: str | None = None
    end_ms: float | None = None

    def set(self, **attrs: Any) -> None:
        self.attrs.update({k: v for k, v in attrs.items() if v is not None})

    def as_dict(self) -> dict:
        return {"id": self.id, "trace_id": self.trace_id, "parent_id": self.parent_id, "kind": self.kind, "name": self.name,
                "agent": self.agent, "run_id": self.run_id, "start_ms": self.start_ms, "end_ms": self.end_ms,
                "input": self.input, "output": self.output, "attrs": self.attrs, "status": self.status, "error": self.error}


CURRENT: ContextVar[Span | None] = ContextVar("trace_span", default=None)

# 끝난 span 을 받는 곳 (LangSmith 내보내기 등). 실패해도 다른 곳에 영향이 없게 각각 감싼다.
EXPORTERS: list[Callable[[dict], None]] = []


def enabled() -> bool:
    return os.environ.get("MIA_TRACE", "1") != "0"


def current() -> Span | None:
    return CURRENT.get()


def _now_ms() -> float:
    return time.time() * 1000


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = json.dumps(str(value), ensure_ascii=False)
    return text if len(text) <= CLIP else json.dumps({"clipped": True, "head": text[:CLIP]}, ensure_ascii=False)


def _write(sql: str, args: tuple) -> None:
    try:
        conn = connect()
        try:
            conn.execute(sql, args)
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — 추적 기록이 본 기능을 막으면 안 된다
        log.exception("trace write failed")


def _insert(sp: Span) -> None:
    _write("INSERT INTO trace_spans (id, trace_id, parent_id, run_id, kind, name, agent, status, start_ms, input_json, attrs_json) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
           (sp.id, sp.trace_id, sp.parent_id, sp.run_id, sp.kind, sp.name, sp.agent, sp.status, sp.start_ms, _dump(sp.input),
            _dump(sp.attrs) or "{}"))


def _finish(sp: Span) -> None:
    sp.end_ms = sp.end_ms or _now_ms()
    _write("UPDATE trace_spans SET status = ?, end_ms = ?, duration_ms = ?, output_json = ?, attrs_json = ?, error = ? "
           "WHERE id = ?",
           (sp.status, sp.end_ms, int(sp.end_ms - sp.start_ms), _dump(sp.output), _dump(sp.attrs) or "{}",
            (sp.error or "")[:2000] or None, sp.id))
    for export in list(EXPORTERS):
        try:
            export(sp.as_dict())
        except Exception:  # noqa: BLE001
            log.exception("trace export failed")


def _new(kind: str, name: str, *, agent: str | None, input: Any, attrs: dict | None, run_id: int | None,
         parent: Span | None) -> Span:
    parent = parent if parent is not None else CURRENT.get()
    sid = uuid.uuid4().hex
    return Span(id=sid, trace_id=parent.trace_id if parent else sid, parent_id=parent.id if parent else None, kind=kind,
                name=name[:200], agent=agent if agent is not None else (parent.agent if parent else None),
                run_id=run_id if run_id is not None else (parent.run_id if parent else None), start_ms=_now_ms(),
                input=input, attrs=dict(attrs or {}))


@contextmanager
def span(kind: str, name: str, *, agent: str | None = None, input: Any = None, attrs: dict | None = None,
         run_id: int | None = None, parent: Span | None = None) -> Iterator[Span]:
    """with trace.span("llm", "agent_action", input=...) as sp: ... sp.output = 결과 / sp.set(tokens=…).
    예외는 그대로 올리고 span 에는 error 로 남긴다."""
    sp = _new(kind, name, agent=agent, input=input, attrs=attrs, run_id=run_id, parent=parent)
    if not enabled():
        token = CURRENT.set(sp)
        try:
            yield sp
        finally:
            CURRENT.reset(token)
        return
    _insert(sp)
    token = CURRENT.set(sp)
    try:
        yield sp
        if sp.status == "running":
            sp.status = "ok"
    except BaseException as e:
        sp.status = "cancelled" if type(e).__name__ in ("Cancelled", "CancelledError") else "error"
        sp.error = f"{type(e).__name__}: {e}"
        raise
    finally:
        CURRENT.reset(token)
        _finish(sp)


def event(name: str, data: Any = None, *, parent: Span | None = None, agent: str | None = None) -> None:
    """길이 없는 사건 하나 (게이트 결과 · 주문 · 관리자 말)."""
    if not enabled():
        return
    sp = _new("event", name, agent=agent, input=None, attrs=None, run_id=None, parent=parent)
    sp.output, sp.status, sp.end_ms = data, "ok", sp.start_ms
    _insert(sp)
    _finish(sp)


def annotate(**attrs: Any) -> None:
    """지금 span 에 속성을 더한다 (예: LLM 사용량 기록이 토큰 · 요금을 붙인다)."""
    if sp := CURRENT.get():
        sp.set(**attrs)


P = ParamSpec("P")
R = TypeVar("R")


def traced(kind: str, name: Callable[..., str] | str) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """async 함수를 span 하나로 감싼다. name 이 함수면 같은 인자로 불러 이름을 만든다."""
    def deco(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(fn)
        async def run(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                label = name(*args, **kwargs) if callable(name) else name
            except Exception:  # noqa: BLE001 — 이름 만들기가 실패해도 본 함수는 돈다
                label = fn.__name__
            with span(kind, label):
                return await fn(*args, **kwargs)
        return run
    return deco


def prune(days: int = 30) -> int:
    """오래된 span 지우기 (서버 시작 때)."""
    cutoff = _now_ms() - days * 86_400_000
    try:
        conn = connect()
        try:
            n = conn.execute("DELETE FROM trace_spans WHERE start_ms < ?", (cutoff,)).rowcount
            conn.commit()
            return n
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        log.exception("trace prune failed")
        return 0
