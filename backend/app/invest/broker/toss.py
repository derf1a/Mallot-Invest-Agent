"""토스증권 Open API 클라이언트 (https://openapi.tossinvest.com, 스펙 v1.2.24 기준).

확인한 사항 (openapi-docs/latest/openapi.json):
- 인증: POST /oauth2/token (client_credentials) → Bearer 토큰. 재발급하면 이전 토큰이 바로 무효 → 토큰은 프로세스 전체에서
  하나만 캐시한다.
- 응답 봉투: {"result": ...}, 오류 {"error": {"requestId", "code", "message"}}. 가격·수량은 문자열 decimal.
- 시세: /prices, /candles(1m·1d, 최신순, 최대 200), /orderbook, /price-limits. 종목: /stocks, /stocks/all, warnings,
  investor-trading. 시장: /market-indicators/prices(KOSPI·KOSDAQ·국채), /rankings, /market-calendar/KR, /exchange-rate.
- 섹터: /sectors(계층 목록), /sectors/{id}(요약·기간 등락률), /sectors/{id}/stocks(시가총액순), /sectors/{id}/etfs.
- 관심 그룹·공모펀드 조회 API 는 없다 (관심 그룹은 앱 안에서 관리, 펀드는 ETF 로 다룬다).
- 계좌·주문(X-Tossinvest-Account 헤더 = /accounts 응답의 accountSeq — 계좌번호가 아니다. 틀리면 404 account-not-found):
  /accounts, /holdings, /buying-power, /sellable-quantity,
  POST /orders(LIMIT·MARKET, clientOrderId 멱등 키), GET /orders?status=OPEN|CLOSED, POST /orders/{id}/cancel.
- 1억원 이상 주문은 confirmHighValueOrder 가 필요 — 이 앱은 그 플래그를 보내지 않는다 (큰 주문은 토스가 거절).
- 주문 생성이 500·연결 오류면 접수 여부가 불확실하다 → 자동 재시도하지 않고 호출한 쪽이 '확인 필요'로 남긴다.
  (401 토큰 무효·429 한도 초과는 처리 전 거절이라 같은 clientOrderId 로 한 번 다시 보낸다.)
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from app.core.net import ssl_context

BASE_URL = "https://openapi.tossinvest.com"
NOT_CONFIGURED = "토스증권 Open API 키가 없습니다 (설정 > 투자에서 입력하거나 .env 의 TOSS_CLIENT_ID·TOSS_CLIENT_SECRET)"
# 그룹별 초당 요청 수. 공식 표에서 확인한 값 + 나머지 그룹은 보수적으로 5
RATE_LIMITS = {"AUTH": 5, "ACCOUNT": 1, "ASSET": 5, "MARKET_DATA": 15, "ORDER": 10}
DEFAULT_RATE = 5
SYMBOL_RE = re.compile(r"^[A-Za-z0-9.\-]{1,20}$")


class TossError(Exception):
    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class TossUncertain(TossError):
    """요청이 접수됐는지 알 수 없음 (주문 생성 중 연결 오류·500)."""


def _urllib(method: str, url: str, headers: dict | None = None, data: bytes | None = None,
            timeout: float = 15) -> tuple[int, bytes, dict]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:  # 고정된 https 기본 URL만
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TossUncertain(f"토스증권 Open API에 연결하지 못했습니다: {getattr(e, 'reason', e)}") from None


# 테스트에서 바꿔 끼운다: (method, url, headers=None, data=None, timeout=15) -> (status, body, headers)
HTTP: Callable[..., tuple[int, bytes, dict]] = _urllib

# client_id → (토큰, 만료 시각[monotonic]).
TOKEN_CACHE: dict[str, tuple[str, float]] = {}
TOKEN_LOCK = threading.Lock()
# (client_id, TOSS_ACCOUNT_SEQ 설정값) → 확인한 accountSeq
ACCOUNT_CACHE: dict[tuple[str, str], str] = {}
_LAST_CALL: dict[str, float] = {}
_RATE_LOCK = threading.Lock()


def clear_token_cache() -> None:
    with TOKEN_LOCK:
        TOKEN_CACHE.clear()
        ACCOUNT_CACHE.clear()


def _digits(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def match_account(accounts: list[dict], configured: str | None) -> str:
    """설정값 → accountSeq. 비어 있으면 첫 계좌, 순번과 같으면 그 계좌, 계좌번호(하이픈 무시)와 같으면 그 계좌의 순번."""
    if not accounts:
        raise TossError("토스증권 계좌가 없습니다 (종합매매 계좌만 조회됩니다)")
    want = _digits(configured)
    if not want:
        return str(accounts[0].get("accountSeq"))
    for a in accounts:
        if _digits(a.get("accountSeq")) == want:
            return str(a.get("accountSeq"))
    for a in accounts:
        if _digits(a.get("accountNo")) == want:
            return str(a.get("accountSeq"))
    seqs = ", ".join(str(a.get("accountSeq")) for a in accounts)
    raise TossError(f"설정한 계좌(TOSS_ACCOUNT_SEQ)를 토스증권 계좌 목록에서 찾지 못했습니다 — 계좌 순번({seqs}) 중 하나를 "
                    "넣거나 비워 두세요 (설정 > 투자 > 연결 테스트에서 고를 수 있어요)", status=400, code="account-not-configured")


def num(value: object) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def symbol_ok(symbol: str) -> str:
    s = (symbol or "").strip()
    if not SYMBOL_RE.match(s):
        raise TossError("종목 코드는 영문·숫자·'.'·'-'만 쓸 수 있습니다 (예: 005930, AAPL)", status=400)
    return s


class TossClient:
    TOKEN_MARGIN_S = 300

    def __init__(self, client_id: str | None = None, client_secret: str | None = None,
                 account_seq: str | int | None = None, base_url: str = BASE_URL,
                 http: Callable[..., tuple[int, bytes, dict]] | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.client_id = client_id if client_id is not None else os.environ.get("TOSS_CLIENT_ID", "")
        self.client_secret = client_secret if client_secret is not None else os.environ.get("TOSS_CLIENT_SECRET", "")
        seq = account_seq if account_seq is not None else os.environ.get("TOSS_ACCOUNT_SEQ", "")
        self.account_seq = str(seq).strip() or None
        self.base_url = base_url.rstrip("/")
        self._http = http
        self._clock = clock
        self._sleep = sleep

    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    # ---- 저수준 ----
    def _throttle(self, group: str) -> None:
        limit = RATE_LIMITS.get(group, DEFAULT_RATE)
        key = f"{self.client_id}:{group}"
        with _RATE_LOCK:
            wait = _LAST_CALL.get(key, -1e9) + 1.0 / limit - self._clock()
            if wait > 0:
                self._sleep(wait)
            _LAST_CALL[key] = self._clock()

    def _send(self, method: str, path: str, *, headers: dict, data: bytes | None, group: str) -> tuple[int, bytes, dict]:
        self._throttle(group)
        http = self._http or HTTP
        return http(method, f"{self.base_url}{path}", headers=headers, data=data, timeout=15)

    @staticmethod
    def error(status: int, body: bytes) -> TossError:
        code, message = None, ""
        try:
            payload = json.loads(body or b"{}")
            err = payload.get("error")
            if isinstance(err, dict):
                code, message = err.get("code"), err.get("message") or ""
            elif isinstance(err, str):
                code, message = err, payload.get("error_description") or ""
        except (ValueError, AttributeError):
            pass
        hint = {
            400: "요청이 올바르지 않습니다",
            401: "인증에 실패했습니다 (클라이언트 ID/시크릿 확인)",
            403: "허용되지 않은 IP입니다 — 토스증권 WTS > Open API > 허용 IP 관리에서 이 Mac 의 공인 IP를 등록하세요",
            404: "대상을 찾을 수 없습니다",
            409: "중복되었거나 처리 중인 요청입니다",
            422: "주문 조건이 맞지 않습니다",
            429: "요청 한도를 넘었습니다. 잠시 후 다시 시도하세요",
        }.get(status, "요청이 실패했습니다")
        detail = message or code or ""
        return TossError(f"토스증권 {status}: {hint}" + (f" — {detail}" if detail else ""), status=status, code=code)

    def token(self, force: bool = False) -> str:
        if not self.is_configured():
            raise TossError(NOT_CONFIGURED)
        with TOKEN_LOCK:
            cached = TOKEN_CACHE.get(self.client_id)
            if cached and not force and cached[1] > self._clock():
                return cached[0]
            body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": self.client_id,
                                           "client_secret": self.client_secret}).encode()
            status, raw, _ = self._send("POST", "/oauth2/token", data=body, group="AUTH",
                                        headers={"Content-Type": "application/x-www-form-urlencoded",
                                                 "Accept": "application/json"})
            if status != 200:
                raise self.error(status, raw)
            try:
                payload = json.loads(raw)
                access = payload["access_token"]
                expires = float(payload.get("expires_in") or 86400)
            except (ValueError, KeyError, TypeError):
                raise TossError("토스증권 토큰 응답을 해석하지 못했습니다") from None
            TOKEN_CACHE[self.client_id] = (access, self._clock() + max(expires - self.TOKEN_MARGIN_S, 1))
            return access

    def call(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None,
             group: str, account: bool = False, uncertain_on_failure: bool = False) -> object:
        """API 호출 → result. 401 은 토큰을 새로 받아 한 번, 429 는 Retry-After 만큼 쉬고 한 번 더 보낸다."""
        query = f"?{urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})}" if params else ""
        data = json.dumps(body).encode() if body is not None else None
        force = False
        for attempt in range(3):
            headers = {"Authorization": f"Bearer {self.token(force=force)}", "Accept": "application/json"}
            force = False  # 토큰 재발급은 401 직후 한 번만 (재발급하면 이전 토큰이 무효)
            if data is not None:
                headers["Content-Type"] = "application/json"
            if account:
                headers["X-Tossinvest-Account"] = self.resolve_account()
            try:
                status, raw, resp_headers = self._send(method, f"{path}{query}", headers=headers, data=data, group=group)
            except TossUncertain:
                if uncertain_on_failure:
                    raise
                raise TossError("토스증권 Open API에 연결하지 못했습니다") from None
            if status == 401 and attempt == 0:
                force = True
                continue
            if status == 429 and attempt < 2:
                try:
                    wait = float(resp_headers.get("Retry-After") or 1)
                except (TypeError, ValueError):
                    wait = 1.0
                self._sleep(min(max(wait, 0.2), 3.0))
                continue
            if status >= 500 and uncertain_on_failure:
                raise TossUncertain(str(self.error(status, raw)), status=status)
            if status != 200:
                raise self.error(status, raw)
            try:
                return json.loads(raw).get("result")
            except (ValueError, AttributeError):
                raise TossError("토스증권 응답을 해석하지 못했습니다") from None
        raise TossError("토스증권 요청이 반복해서 거절되었습니다")

    # ---- 시세·종목 ----
    def prices(self, symbols: list[str]) -> list[dict]:
        syms = ",".join(symbol_ok(s) for s in symbols[:200])
        result = self.call("GET", "/api/v1/prices", params={"symbols": syms}, group="MARKET_DATA")
        return [r for r in result or [] if isinstance(r, dict)]

    def candles(self, symbol: str, interval: str = "1d", count: int = 200, before: str | None = None) -> dict:
        result = self.call("GET", "/api/v1/candles", group="MARKET_DATA_CHART",
                           params={"symbol": symbol_ok(symbol), "interval": interval, "count": min(max(count, 1), 200),
                                   "before": before})
        return result if isinstance(result, dict) else {"candles": []}

    def orderbook(self, symbol: str) -> dict:
        result = self.call("GET", "/api/v1/orderbook", params={"symbol": symbol_ok(symbol)}, group="MARKET_DATA")
        return result if isinstance(result, dict) else {}

    def price_limits(self, symbol: str) -> dict:
        result = self.call("GET", "/api/v1/price-limits", params={"symbol": symbol_ok(symbol)}, group="MARKET_DATA")
        return result if isinstance(result, dict) else {}

    def stocks(self, symbols: list[str]) -> list[dict]:
        syms = ",".join(symbol_ok(s) for s in symbols[:200])
        result = self.call("GET", "/api/v1/stocks", params={"symbols": syms}, group="STOCK")
        return [r for r in result or [] if isinstance(r, dict)]

    def stocks_all(self, market: str) -> list[dict]:
        result = self.call("GET", "/api/v1/stocks/all", params={"market": market}, group="STOCK")
        return [r for r in result or [] if isinstance(r, dict)]

    def warnings(self, symbol: str) -> object:
        return self.call("GET", f"/api/v1/stocks/{symbol_ok(symbol)}/warnings", group="STOCK")

    def investor_trading(self, symbol: str, count: int = 10) -> dict:
        result = self.call("GET", f"/api/v1/stocks/{symbol_ok(symbol)}/investor-trading",
                           params={"count": min(max(count, 1), 100)}, group="STOCK")
        return result if isinstance(result, dict) else {}

    def indicator_prices(self, symbols: list[str]) -> list[dict]:
        result = self.call("GET", "/api/v1/market-indicators/prices", params={"symbols": ",".join(symbols)},
                           group="MARKET_INDICATOR")
        return [r for r in result or [] if isinstance(r, dict)]

    def rankings(self, kind: str, market_country: str = "KR", duration: str = "1d", count: int = 20) -> dict:
        result = self.call("GET", "/api/v1/rankings", group="RANKING",
                           params={"type": kind, "marketCountry": market_country, "duration": duration,
                                   "count": min(max(count, 1), 100)})
        return result if isinstance(result, dict) else {}

    def exchange_rate(self, base: str = "USD", quote: str = "KRW") -> float | None:
        result = self.call("GET", "/api/v1/exchange-rate", params={"baseCurrency": base, "quoteCurrency": quote},
                           group="MARKET_INFO")
        return num(result.get("rate")) if isinstance(result, dict) else None

    # ---- 섹터 (토스증권 자체 산업 분류, 한 종목이 여러 섹터에 속할 수 있음) ----
    def sectors(self) -> list[dict]:
        result = self.call("GET", "/api/v1/sectors", group="SECTOR")
        return [x for x in (result or {}).get("sectors") or [] if isinstance(x, dict)] if isinstance(result, dict) else []

    def sector(self, sector_id: str, market_country: str = "KR") -> dict:
        result = self.call("GET", f"/api/v1/sectors/{urllib.parse.quote(sector_id, safe='')}",
                           params={"marketCountry": market_country}, group="SECTOR")
        return result if isinstance(result, dict) else {}

    def sector_stocks(self, sector_id: str, market_country: str = "KR") -> list[dict]:
        result = self.call("GET", f"/api/v1/sectors/{urllib.parse.quote(sector_id, safe='')}/stocks",
                           params={"marketCountry": market_country}, group="SECTOR")
        return [x for x in (result or {}).get("stocks") or [] if isinstance(x, dict)] if isinstance(result, dict) else []

    def sector_etfs(self, sector_id: str, market_country: str = "KR") -> list[dict]:
        result = self.call("GET", f"/api/v1/sectors/{urllib.parse.quote(sector_id, safe='')}/etfs",
                           params={"marketCountry": market_country}, group="SECTOR")
        return [x for x in (result or {}).get("etfs") or [] if isinstance(x, dict)] if isinstance(result, dict) else []

    def market_calendar_kr(self) -> dict:
        result = self.call("GET", "/api/v1/market-calendar/KR", group="MARKET_INFO")
        return result if isinstance(result, dict) else {}

    # ---- 계좌 ----
    def accounts(self) -> list[dict]:
        result = self.call("GET", "/api/v1/accounts", group="ACCOUNT")
        return [a for a in result or [] if isinstance(a, dict)]

    def resolve_account(self) -> str:
        """X-Tossinvest-Account 에 넣을 accountSeq. 계좌 목록과 한 번 맞춰 본 뒤 프로세스에 캐시한다."""
        key = (self.client_id, self.account_seq or "")
        cached = ACCOUNT_CACHE.get(key)
        if cached is None:
            cached = ACCOUNT_CACHE[key] = match_account(self.accounts(), self.account_seq)
        return cached

    def holdings(self) -> dict:
        result = self.call("GET", "/api/v1/holdings", group="ASSET", account=True)
        return result if isinstance(result, dict) else {}

    def buying_power(self, currency: str = "KRW") -> float | None:
        result = self.call("GET", "/api/v1/buying-power", params={"currency": currency}, group="ORDER_INFO", account=True)
        return num(result.get("cashBuyingPower")) if isinstance(result, dict) else None

    def sellable_quantity(self, symbol: str) -> float | None:
        result = self.call("GET", "/api/v1/sellable-quantity", params={"symbol": symbol_ok(symbol)},
                           group="ORDER_INFO", account=True)
        return num(result.get("sellableQuantity")) if isinstance(result, dict) else None

    # ---- 주문 ----
    def orders(self, status: str = "OPEN", symbol: str | None = None, limit: int = 50) -> list[dict]:
        result = self.call("GET", "/api/v1/orders", group="ORDER_HISTORY", account=True,
                           params={"status": status, "symbol": symbol_ok(symbol) if symbol else None,
                                   "limit": min(limit, 100) if status == "CLOSED" else None})
        return [o for o in (result or {}).get("orders") or [] if isinstance(o, dict)] if isinstance(result, dict) else []

    def order(self, order_id: str) -> dict:
        result = self.call("GET", f"/api/v1/orders/{urllib.parse.quote(order_id, safe='')}", group="ORDER_HISTORY",
                           account=True)
        return result if isinstance(result, dict) else {}

    def create_order(self, *, symbol: str, side: str, order_type: str, quantity: float, price: float | None,
                     client_order_id: str) -> dict:
        """실제 주문. side=BUY|SELL, order_type=LIMIT|MARKET. 접수 여부가 불확실하면 TossUncertain."""
        body = {"clientOrderId": client_order_id, "symbol": symbol_ok(symbol), "side": side, "orderType": order_type,
                "quantity": _decimal(quantity)}
        if order_type == "LIMIT":
            body["price"] = _decimal(price)
        result = self.call("POST", "/api/v1/orders", body=body, group="ORDER", account=True, uncertain_on_failure=True)
        if not isinstance(result, dict) or not result.get("orderId"):
            raise TossUncertain("토스증권 주문 응답에 주문 번호가 없습니다")
        return result

    def cancel_order(self, order_id: str) -> dict:
        result = self.call("POST", f"/api/v1/orders/{urllib.parse.quote(order_id, safe='')}/cancel", body={},
                           group="ORDER", account=True)
        return result if isinstance(result, dict) else {}


def _decimal(value: float | None) -> str:
    if value is None:
        raise TossError("가격이 필요합니다")
    return f"{value:.0f}" if float(value).is_integer() else f"{value:f}".rstrip("0").rstrip(".")
