"""투자 라우터들이 같이 쓰는 FastAPI 의존성 — 주문 PIN 헤더."""

from typing import Annotated

from fastapi import Header, HTTPException

from app.invest import config


def require_pin(pin: str | None, *, required: bool = True) -> None:
    """X-Trading-Pin 헤더 확인 (인증 없는 앱 — 같은 네트워크의 다른 기기가 실계좌 주문을 못 내게).
    required=False 는 설정 변경용: .env 에 PIN 이 있을 때만 확인한다."""
    try:
        config.check_pin(pin, required=required)
    except config.PinError as e:
        raise HTTPException(e.status, {"message": str(e), "code": e.code}) from None


Pin = Annotated[str | None, Header(alias="X-Trading-Pin")]
