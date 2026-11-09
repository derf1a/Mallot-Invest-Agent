"""저장소 루트의 .env 파일 읽기·쓰기 (git 제외, 권한 600).

- 서버 시작 시 load() 가 .env 값을 os.environ 에 넣는다. 셸에서 이미 export 한 값이 있으면 그것이 우선한다.
- 설정 화면에서 API 키를 저장하면 update() 가 파일을 고치고 os.environ 도 바로 바꾼다 (재시작 불필요).
- 어떤 키를 화면에서 바꿀 수 있는지는 부르는 쪽이 허용 목록으로 정한다 (예: 실전 매매 스위치는 화면에서 못 바꾼다).
"""

import os
import re
from pathlib import Path

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def env_path() -> Path:
    custom = os.environ.get("MIA_ENV_FILE")
    return Path(custom) if custom else Path(__file__).resolve().parents[3] / ".env"


def _unquote(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    return raw.split(" #", 1)[0].rstrip()  # 따옴표 없는 값 뒤의 주석


def read() -> dict[str, str]:
    path = env_path()
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _LINE.match(line)
        if m:
            out[m.group(1)] = _unquote(m.group(2))
    return out


def load() -> list[str]:
    """.env 값을 os.environ 에 넣는다 (이미 있는 환경 변수는 건드리지 않음). 넣은 키 목록을 돌려준다."""
    loaded = []
    for key, value in read().items():
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def _quote(value: str) -> str:
    if value == "" or re.search(r"[\s#'\"]", value):
        return '"' + value.replace('"', "") + '"'
    return value


def update(values: dict[str, str | None]) -> None:
    """키를 쓰거나(값) 지운다(None·빈 문자열). 다른 줄·주석은 그대로 둔다."""
    for key, value in values.items():
        if not _KEY.match(key):
            raise ValueError(f"잘못된 환경 변수 이름: {key}")
        if value is not None and ("\n" in value or "\r" in value):
            raise ValueError("값에 줄바꿈을 넣을 수 없습니다")
    path = env_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = {k: (v.strip() if v else None) for k, v in values.items()}
    out: list[str] = []
    for line in lines:
        m = _LINE.match(line) if not line.lstrip().startswith("#") else None
        if m and m.group(1) in pending:
            value = pending.pop(m.group(1))
            if value:
                out.append(f"{m.group(1)}={_quote(value)}")
            continue
        out.append(line)
    out += [f"{k}={_quote(v)}" for k, v in pending.items() if v]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    path.chmod(0o600)
    for key, value in values.items():
        if value and value.strip():
            os.environ[key] = value.strip()
        else:
            os.environ.pop(key, None)


def mask(value: str | None, secret: bool) -> str | None:
    """화면 표시용. 시크릿은 끝 4자리만, 그 외는 앞 4자리·끝 2자리만."""
    if not value:
        return None
    if secret:
        return "••••" + value[-4:] if len(value) >= 12 else "••••"
    return value if len(value) <= 6 else f"{value[:4]}…{value[-2:]}"
