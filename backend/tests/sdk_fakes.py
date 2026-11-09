"""Claude Agent SDK 가짜 — 실제 CLI·네트워크 없이 structured()·agentic() 을 시험한다."""

from claude_agent_sdk.types import ResultMessage

from app.core.db import connect


def fake_query(result: ResultMessage | None = None, raises: Exception | None = None, seen: list | None = None):
    async def _query(*, prompt, options):
        if seen is not None:
            seen.append(options)
        if raises:
            raise raises
        yield result
    return _query


def ok_result(**kw) -> ResultMessage:
    base = dict(subtype="success", duration_ms=1200, duration_api_ms=900, is_error=False, num_turns=1, session_id="s",
                total_cost_usd=0.0123, structured_output={"answer": 1}, model_usage={"claude-opus-5-5": {}},
                usage={"input_tokens": 1500, "output_tokens": 300, "cache_read_input_tokens": 50,
                       "cache_creation_input_tokens": 10})
    return ResultMessage(**{**base, **kw})


def usage_rows() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM llm_usage ORDER BY id")]
