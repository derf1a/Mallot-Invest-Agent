"""vLLM 백엔드 — OpenAI 호환 /v1/chat/completions 를 부른다 (vLLM · SGLang · LM Studio · Ollama 등 같은 API 면 다 된다).

- 주소 · 키는 .env 로만 (MIA_VLLM_URL · MIA_VLLM_API_KEY). 화면에서 주소를 바꿀 수 있으면 같은 네트워크의 다른 기기가
  에이전트 프롬프트(보유 종목 · 운용 목표)를 다른 서버로 보내게 만들 수 있어서다. 모델 이름만 설정에서 고른다.
- 구조화 출력은 response_format(json_schema)로 서버에 강제하고, 앱 쪽 검사(loop.check)도 그대로 한다.
- 도구 호출은 OpenAI 형식(tools · tool_calls · tool_call_id). vLLM 은 서버를 `--enable-auto-tool-choice --tool-call-parser <모델에 맞는 것>`
  으로 띄워야 도구를 부른다.
- 이 서버는 보통 내 컴퓨터나 집 안 네트워크에 있으므로 공인 주소 검사(core.net)를 하지 않는다 — 주소는 사용자가 .env 에 직접 쓴 값뿐.
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from app.core.db import connect
from app.llm.client import LLMError

S_VLLM = "llm_vllm"
TIMEOUT = 600


def _setting() -> dict:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (S_VLLM,)).fetchone()
    finally:
        conn.close()
    try:
        return json.loads(row["value"]) if row else {}
    except (TypeError, ValueError):
        return {}


def _put(value: dict) -> None:
    conn = connect()
    try:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (S_VLLM, json.dumps(value, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()


def base_url() -> str:
    """끝의 / 를 뗀 주소 (예: http://127.0.0.1:8001/v1). 비면 vLLM 을 쓸 수 없다."""
    return os.environ.get("MIA_VLLM_URL", "").strip().rstrip("/")


def default_model() -> str:
    """경로에 모델 이름이 없을 때 쓸 모델: 설정 → MIA_VLLM_MODEL → 서버의 첫 모델."""
    return _setting().get("model") or os.environ.get("MIA_VLLM_MODEL", "").strip()


def set_default_model(model: str) -> dict:
    model = model.strip()
    if len(model) > 200:
        raise ValueError("모델 이름이 너무 길어요")
    _put({**_setting(), "model": model})
    return status()


# ---------- HTTP (테스트에서 바꿔 끼운다) ----------

def _http(method: str, url: str, body: dict | None = None, timeout: float = TIMEOUT) -> tuple[int, bytes]:
    headers = {"Content-Type": "application/json"}
    if key := os.environ.get("MIA_VLLM_API_KEY", "").strip():
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — 주소는 .env 의 사용자 값뿐
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, str(e).encode()


HTTP: Callable[..., tuple[int, bytes]] = _http


def _call(method: str, path: str, body: dict | None = None, timeout: float = TIMEOUT) -> dict:
    url = base_url()
    if not url:
        raise LLMError("vLLM 주소가 없어요 — .env 에 MIA_VLLM_URL 을 넣으세요 (예: http://127.0.0.1:8001/v1)")
    status, raw = HTTP(method, url + path, body, timeout)
    if status == 0:
        raise LLMError(f"vLLM 연결 실패: {raw.decode(errors='replace')[:200]}")
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        data = {}
    if status >= 400:
        err = data.get("error") if isinstance(data, dict) else None
        msg = (err.get("message") if isinstance(err, dict) else err) or data.get("message") or raw.decode(errors="replace")
        raise LLMError(f"vLLM 오류 {status}: {str(msg)[:300]}")
    return data


def models(timeout: float = 5) -> list[str]:
    return [m.get("id", "") for m in _call("GET", "/models", timeout=timeout).get("data", []) if m.get("id")]


def status() -> dict:
    """화면용: 주소(키 없이) · 기본 모델 · 서버가 가진 모델 · 연결 상태."""
    out = {"configured": bool(base_url()), "url": base_url(), "has_key": bool(os.environ.get("MIA_VLLM_API_KEY")),
           "model": default_model(), "models": [], "reachable": False, "error": None}
    if not out["configured"]:
        return out
    try:
        out["models"] = models()
        out["reachable"] = True
    except LLMError as e:
        out["error"] = str(e)
    return out


def _resolve_model(model: str | None) -> str:
    if model:
        return model
    if m := default_model():
        return m
    found = models()
    if not found:
        raise LLMError("vLLM 서버에 모델이 없어요")
    return found[0]


def normalize(data: dict) -> dict:
    """OpenAI 응답 → loop 가 읽는 모양."""
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("vLLM 응답에 choices 가 없어요")
    choice = choices[0]
    msg = choice.get("message") or {}
    calls, raw_calls = [], msg.get("tool_calls") or []
    for tc in raw_calls:
        fn = tc.get("function") or {}
        call = {"id": tc.get("id") or "", "name": fn.get("name") or ""}
        try:
            args = json.loads(fn.get("arguments") or "{}")
            call["arguments"] = args if isinstance(args, dict) else {}
        except ValueError as e:
            call.update(arguments={}, error=f"인자가 JSON 이 아니에요 ({e})")
        calls.append(call)
    usage = data.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    return {
        "content": msg.get("content") or "",
        "reasoning": msg.get("reasoning_content") or msg.get("reasoning") or "",
        "tool_calls": calls,
        "raw_tool_calls": raw_calls,
        "usage": {"prompt_tokens": int(usage.get("prompt_tokens") or 0),
                  "cached_tokens": int(details.get("cached_tokens") or 0),
                  "output_tokens": int(usage.get("completion_tokens") or 0)},
        "timing": {},
        "finish_reason": choice.get("finish_reason"),
        "model": data.get("model"),
    }


async def chat(messages: list[dict], *, model: str | None = None, tools: list[dict] | None = None,
               max_tokens: int = 4096, temperature: float | None = None, response_format: dict | None = None) -> dict:
    name = await asyncio.to_thread(_resolve_model, model)
    body: dict = {"model": name, "messages": messages, "max_tokens": max_tokens}
    if tools:
        body.update(tools=tools, tool_choice="auto")
    if temperature is not None:
        body["temperature"] = temperature
    if response_format:
        body["response_format"] = response_format
    started = time.perf_counter()
    data = await asyncio.to_thread(_call, "POST", "/chat/completions", body)
    out = normalize(data)
    out["timing"] = {"generate_ms": int((time.perf_counter() - started) * 1000)}
    return out


class VllmBackend:
    """loop.structured·agentic 이 쓰는 vLLM 백엔드."""

    provider = "vllm"
    supports_response_format = True

    def __init__(self, model: str | None = None):
        self.model = model or None
        self._used = model or ""

    def model_name(self) -> str:
        return self._used or default_model() or "vllm"

    def max_tokens(self) -> int:
        return 4096

    def temperature(self) -> float | None:
        return 0.2

    async def chat(self, messages: list[dict], *, tools: list[dict] | None = None, max_tokens: int = 4096,
                   temperature: float | None = None, response_format: dict | None = None) -> dict:
        res = await chat(messages, model=self.model, tools=tools, max_tokens=max_tokens, temperature=temperature,
                         response_format=response_format)
        self._used = res.get("model") or self._used
        return res

    def assistant_message(self, res: dict) -> dict:
        msg: dict = {"role": "assistant", "content": res["content"] or None}
        if res.get("raw_tool_calls"):
            msg["tool_calls"] = res["raw_tool_calls"]
        return msg

    def tool_message(self, call: dict | None, content: str) -> dict:
        if call and call.get("id"):
            return {"role": "tool", "tool_call_id": call["id"], "content": content}
        return {"role": "user", "content": content}  # id 가 없는 형식 오류는 사용자 말로 알려 준다
