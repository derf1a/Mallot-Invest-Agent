"""LLM 라우팅 — 호출 하나를 어느 공급자 · 모델로 보낼지 정한다.

경로(route) 글자: `claude` · `claude:<모델>` · `claude-<…>`(Claude 모델 이름 그대로) · `mlx`(=`local`) · `vllm` · `vllm:<모델>`.
MLX 는 작업 프로세스 하나가 모델 하나만 올리므로 모델을 고르지 않는다 (설정 > LLM 에서 고른 로컬 모델).

정하는 순서 (위가 이긴다):
1. 이미지가 든 프롬프트 → Claude (로컬 · vLLM 모델은 이미지를 읽지 못한다고 본다)
2. 에이전트 경로 — 지금 말하는 에이전트(as_agent 로 표시)에 정해 둔 경로
3. 부르는 쪽이 넘긴 model — 예: 토론 설정의 모델
4. 기능 경로 — llm_routes.features[<기능 키>]
5. 기본 경로 — llm_routes.default, 비어 있으면 설정 > LLM 의 기본 LLM (Claude / 로컬)
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass

from app.core.db import connect

S_ROUTES = "llm_routes"
PROVIDERS = {"claude": "Claude (Agent SDK)", "mlx": "로컬 MLX", "vllm": "vLLM (OpenAI 호환)"}

# 지금 LLM 을 부르는 에이전트의 키 (토론 · 비서 · 데스크가 as_agent 로 감싼다)
AGENT: ContextVar[str | None] = ContextVar("llm_agent", default=None)


@contextmanager
def as_agent(key: str | None) -> Iterator[None]:
    token = AGENT.set(key)
    try:
        yield
    finally:
        AGENT.reset(token)


@dataclass(frozen=True)
class Route:
    provider: str          # claude · mlx · vllm
    model: str = ""        # Claude 모델 id · vLLM 모델 이름 ('' = 그 공급자의 기본)
    source: str = "default"  # image · agent · call · feature · default (어디서 정해졌는지 — 화면 · 트레이스용)

    @property
    def text(self) -> str:
        return f"{self.provider}:{self.model}" if self.model else self.provider

    def as_dict(self) -> dict:
        return {**asdict(self), "text": self.text, "label": label(self)}


def parse(text: str | None, source: str = "call") -> Route | None:
    """경로 글자 → Route. 빈 값은 None, 모르는 값은 ValueError."""
    t = (text or "").strip()
    if not t:
        return None
    if t in ("local", "mlx"):
        return Route("mlx", "", source)
    if t.startswith("claude-"):
        return Route("claude", t, source)
    provider, _, model = t.partition(":")
    if provider not in PROVIDERS:
        raise ValueError(f"모르는 LLM 경로예요: {t} (claude · claude:<모델> · mlx · vllm · vllm:<모델>)")
    if provider == "mlx" and model:
        raise ValueError("MLX 는 모델을 경로에 쓰지 않아요 — 설정 > LLM 에서 로컬 모델을 고르세요")
    if provider == "claude" and model and not model.startswith("claude-"):
        raise ValueError(f"Claude 모델 이름이 아니에요: {model}")
    if len(model) > 200:
        raise ValueError("모델 이름이 너무 길어요")
    return Route(provider, model, source)


def label(route: Route) -> str:
    if route.provider == "claude":
        from app.llm.client import default_model

        return f"Claude · {route.model or default_model()}"
    if route.provider == "mlx":
        from app.llm import local

        return f"로컬 MLX · {local.model_title()}"
    from app.llm import vllm

    return f"vLLM · {route.model or vllm.default_model() or '서버 기본'}"


# ---------- 설정 ----------

def _load() -> dict:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (S_ROUTES,)).fetchone()
    finally:
        conn.close()
    try:
        value = json.loads(row["value"]) if row else {}
    except (TypeError, ValueError):
        value = {}
    return {"default": str(value.get("default") or ""), "features": dict(value.get("features") or {}),
            "agents": dict(value.get("agents") or {})}


def routes() -> dict:
    """저장된 경로 (빈 값 = 정하지 않음)."""
    return _load()


def set_routes(*, default: str | None = None, features: dict[str, str] | None = None,
               agents: dict[str, str] | None = None) -> dict:
    """넘긴 칸만 바꾼다. 빈 글자는 그 경로를 지운다. 하나라도 틀리면 아무것도 바꾸지 않는다 (ValueError)."""
    cur = _load()
    if default is not None:
        parse(default)
        cur["default"] = default.strip()
    for key, table in (("features", features), ("agents", agents)):
        if table is None:
            continue
        for name, text in table.items():
            if not name or len(name) > 80:
                raise ValueError(f"이름이 잘못됐어요: {name!r}")
            parse(text)
            if (text or "").strip():
                cur[key][name] = text.strip()
            else:
                cur[key].pop(name, None)
    conn = connect()
    try:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (S_ROUTES, json.dumps(cur, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()
    return cur


def _fallback() -> Route:
    from app.llm import local

    return Route("mlx" if local.provider() == "local" else "claude", "", "default")


def resolve(feature: str, model: str | None = None, *, has_image: bool = False, agent: str | None = None) -> Route:
    """이 호출을 보낼 경로. agent 를 비우면 지금 문맥(as_agent)의 에이전트."""
    if has_image:
        return Route("claude", model if model and model.startswith("claude-") else "", "image")
    table = _load()
    agent = agent if agent is not None else AGENT.get()
    if agent and (r := parse(table["agents"].get(agent), "agent")):
        return r
    if r := parse(model, "call"):
        return r
    if r := parse(table["features"].get(feature), "feature"):
        return r
    return parse(table["default"], "default") or _fallback()
