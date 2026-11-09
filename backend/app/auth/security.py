"""접근 권한 — 관리자(나) 로그인과 손님(공유 링크) 입장.

- 켜는 조건: 관리자 비밀번호(MIA_ADMIN_PASSWORD_HASH 또는 MIA_ADMIN_PASSWORD)가 있거나 MIA_PUBLIC=1.
  꺼져 있으면 지금처럼 이 컴퓨터에서 열어 쓰는 앱이다. 다만 터널 · 프록시를 거친 요청(Cf-Connecting-IP · X-Forwarded-For)은
  막는다 — 비밀번호 없이 인터넷에 열리는 일을 막는 안전장치.
- MIA_PUBLIC=1 인데 비밀번호가 없으면 관리자 로그인이 안 된다 (닫힌 쪽으로 실패).
- 역할: admin(모든 API) · viewer(/api/share/* 읽기만). 쿠키는 HttpOnly · SameSite=Strict, https 면 Secure.
- 쿠키는 서명한 토큰(역할 · 만료). 관리자 토큰은 비밀번호에서, 손님 토큰은 공유 키에서 서명 키를 만든다
  → 비밀번호를 바꾸거나 공유 키를 새로 만들면 이전 쿠키가 모두 끊긴다.
- 로그인은 5번 틀리면 15분 잠근다 (프로세스 메모리, 서버 전체).
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from app.core.db import connect, data_dir

ADMIN_COOKIE = "mia_admin"
VIEWER_COOKIE = "mia_viewer"
ADMIN_TTL = 7 * 86_400
VIEWER_TTL = 30 * 86_400
MAX_FAILS = 5
LOCK_SEC = 15 * 60
MIN_PASSWORD = 10
S_SHARE = "share"
PROXY_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "x-real-ip", "forwarded")

_lock = threading.Lock()
_fails: dict = {"count": 0, "until": 0.0}


# ---------- 설정 ----------

def _password_hash() -> str:
    return os.environ.get("MIA_ADMIN_PASSWORD_HASH", "").strip()


def _password_plain() -> str:
    return os.environ.get("MIA_ADMIN_PASSWORD", "")


def has_password() -> bool:
    return bool(_password_hash() or _password_plain())


def public() -> bool:
    return os.environ.get("MIA_PUBLIC") == "1"


def enabled() -> bool:
    return has_password() or public()


def proxied(headers) -> bool:
    return any(headers.get(h) for h in PROXY_HEADERS)


def hash_password(password: str, salt: bytes | None = None) -> str:
    """scrypt 해시 글자 — .env 의 MIA_ADMIN_PASSWORD_HASH 에 넣는다 (python -m app.auth.passwd)."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def _check(password: str) -> bool:
    if h := _password_hash():
        try:
            kind, salt, want = h.split("$")
        except ValueError:
            return False
        return kind == "scrypt" and hmac.compare_digest(hash_password(password, bytes.fromhex(salt)).split("$")[2], want)
    plain = _password_plain()
    return bool(plain) and hmac.compare_digest(password.encode(), plain.encode())


def _secret() -> bytes:
    """서명 비밀 — MIA_SESSION_SECRET, 없으면 data/session_secret 을 만들어 쓴다 (권한 600, git 제외)."""
    if s := os.environ.get("MIA_SESSION_SECRET", "").strip():
        return s.encode()
    path: Path = data_dir() / "session_secret"
    with _lock:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(secrets.token_hex(32), encoding="utf-8")
            path.chmod(0o600)
        return path.read_text(encoding="utf-8").strip().encode()


# ---------- 공유 (손님) ----------

def _share_row(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (S_SHARE,)).fetchone()
    try:
        return json.loads(row["value"]) if row else {}
    except (TypeError, ValueError):
        return {}


def _share_put(conn: sqlite3.Connection, value: dict) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (S_SHARE, json.dumps(value)))
    conn.commit()


def share(conn: sqlite3.Connection) -> dict:
    """{enabled, key} — 키가 없으면 만든다. 처음에는 꺼져 있다."""
    cur = _share_row(conn)
    if not cur.get("key"):
        cur = {"enabled": False, "key": secrets.token_urlsafe(18), "rotated_at": int(time.time())}
        _share_put(conn, cur)
    return {"enabled": bool(cur.get("enabled")), "key": cur["key"], "rotated_at": cur.get("rotated_at")}


def set_share(conn: sqlite3.Connection, *, enabled: bool | None = None, rotate: bool = False) -> dict:
    cur = share(conn)
    if enabled is not None:
        cur["enabled"] = enabled
    if rotate:
        cur.update(key=secrets.token_urlsafe(18), rotated_at=int(time.time()))
    _share_put(conn, cur)
    return cur


# ---------- 토큰 ----------

def _key(role: str, material: str) -> bytes:
    return hmac.new(_secret(), f"{role}:{hashlib.sha256(material.encode()).hexdigest()}".encode(), hashlib.sha256).digest()


def _material(role: str, conn: sqlite3.Connection | None = None) -> str | None:
    if role == "admin":
        return (_password_hash() or _password_plain()) or None
    c = conn or connect()
    try:
        s = share(c)
    finally:
        if conn is None:
            c.close()
    return s["key"] if s["enabled"] else None


def issue(role: str, conn: sqlite3.Connection | None = None) -> str:
    material = _material(role, conn)
    if not material:
        raise PermissionError("이 역할은 지금 쓸 수 없어요")
    ttl = ADMIN_TTL if role == "admin" else VIEWER_TTL
    body = base64.urlsafe_b64encode(json.dumps({"r": role, "exp": int(time.time()) + ttl}).encode()).decode().rstrip("=")
    sig = hmac.new(_key(role, material), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify(token: str | None, role: str, conn: sqlite3.Connection | None = None) -> bool:
    if not token or "." not in token:
        return False
    body, sig = token.rsplit(".", 1)
    material = _material(role, conn)
    if not material:
        return False
    want = hmac.new(_key(role, material), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, want):
        return False
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, TypeError):
        return False
    return data.get("r") == role and int(data.get("exp") or 0) > time.time()


def role_of(cookies) -> str | None:
    """요청의 역할: admin · viewer · None."""
    if verify(cookies.get(ADMIN_COOKIE), "admin"):
        return "admin"
    if verify(cookies.get(VIEWER_COOKIE), "viewer"):
        return "viewer"
    return None


# ---------- 로그인 ----------

class Locked(Exception):
    def __init__(self, seconds: int):
        super().__init__(f"너무 많이 틀렸어요 — {seconds // 60 + 1}분 뒤에 다시 하세요")
        self.seconds = seconds


def login(password: str) -> bool:
    """맞으면 True. 잠겨 있으면 Locked. 비밀번호가 없으면(공개 모드) 언제나 False."""
    with _lock:
        left = _fails["until"] - time.time()
        if left > 0:
            raise Locked(int(left))
    ok = has_password() and _check(password)
    with _lock:
        if ok:
            _fails.update(count=0, until=0.0)
        else:
            _fails["count"] += 1
            if _fails["count"] >= MAX_FAILS:
                _fails.update(count=0, until=time.time() + LOCK_SEC)
    return ok


def reset_failures() -> None:
    with _lock:
        _fails.update(count=0, until=0.0)
