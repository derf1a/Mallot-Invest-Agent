"""설정 > LLM: 기본 LLM(Claude / 로컬) 고르기, 로컬 모델 내려받기·올리기·내리기·시험."""

import time
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.llm import local
from app.llm.client import LLMError

router = APIRouter(prefix="/api/llm", tags=["llm"])


class ProviderIn(BaseModel):
    provider: Literal["claude", "local"]


class LocalOptionsIn(BaseModel):
    model: str | None = None
    thinking: str | None = None


def _model(key: str) -> str:
    if key not in local.CATALOG:
        raise HTTPException(404, "목록에 없는 모델이에요")
    return key


@router.get("")
def status() -> dict:
    return local.status()


@router.put("/provider")
def set_provider(body: ProviderIn) -> dict:
    try:
        local.set_provider(body.provider)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    if body.provider == "local":  # 고르면 바로 올리기 시작한다 (첫 호출이 모델 올리기를 기다리지 않게)
        try:
            local.load()
        except ValueError:
            pass
    return local.status()


@router.put("/local")
def set_local_options(body: LocalOptionsIn) -> dict:
    try:
        local.set_options(model=body.model, thinking=body.thinking)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    return local.status()


@router.post("/local/models/{key}/download")
def download(key: str) -> dict:
    return local.start_download(_model(key))


@router.post("/local/models/{key}/cancel")
def cancel_download(key: str) -> dict:
    return local.cancel_download(_model(key))


@router.delete("/local/models/{key}")
def delete_model(key: str) -> dict:
    try:
        return local.delete_model(_model(key))
    except ValueError as e:
        raise HTTPException(409, str(e)) from None


@router.post("/local/load")
def load() -> dict:
    try:
        local.load()
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    return local.status()


@router.post("/local/unload")
def unload() -> dict:
    local.unload()
    return local.status()


@router.post("/local/test")
async def test() -> dict:
    """짧은 질문 하나로 응답·속도를 잰다 (사용량에는 남기지 않는다)."""
    started = time.perf_counter()
    try:
        res = await local.chat([{"role": "user", "content": "한국의 수도는 어디인가요? 한 문장으로 답하세요."}],
                               max_tokens=64, temperature=0.0)
    except LLMError as e:
        raise HTTPException(502, str(e)) from None
    return {"answer": res["content"], "reasoning": res.get("reasoning") or "", "usage": res["usage"],
            "timing": res["timing"], "total_ms": int((time.perf_counter() - started) * 1000)}
