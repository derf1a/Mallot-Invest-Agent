"""LLM 호출 공통 모듈 — 앱의 모든 LLM 호출은 structured() 또는 agentic() 을 거치고, 호출마다 사용량이 llm_usage 에 남는다.

- structured(): 도구 없이 구조화 출력(JSON Schema) 응답 하나.
- agentic(): 앱이 정한 도구(ToolDef)만 쓸 수 있는 에이전트 — 도구를 여러 번 부른 뒤 구조화 출력으로 끝낸다.
  Claude Code 기본 도구(셸·파일 등)는 모두 끄고(tools=[]), 다른 MCP 설정도 불러오지 않는다(strict_mcp_config).

연결: Claude Agent SDK (로컬 Claude Code CLI). ANTHROPIC_API_KEY 가 있으면 API 키, 없으면 Claude Code 로그인(구독)을 쓴다.
     또는 로컬 LLM (mlx-lm, app/llm/local.py·loop.py). 공급자 고르기:
     - model 을 비우면 설정 > LLM 의 기본 LLM, model='local' 이면 로컬, 'claude-…' 면 그 Claude 모델.
     - 이미지가 든 프롬프트(캡쳐 판독)는 로컬 모델이 읽지 못해 언제나 Claude.
기록: 기능, 공급자, 모델, input/output/캐시 토큰, SDK 가 알려주는 API 요금 환산액, 지연, 턴 수, 성공·실패, 실행(run) id.
"""

import base64
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass

from app.core.db import connect

log = logging.getLogger("llm")

PROVIDER = "claude-agent-sdk"
DEFAULT_MODEL = "claude-opus-5-5"
LOCAL = "local"  # 기능별 모델 선택에서 '로컬 LLM' 을 뜻하는 값

# 기능 키 → 화면 이름
FEATURES: dict[str, str] = {
    "news_analysis": "뉴스 종목 분석",
    "news_report": "뉴스 리포트",
    "agent_action": "에이전틱 매매",
    "invest_assistant": "투자 비서",
    "invest_desk": "운용 데스크 리서치",
}

IMAGE_FEATURES: set[str] = set()  # 이미지를 읽어야 해서 로컬 LLM 을 골라도 Claude 로 가는 기능 (지금은 없음)

# 실행(run) 안에서 부르면 그 실행 id 를 함께 기록한다 (runner.execute 가 설정)
CURRENT_RUN: ContextVar[int | None] = ContextVar("run_id", default=None)


class LLMError(Exception):
    """LLM 호출 실패 (사용자에게 보여줄 메시지)."""


def default_model(model: str | None = None) -> str:
    """Claude 로 보낼 모델 (로컬을 가리키는 값이면 Claude 기본 모델)."""
    return model if model and model != LOCAL else os.environ.get("MIA_LLM_MODEL", DEFAULT_MODEL)


def uses_local(model: str | None = None, has_image: bool = False) -> bool:
    if has_image:
        return False
    if model:
        return model == LOCAL
    from app.llm import local

    return local.provider() == "local"


def model_label(model: str | None = None) -> str:
    """실제로 쓸 모델 이름 (로그·화면용)."""
    if uses_local(model):
        from app.llm import local

        return f"{local.model_name()} (로컬)"
    return default_model(model)


def auth_mode() -> str:
    """Agent SDK 인증 방식: API 키 환경변수가 있으면 api_key, 아니면 Claude Code 로그인."""
    return "api_key" if os.environ.get("ANTHROPIC_API_KEY") else "claude_login"


def image_block(image: bytes, media_type: str) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                        "data": base64.standard_b64encode(image).decode()}}


def record(*, feature: str, model: str, usage: dict | None, cost_usd: float | None, duration_ms: int,
           api_duration_ms: int | None, turns: int | None, ok: bool, error: str | None, provider: str = PROVIDER) -> None:
    """사용량 한 건 저장. 기록 실패가 본 기능을 막으면 안 되므로 오류는 로그만 남긴다."""
    u = usage or {}
    try:
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO llm_usage (feature, provider, model, input_tokens, output_tokens, cache_read_tokens, "
                "cache_write_tokens, cost_usd, duration_ms, api_duration_ms, turns, ok, error, run_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (feature, provider, model, int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
                 int(u.get("cache_read_input_tokens") or 0), int(u.get("cache_creation_input_tokens") or 0),
                 cost_usd, duration_ms, api_duration_ms, turns, int(ok), (error or "")[:500] or None, CURRENT_RUN.get()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        log.exception("llm usage record failed")


async def _run(feature: str, prompt: str | list[dict], options, model: str) -> dict:
    """SDK 질의를 끝까지 돌리고 사용량을 기록한다. 구조화 출력(dict)을 돌려주고, 실패하면 LLMError."""
    from claude_agent_sdk import query
    from claude_agent_sdk.types import ResultMessage

    async def blocks() -> AsyncIterator[dict]:
        yield {"type": "user", "message": {"role": "user", "content": prompt}}

    started = time.perf_counter()
    result: ResultMessage | None = None
    error: str | None = None
    try:
        async for message in query(prompt=blocks() if isinstance(prompt, list) else prompt, options=options):
            if isinstance(message, ResultMessage):
                result = message
    except Exception as e:  # noqa: BLE001 — CLI 없음·프로세스 오류도 기록하고 LLMError 로 바꾼다
        error = f"{type(e).__name__}: {e}"
    ok = error is None and result is not None and not result.is_error and isinstance(result.structured_output, dict)
    if not ok and error is None:
        error = str((result.errors or result.result) if result else "응답 없음")
    actual_model = next(iter(result.model_usage), model) if result and result.model_usage else model
    record(
        feature=feature,
        model=actual_model,
        usage=result.usage if result else None,
        cost_usd=result.total_cost_usd if result else None,
        duration_ms=int((time.perf_counter() - started) * 1000),
        api_duration_ms=result.duration_api_ms if result else None,
        turns=result.num_turns if result else None,
        ok=ok,
        error=error,
    )
    if not ok:
        raise LLMError(error or "응답 없음")
    assert result is not None
    return result.structured_output


async def structured(
    feature: str,
    prompt: str | list[dict],
    schema: dict,
    *,
    model: str | None = None,
    system_prompt: str | None = None,
    max_turns: int = 3,
) -> dict:
    """구조화 출력(JSON Schema)으로 응답 하나를 받는다. prompt 가 리스트면 이미지 등 content 블록."""
    has_image = isinstance(prompt, list) and any(b.get("type") == "image" for b in prompt)
    if uses_local(model, has_image):
        from app.llm import local, loop

        return await loop.structured(feature, prompt, schema, backend=local.MlxBackend(), system_prompt=system_prompt,
                                     max_turns=max_turns)
    from claude_agent_sdk import ClaudeAgentOptions

    model = default_model(model)
    options = ClaudeAgentOptions(
        model=model,
        system_prompt=system_prompt,
        allowed_tools=[],
        disallowed_tools=["WebSearch", "WebFetch"],  # 넘겨준 자료만 근거로
        max_turns=max_turns,
        setting_sources=[],  # 프로젝트/사용자 CLAUDE.md, 스킬 등을 불러오지 않는다
        output_format={"type": "json_schema", "schema": schema},
    )
    return await _run(feature, prompt, options, model)


@dataclass
class ToolDef:
    """에이전트에게 주는 도구 하나. handler 는 입력(dict)을 받아 JSON 으로 바꿀 수 있는 값을 돌려준다."""
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], Awaitable[object]]
    read_only: bool = True


MCP_SERVER = "mallot"


def tool_id(name: str) -> str:
    return f"mcp__{MCP_SERVER}__{name}"


async def agentic(
    feature: str,
    prompt: str,
    schema: dict,
    *,
    tools: list[ToolDef],
    model: str | None = None,
    system_prompt: str | None = None,
    max_turns: int = 10,
) -> dict:
    """앱 도구만 쓰는 에이전트 실행 → 구조화 출력. 도구 오류는 에이전트에게 오류 결과로 돌려준다."""
    if uses_local(model):
        from app.llm import local, loop

        return await loop.agentic(feature, prompt, schema, tools=tools, backend=local.MlxBackend(),
                                  system_prompt=system_prompt, max_turns=max_turns)
    from claude_agent_sdk import ClaudeAgentOptions, ToolAnnotations, create_sdk_mcp_server, tool

    sdk_tools = []
    for t in tools:
        async def run(args: dict, _t: ToolDef = t) -> dict:
            try:
                value = await _t.handler(args or {})
                return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=str)}]}
            except Exception as e:  # noqa: BLE001 — 에이전트가 읽고 판단하도록 오류를 결과로 돌려준다
                return {"content": [{"type": "text", "text": f"오류: {e}"}], "is_error": True}
        hints = ToolAnnotations(readOnlyHint=t.read_only, destructiveHint=not t.read_only)
        sdk_tools.append(tool(t.name, t.description, t.input_schema, annotations=hints)(run))
    model = default_model(model)
    options = ClaudeAgentOptions(
        model=model,
        system_prompt=system_prompt,
        tools=[],  # Claude Code 기본 도구(Bash·Read·Write·WebFetch 등) 전부 끔
        mcp_servers={MCP_SERVER: create_sdk_mcp_server(MCP_SERVER, tools=sdk_tools)},
        strict_mcp_config=True,  # 사용자·프로젝트의 다른 MCP 서버를 불러오지 않는다
        allowed_tools=[tool_id(t.name) for t in tools],
        max_turns=max_turns,
        setting_sources=[],
        output_format={"type": "json_schema", "schema": schema},
    )
    return await _run(feature, prompt, options, model)
