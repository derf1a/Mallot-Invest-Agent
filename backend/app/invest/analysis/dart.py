"""OpenDART 클라이언트 (https://opendart.fss.or.kr, 금융감독원 전자공시 — 무료, 인증키 필요).

토스 Open API(스펙 v1.2.28 확인)에는 재무 자료가 없어(시가총액·발행주식수까지) 가치평가의 재무는 여기서 받는다.

확인한 사항 (개발가이드, 2026-11):
- 고유번호 /corpCode.xml: 정상이면 ZIP(안에 XML 하나 — list: corp_code·corp_name·stock_code·modify_date), 오류면 XML. 상장사만 stock_code.
- 다중회사 주요계정 /fnlttMultiAcnt.json: corp_code 콤마로 최대 100개, bsns_year(2015년부터), reprt_code(1분기 11013·반기 11012·
  3분기 11014·사업보고서 11011). 주요계정은 재무상태표(BS)·손익계산서(IS)이고 연결(CFS)·별도(OFS)가 같이 온다.
- 오류도 HTTP 200 + status: 000 정상, 013 조회된 데이터 없음, 020 요청 제한 초과(일반적으로 2만 건), 021 회사 수 초과, 010·011·012·901 키·IP
  문제, 800 점검.

인증키는 주소에 들어가므로 오류 메시지·로그에 주소를 넣지 않는다.
"""

import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable

from app.core.net import is_public_url, public_opener

BASE_URL = "https://opendart.fss.or.kr/api"
KEY_ENV = "DART_API_KEY"
NOT_CONFIGURED = "OpenDART 인증키가 없습니다 (설정 > 투자에서 입력하거나 .env 의 DART_API_KEY)"
USER_AGENT = "Mallot/0.4 (personal investment research)"
MAX_BODY_BYTES = 60_000_000
MAX_CORPS = 100                 # 한 번에 묻는 회사 수 (API 제한)
REPORTS = {"11013": "1분기", "11012": "반기", "11014": "3분기", "11011": "사업보고서"}
FATAL = {"010", "011", "012", "020", "901"}  # 다시 물어도 소용없는 오류 — 동기화를 멈춘다


class DartError(Exception):
    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code

    @property
    def fatal(self) -> bool:
        return self.code in FATAL


def api_key() -> str | None:
    return os.environ.get(KEY_ENV, "").strip() or None


def configured() -> bool:
    return api_key() is not None


def http_get(url: str, timeout: float = 30) -> tuple[int, bytes]:
    """GET → (상태 코드, 본문). 연결 실패·시간 초과는 0."""
    if not is_public_url(url):
        return 0, b""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with public_opener().open(req, timeout=timeout) as resp:
            return resp.status, resp.read(MAX_BODY_BYTES)
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (urllib.error.URLError, OSError, ValueError):
        return 0, b""


HTTP: Callable[..., tuple[int, bytes]] = http_get  # 테스트에서 바꿔 끼운다


def _get(path: str, params: dict, timeout: float = 30) -> bytes:
    key = api_key()
    if not key:
        raise DartError(NOT_CONFIGURED, "no_key")
    status, body = HTTP(f"{BASE_URL}/{path}?{urllib.parse.urlencode({'crtfc_key': key, **params})}", timeout)
    if status != 200:
        raise DartError(f"OpenDART 요청 실패 (HTTP {status or '연결 실패'})", "http")
    return body


def _status(code: str, message: str) -> None:
    if code not in ("000", "013"):
        raise DartError(f"OpenDART 오류 {code}: {message}", code)


def corp_codes() -> list[dict]:
    """상장사 고유번호 목록 → [{symbol, corp_code, corp_name, modify_date}]."""
    body = _get("corpCode.xml", {}, timeout=90)
    if body[:2] != b"PK":
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            raise DartError(f"OpenDART 고유번호 응답을 해석할 수 없습니다: {e}") from None
        _status(root.findtext("status") or "", root.findtext("message") or "")
        return []
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            root = ET.fromstring(z.read(z.namelist()[0]))
    except (zipfile.BadZipFile, IndexError, ET.ParseError) as e:
        raise DartError(f"OpenDART 고유번호 파일을 해석할 수 없습니다: {e}") from None
    out = []
    for n in root.iter("list"):
        symbol = (n.findtext("stock_code") or "").strip()
        code = (n.findtext("corp_code") or "").strip()
        if len(symbol) == 6 and code:
            out.append({"symbol": symbol, "corp_code": code, "corp_name": (n.findtext("corp_name") or "").strip(),
                        "modify_date": (n.findtext("modify_date") or "").strip()})
    return out


def multi_accounts(corps: list[str], year: int, reprt_code: str) -> list[dict]:
    """다중회사 주요계정 → 응답 list 그대로. 공시 전이면(013) 빈 목록."""
    body = _get("fnlttMultiAcnt.json", {"corp_code": ",".join(corps[:MAX_CORPS]), "bsns_year": year, "reprt_code": reprt_code})
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise DartError(f"OpenDART 응답을 해석할 수 없습니다: {e}") from None
    if not isinstance(data, dict):
        raise DartError("OpenDART 응답 형식이 예상과 다릅니다")
    code = str(data.get("status") or "")
    _status(code, str(data.get("message") or ""))
    rows = data.get("list") if code == "000" else []
    return [r for r in rows or [] if isinstance(r, dict)]


def _json(body: bytes) -> dict:
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise DartError(f"OpenDART 응답을 해석할 수 없습니다: {e}") from None
    if not isinstance(data, dict):
        raise DartError("OpenDART 응답 형식이 예상과 다릅니다")
    _status(str(data.get("status") or ""), str(data.get("message") or ""))
    return data


def disclosure_list(corp_code: str, bgn_de: str, end_de: str, max_pages: int = 3) -> list[dict]:
    """공시 검색 /list.json — 한 회사의 기간(YYYYMMDD) 공시 → [{rcept_no, report_nm, rcept_dt, flr_nm, rm, corp_name, stock_code …}].
    최근 것부터, 100건씩 max_pages 쪽까지."""
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        data = _json(_get("list.json", {"corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de, "page_no": page, "page_count": 100}))
        rows = [r for r in data.get("list") or [] if isinstance(r, dict)]
        out += rows
        if page >= int(data.get("total_page") or 1) or not rows:
            break
    return out


# 주요사항보고서 상세 — 희석 · 자사주 비율을 계산할 때 (필드 이름은 2026-11 실제 응답으로 확인, 신주인수권부사채는 같은 규칙으로 추정)
DETAIL = {"rights": "piicDecsn.json", "cb": "cvbdIsDecsn.json", "bw": "bdwtIsDecsn.json", "buyback": "tsstkAqDecsn.json"}


def decision_detail(kind: str, corp_code: str, bgn_de: str, end_de: str) -> list[dict]:
    """주요사항보고서 상세(DETAIL) → 응답 list 그대로. 없으면(013) 빈 목록."""
    data = _json(_get(DETAIL[kind], {"corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de}))
    return [r for r in data.get("list") or [] if isinstance(r, dict)]
