"""관리자 비밀번호 정하기: `cd backend && uv run python -m app.auth.passwd` → .env 에 MIA_ADMIN_PASSWORD_HASH (평문은 남기지 않는다)."""

import getpass
import sys

from app.auth.security import MIN_PASSWORD, hash_password
from app.core import envfile


def main() -> int:
    pw = getpass.getpass("관리자 비밀번호: ")
    if len(pw) < MIN_PASSWORD:
        print(f"{MIN_PASSWORD}자 이상으로 정하세요", file=sys.stderr)
        return 1
    if getpass.getpass("한 번 더: ") != pw:
        print("두 번 입력한 값이 달라요", file=sys.stderr)
        return 1
    envfile.update({"MIA_ADMIN_PASSWORD_HASH": hash_password(pw), "MIA_ADMIN_PASSWORD": None})
    print(f"{envfile.env_path()} 에 저장했어요. 서버를 다시 시작하면 적용돼요 (이전 로그인은 모두 끊겨요).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
