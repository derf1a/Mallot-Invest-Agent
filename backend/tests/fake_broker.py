"""가짜 토스증권 계좌 — 계좌·보유·매수 가능 금액·판매 가능 수량·주문(생성·조회·취소)을 상태와 함께 흉내 낸다.

toss.HTTP 자리에 끼워 쓴다. 시세는 샘플 시세(SampleMarket)를 그대로 써서, 시장가는 현재가로 바로 체결하고 지정가는 현재가가
닿을 때만 체결한다(아니면 PENDING). X-Tossinvest-Account 가 계좌 순번(1)이 아니면 실제 API처럼 404 account-not-found.
"""

import json
import urllib.parse

from app.invest.broker import toss
from app.invest.market import SampleMarket

ACCOUNT_SEQ = "1"
ACCOUNT_NO = "12345678901"


class FakeBroker:
    def __init__(self, cash: float = 10_000_000):
        self.cash = cash
        self.usd_cash = 0.0
        self.positions: dict[str, dict] = {}   # symbol → {quantity, cost}
        self.orders: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict, bytes | None]] = []
        self.fail_next_order: Exception | tuple[int, dict] | None = None
        self.market = SampleMarket()

    # ---- 도우미 ----
    def price(self, symbol: str) -> float:
        return self.market.quotes([symbol])[symbol]["price"]

    def posts(self) -> list[dict]:
        return [json.loads(c[3]) for c in self.calls if c[0] == "POST" and c[1] == "/api/v1/orders"]

    def _reserved(self, side: str, symbol: str | None = None) -> float:
        total = 0.0
        for o in self.orders.values():
            if o["status"] == "PENDING" and o["side"] == side and (symbol is None or o["symbol"] == symbol):
                total += o["quantity"] * (o["price"] if side == "BUY" else 1)
        return total

    def _fill(self, o: dict, price: float) -> None:
        q = o["quantity"]
        pos = self.positions.setdefault(o["symbol"], {"quantity": 0.0, "cost": 0.0})
        if o["side"] == "BUY":
            self.cash -= q * price
            pos["quantity"] += q
            pos["cost"] += q * price
        else:
            self.cash += q * price
            avg = pos["cost"] / pos["quantity"]
            pos["quantity"] -= q
            pos["cost"] -= avg * q
        o.update(status="FILLED", filled=q, avg=price)

    def _try_fill(self, o: dict) -> None:
        price = self.price(o["symbol"])
        if o["type"] == "LIMIT" and ((o["side"] == "BUY" and price > o["price"]) or (o["side"] == "SELL" and price < o["price"])):
            return
        self._fill(o, price)

    # ---- HTTP ----
    def __call__(self, method, url, headers=None, data=None, timeout=15):
        headers = headers or {}
        parsed = urllib.parse.urlparse(url.replace(toss.BASE_URL, ""))
        path, query = parsed.path, dict(urllib.parse.parse_qsl(parsed.query))
        self.calls.append((method, path, headers, data))
        if path == "/oauth2/token":
            return self._ok({"access_token": "tok", "expires_in": 86400}, wrap=False)
        if path == "/api/v1/exchange-rate":
            return self._ok({"baseCurrency": "USD", "quoteCurrency": "KRW", "rate": "1380"})
        if path == "/api/v1/accounts":
            return self._ok([{"accountSeq": int(ACCOUNT_SEQ), "accountNo": ACCOUNT_NO, "accountType": "BROKERAGE"}])
        if headers.get("X-Tossinvest-Account") != ACCOUNT_SEQ:
            return 404, json.dumps({"error": {"code": "account-not-found", "message": "계좌를 찾을 수 없습니다."}}).encode(), {}
        if path == "/api/v1/buying-power":
            if query.get("currency") == "USD":
                return self._ok({"currency": "USD", "cashBuyingPower": str(self.usd_cash)})
            return self._ok({"currency": "KRW", "cashBuyingPower": str(self.cash - self._reserved("BUY"))})
        if path == "/api/v1/sellable-quantity":
            held = self.positions.get(query["symbol"], {}).get("quantity", 0.0)
            return self._ok({"sellableQuantity": str(held - self._reserved("SELL", query["symbol"]))})
        if path == "/api/v1/holdings":
            items = [{"symbol": s, "name": self.market.info(s)["name"], "quantity": str(p["quantity"]),
                      "averagePurchasePrice": str(p["cost"] / p["quantity"]), "lastPrice": str(self.price(s)),
                      "currency": "KRW"} for s, p in self.positions.items() if p["quantity"] > 0]
            return self._ok({"items": items})
        if path == "/api/v1/orders" and method == "POST":
            return self._create(json.loads(data))
        if path == "/api/v1/orders":
            return self._ok({"orders": [self._view(o) for o in self.orders.values() if o["status"] == "PENDING"]})
        if path.startswith("/api/v1/orders/"):
            oid = path.split("/")[4]
            o = self.orders.get(oid)
            if o is None:
                return 404, json.dumps({"error": {"code": "order-not-found"}}).encode(), {}
            if path.endswith("/cancel"):
                if o["status"] == "PENDING":
                    o["status"] = "CANCELED"
                return self._ok({"orderId": oid})
            return self._ok(self._view(o))
        return 404, json.dumps({"error": {"code": "not-found", "message": path}}).encode(), {}

    def _create(self, body: dict):
        if self.fail_next_order is not None:
            fail, self.fail_next_order = self.fail_next_order, None
            if isinstance(fail, Exception):
                raise fail
            return fail[0], json.dumps(fail[1]).encode(), {}
        oid = f"ord-{len(self.orders) + 1}"
        o = {"id": oid, "symbol": body["symbol"], "side": body["side"], "type": body["orderType"],
             "quantity": float(body["quantity"]), "price": float(body["price"]) if "price" in body else None,
             "status": "PENDING", "filled": 0.0, "avg": None, "client_order_id": body["clientOrderId"]}
        self.orders[oid] = o
        self._try_fill(o)
        return self._ok({"orderId": oid, "clientOrderId": body["clientOrderId"]})

    @staticmethod
    def _view(o: dict) -> dict:
        return {"orderId": o["id"], "symbol": o["symbol"], "status": o["status"],
                "execution": {"filledQuantity": str(o["filled"]), "averageFilledPrice": str(o["avg"]) if o["avg"] else None,
                              "filledAt": "2026-10-13T11:30:05+09:00" if o["status"] == "FILLED" else None}}

    @staticmethod
    def _ok(result, wrap: bool = True):
        return 200, json.dumps({"result": result} if wrap else result).encode(), {}
