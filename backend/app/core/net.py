"""외부 HTTPS 호출 공통 설정.

python.org 설치판 macOS Python 은 시스템 인증서를 쓰지 않아 기본 설정으로는 모든 HTTPS 요청이
CERTIFICATE_VERIFY_FAILED 로 실패한다 ("Install Certificates.command" 를 따로 실행해야 함).
설치 방식에 상관없이 동작하도록 certifi 의 CA 묶음으로 검증한다.
"""

import http.client
import ipaddress
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from functools import lru_cache

import certifi


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def is_public_url(url: str) -> bool:
    """http(s) 이고 호스트가 공인 IP 로만 해석되는지. 외부 글의 링크를 열 때 내부망(127.0.0.1, 192.168.x.x 등)으로
    요청이 가지 않게 막는다 (SSRF)."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except (socket.gaierror, UnicodeError, ValueError):
        return False
    return bool(infos) and all(ipaddress.ip_address(info[4][0].split("%")[0]).is_global for info in infos)


class PublicOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """리다이렉트 대상도 공인 주소일 때만 따라간다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_public_url(newurl):
            raise urllib.error.URLError(f"내부 주소로의 리다이렉트를 막았습니다: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def public_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context()), PublicOnlyRedirectHandler())


USER_AGENT = "MallotInvestAgent/0.1 (personal use; stdlib urllib)"
MAX_BODY_BYTES = 5_000_000


def http_request(method: str, url: str, headers: dict | None = None, data: bytes | None = None,
                 timeout: float = 15) -> tuple[int, bytes]:
    """HTTP 요청 → (상태 코드, 본문). HTTP 오류는 그 상태 코드로, 연결 실패·시간 초과는 0으로 돌려준다.
    공인 주소만, 리다이렉트도 공인 주소로만 따라간다 (뉴스 링크 등 외부 글의 주소로 내부망을 찌르지 않게)."""
    if not is_public_url(url):
        return 0, b"blocked: non-public address"
    req = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with public_opener().open(req, timeout=timeout) as resp:
            return resp.status, resp.read(MAX_BODY_BYTES)
    except urllib.error.HTTPError as e:
        try:
            body = e.read(MAX_BODY_BYTES)
        except Exception:  # noqa: BLE001 — 오류 본문은 못 읽어도 상태 코드만 있으면 된다
            body = b""
        return e.code, body
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
        return 0, str(e).encode()


# 테스트에서 바꿔 끼운다
HTTP_REQUEST: Callable[..., tuple[int, bytes]] = http_request
