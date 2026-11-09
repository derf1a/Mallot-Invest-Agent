"""요청한 사람의 역할 — app/auth/guard 가 request.state.role 에 넣는다 (로그인이 꺼져 있으면 언제나 admin).

라우터가 손님(읽기 전용)에게 보낼 값을 줄일 때 쓴다 — 예: 설정 화면의 키(가린 값도) · .env 위치는 빼고 보낸다.
"""

from fastapi import Request


def is_viewer(request: Request) -> bool:
    return getattr(request.state, "role", "admin") == "viewer"
