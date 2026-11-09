"""채팅 API 하나로 structured()·agentic() 을 흉내 내는 루프 — 로컬 MLX · vLLM 이 같이 쓴다 (client.py 가 경로에 따라 부른다).

- 구조화 출력: 스키마를 시스템 프롬프트에 넣고, 답에서 JSON 을 꺼내 스키마를 검사한다. 틀리면 무엇이 틀렸는지 알려 주고
  다시 받는다 (max_turns 안에서). 서버가 형식을 강제할 수 있으면(vLLM response_format) 그것도 쓴다 — 검사는 그대로.
- 도구: 백엔드의 도구 호출 형식으로 앱 도구(ToolDef)를 주고, 호출이 오면 이 프로세스에서 실행해 결과를 돌려준다.
  도구 호출이 없는 답이 최종 답이다. 이전 턴을 어떻게 다시 넣을지는 백엔드가 정한다 (MLX 는 생성된 글 그대로 → KV 캐시 재사용).
- 사용량은 호출(여러 턴) 하나를 한 건으로 남긴다: 새로 읽은 입력 토큰 / 재사용한 캐시 토큰 / 출력 토큰, 요금 없음.
"""

import json
import time
from typing import Protocol

from app.llm.client import LLMError, ToolDef, record


class Backend(Protocol):
    """채팅 한 턴을 보내는 쪽. chat() 은 정규화한 dict 를 돌려준다:
    content · reasoning · tool_calls[{id?, name, arguments, error?}] · tool_call_errors · usage{prompt_tokens, cached_tokens,
    output_tokens} · timing{prefill_ms, generate_ms} · finish_reason."""

    provider: str                     # llm_usage.provider ('mlx-lm' · 'vllm')
    supports_response_format: bool    # 서버가 JSON Schema 로 출력을 강제할 수 있나

    def model_name(self) -> str: ...
    def max_tokens(self) -> int: ...
    def temperature(self) -> float | None: ...
    async def chat(self, messages: list[dict], *, tools: list[dict] | None = None, max_tokens: int = 4096,
                   temperature: float | None = None, response_format: dict | None = None) -> dict: ...
    def assistant_message(self, res: dict) -> dict: ...
    def tool_message(self, call: dict | None, content: str) -> dict: ...


TOOL_RESULT_CHARS = 12_000  # 로컬 모델은 문맥이 작아 도구 결과 하나가 KV 캐시를 다 먹지 않게 자른다


# ---------- JSON ----------

def extract_json(text: str) -> object:
    """답에서 JSON 값을 꺼낸다: 그대로 → 코드 블록 안 → 첫 '{' 부터 읽을 수 있는 만큼."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    decoder = json.JSONDecoder(strict=False)  # 문자열 안의 줄바꿈 같은 제어 문자도 받는다 (모델이 자주 그대로 쓴다)
    try:
        return decoder.decode(t)
    except json.JSONDecodeError:
        pass
    start = t.find("{")
    if start < 0:
        raise ValueError("JSON 객체가 없어요")
    try:
        value, _ = decoder.raw_decode(t[start:])
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 을 읽지 못했어요: {e.msg} (위치 {e.pos})") from None
    return value


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is(value, t: str) -> bool:
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool) or (isinstance(value, float) and value.is_integer())
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES.get(t, object))


def check(value, schema: dict, path: str = "$") -> list[str]:
    """앱이 쓰는 JSON Schema 부분집합 검사. 문제 목록 (비면 통과). additionalProperties=false 인 객체의 모르는 키는 지운다."""
    if "anyOf" in schema:
        return [] if any(not check(value, s, path) for s in schema["anyOf"]) else [f"{path}: 허용된 형식 중 어느 것과도 맞지 않음"]
    types = schema.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else types
        if not any(_is(value, t) for t in types):
            return [f"{path}: {'/'.join(types)} 이어야 함 (받은 값 {json.dumps(value, ensure_ascii=False)[:60]})"]
    errors: list[str] = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {schema['enum']} 중 하나여야 함 (받은 값 {value!r})")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        errors += [f"{path}.{k}: 빠짐" for k in schema.get("required", []) if k not in value]
        if schema.get("additionalProperties") is False:
            for k in [k for k in value if k not in props]:
                del value[k]
        for k, sub in props.items():
            if k in value:
                errors += check(value[k], sub, f"{path}.{k}")
    elif isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: 항목이 {schema['minItems']}개 이상이어야 함")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: 항목이 {schema['maxItems']}개 이하여야 함")
        for i, item in enumerate(value):
            errors += check(item, schema.get("items", {}), f"{path}[{i}]")
    elif _is(value, "number"):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {schema['minimum']} 이상이어야 함")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {schema['maximum']} 이하여야 함")
    return errors


def format_rules(schema: dict) -> str:
    return ("## 출력 형식\n최종 답은 아래 JSON Schema 에 맞는 JSON 객체 하나만 출력하세요. 마크다운 코드 블록·설명·앞뒤 글 없이 "
            "'{' 로 시작해 '}' 로 끝나야 합니다. 필수(required) 항목은 모두 채우고, enum 은 목록의 값 그대로 씁니다.\n"
            f"<schema>\n{json.dumps(schema, ensure_ascii=False)}\n</schema>")


def _parse(text: str, schema: dict) -> tuple[dict | None, list[str]]:
    try:
        value = extract_json(text)
    except ValueError as e:
        return None, [str(e)]
    if not isinstance(value, dict):
        return None, ["최상위 값은 JSON 객체여야 해요"]
    errors = check(value, schema)
    return (value, []) if not errors else (None, errors)


def _errors(res: dict, errors: list[str]) -> list[str]:
    if res.get("finish_reason") == "repetition":
        return ["같은 말을 되풀이하다 멈췄어요", *errors]
    if res.get("finish_reason") == "length":
        return ["출력 길이 한도에 걸려 끝까지 쓰지 못했어요 — 더 짧게 쓰세요", *errors]
    return errors


def _fix_request(errors: list[str]) -> str:
    listed = "\n".join(f"- {e}" for e in errors[:8])
    return f"앞 답을 받아들일 수 없어요:\n{listed}\n설명 없이 스키마에 맞는 JSON 객체 하나만 다시 출력하세요."


def _text(prompt: str | list[dict]) -> str:
    if isinstance(prompt, str):
        return prompt
    return "\n\n".join(b.get("text", "") for b in prompt if b.get("type") == "text")


class _Usage:
    def __init__(self):
        self.input = self.cached = self.output = self.turns = self.gen_ms = 0
        self.started = time.perf_counter()

    def add(self, res: dict) -> None:
        u = res.get("usage") or {}
        self.input += max(0, int(u.get("prompt_tokens") or 0) - int(u.get("cached_tokens") or 0))
        self.cached += int(u.get("cached_tokens") or 0)
        self.output += int(u.get("output_tokens") or 0)
        t = res.get("timing") or {}
        self.gen_ms += int(t.get("prefill_ms") or 0) + int(t.get("generate_ms") or 0)
        self.turns += 1

    def save(self, backend: Backend, feature: str, ok: bool, error: str | None) -> None:
        record(feature=feature, model=backend.model_name(), provider=backend.provider,
               usage={"input_tokens": self.input, "output_tokens": self.output, "cache_read_input_tokens": self.cached},
               cost_usd=None, duration_ms=int((time.perf_counter() - self.started) * 1000),
               api_duration_ms=self.gen_ms or None, turns=self.turns, ok=ok, error=error)


# ---------- structured / agentic ----------

async def structured(feature: str, prompt: str | list[dict], schema: dict, *, backend: Backend,
                     system_prompt: str | None = None, max_turns: int = 3) -> dict:
    usage = _Usage()
    fmt = ({"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}
           if backend.supports_response_format else None)
    messages = [{"role": "system", "content": "\n\n".join(p for p in (system_prompt, format_rules(schema)) if p)},
                {"role": "user", "content": _text(prompt)}]
    errors: list[str] = []
    try:
        for _ in range(max(1, max_turns)):
            kw = {"response_format": fmt} if fmt else {}
            res = await backend.chat(messages, max_tokens=backend.max_tokens(), temperature=backend.temperature(), **kw)
            usage.add(res)
            value, errors = _parse(res["content"], schema)
            if value is not None:
                usage.save(backend, feature, True, None)
                return value
            errors = _errors(res, errors)
            messages += [backend.assistant_message(res), {"role": "user", "content": _fix_request(errors)}]
    except LLMError as e:
        usage.save(backend, feature, False, str(e))
        raise
    error = "형식에 맞는 답을 받지 못했어요: " + "; ".join(errors[:3])
    usage.save(backend, feature, False, error)
    raise LLMError(error)


TOOL_RULES = ("## 도구\n필요한 자료는 주어진 도구로 확인하세요. 도구를 부를 때는 도구 호출만 쓰고, 결과를 본 뒤 이어서 판단합니다. "
              "더 확인할 것이 없으면 도구를 부르지 말고 최종 답만 출력하세요.")


async def agentic(feature: str, prompt: str, schema: dict, *, tools: list[ToolDef], backend: Backend,
                  system_prompt: str | None = None, max_turns: int = 10) -> dict:
    usage = _Usage()
    by_name = {t.name: t for t in tools}
    specs = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                               "parameters": t.input_schema}} for t in tools]
    messages = [{"role": "system", "content": "\n\n".join(p for p in (system_prompt, TOOL_RULES, format_rules(schema)) if p)},
                {"role": "user", "content": prompt}]
    errors: list[str] = []
    try:
        for turn in range(max(1, max_turns)):
            if turn == max_turns - 1 and turn > 0:
                messages.append({"role": "user", "content": "도구는 그만 쓰고 지금까지 본 자료로 최종 JSON 만 출력하세요."})
            res = await backend.chat(messages, tools=specs or None, max_tokens=backend.max_tokens(),
                                     temperature=backend.temperature())
            usage.add(res)
            calls, call_errors = res.get("tool_calls") or [], res.get("tool_call_errors") or []
            if calls or call_errors:
                messages.append(backend.assistant_message(res))
                for call in calls:
                    content = (f"오류: 도구 호출 형식이 틀렸어요 — {call['error']}" if call.get("error")
                               else await _run_tool(by_name, call))
                    messages.append(backend.tool_message(call, content))
                for err in call_errors:
                    messages.append(backend.tool_message(None, f"오류: 도구 호출 형식이 틀렸어요 — {err}"))
                continue
            value, errors = _parse(res["content"], schema)
            if value is not None:
                usage.save(backend, feature, True, None)
                return value
            errors = _errors(res, errors)
            messages += [backend.assistant_message(res), {"role": "user", "content": _fix_request(errors)}]
    except LLMError as e:
        usage.save(backend, feature, False, str(e))
        raise
    error = f"{max_turns}턴 안에 형식에 맞는 최종 답을 받지 못했어요" + (": " + "; ".join(errors[:3]) if errors else "")
    usage.save(backend, feature, False, error)
    raise LLMError(error)


async def _run_tool(by_name: dict[str, ToolDef], call: dict) -> str:
    tool = by_name.get(call.get("name") or "")
    if tool is None:
        return f"오류: 없는 도구예요 ({call.get('name')}). 쓸 수 있는 도구: {', '.join(by_name)}"
    args = call.get("arguments") or {}
    try:
        value = await tool.handler(args if isinstance(args, dict) else {})
    except Exception as e:  # noqa: BLE001 — Claude 경로처럼 모델이 읽고 판단하도록 오류를 결과로 돌려준다
        return f"오류: {e}"
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= TOOL_RESULT_CHARS else text[:TOOL_RESULT_CHARS] + " …(잘림)"
