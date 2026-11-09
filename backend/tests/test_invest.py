import json
import os
from datetime import datetime

import pytest

from app.core import envfile
from app.core.db import connect
from app.invest.marketdata import market
from app.invest.broker import config, toss, trading
from app.invest.marketdata.market import KST, SampleMarket, tick_size
from app.invest.broker.toss import TossClient, TossError, TossUncertain, match_account
from tests.conftest import PIN as PIN_VALUE
from tests.fake_broker import ACCOUNT_NO

PIN = {"X-Trading-Pin": PIN_VALUE}

OPEN_TIME = datetime(2026, 10, 13, 11, 30, tzinfo=KST)  # 화요일 장중


@pytest.fixture
def at_open(monkeypatch):
    monkeypatch.setattr(market, "now_kst", lambda: OPEN_TIME)


@pytest.fixture
def conn(client):
    c = connect()
    yield c
    c.close()


# ---- .env ----
def test_envfile_update_keeps_other_lines_and_masks(tmp_path):
    path = envfile.env_path()
    path.write_text("# 주석\nOTHER=1\nTOSS_CLIENT_ID=old\n")
    envfile.update({"TOSS_CLIENT_ID": "abcd1234efgh", "TOSS_CLIENT_SECRET": "s3cr3t-value-xyz9"})
    text = path.read_text()
    assert "# 주석" in text and "OTHER=1" in text and "TOSS_CLIENT_ID=abcd1234efgh" in text
    assert oct(path.stat().st_mode)[-3:] == "600"
    assert os.environ["TOSS_CLIENT_SECRET"] == "s3cr3t-value-xyz9"
    assert envfile.mask("s3cr3t-value-xyz9", True) == "••••xyz9"
    assert envfile.mask("abcd1234efgh", False) == "abcd…gh"
    envfile.update({"TOSS_CLIENT_ID": None})
    assert "TOSS_CLIENT_ID" not in envfile.read() and "TOSS_CLIENT_ID" not in os.environ
    with pytest.raises(ValueError):
        envfile.update({"TOSS_CLIENT_ID": "a\nWT_TRADING_PIN=1"})


def test_keys_api_masks_and_refuses_pin(client):
    res = client.put("/api/invest/config/keys", json={"TOSS_CLIENT_ID": "client-id-123", "TOSS_CLIENT_SECRET": "very-secret-9876"})
    keys = {k["key"]: k for k in res.json()["keys"]}
    assert keys["TOSS_CLIENT_SECRET"]["masked"] == "••••9876" and keys["TOSS_CLIENT_SECRET"]["set"]
    assert "very-secret" not in res.text
    assert res.json()["toss_configured"] is True
    # 주문 PIN 은 화면(API)에서 바꿀 수 없다
    with pytest.raises(config.ConfigError):
        config.save_keys({"MIA_TRADING_PIN": "1"})
    assert client.put("/api/invest/config/keys", json={"TOSS_ACCOUNT_SEQ": "abc"}).status_code == 400
    # 계좌번호를 하이픈과 함께 넣어도 숫자만 저장 (주문 때 계좌 목록과 맞춰 순번으로 바꾼다)
    client.put("/api/invest/config/keys", json={"TOSS_ACCOUNT_SEQ": "123-45-678901"})
    assert os.environ["TOSS_ACCOUNT_SEQ"] == "12345678901"


def test_trading_needs_keys_and_pin(client, monkeypatch, at_open):
    assert len(config.trading_blockers()) == 2
    body = {"symbol": "005930", "side": "buy", "order_type": "market", "quantity": 1, "confirm_live": True}
    res = client.post("/api/invest/orders", json=body, headers=PIN)
    assert res.status_code == 403 and res.json()["detail"]["code"] == "pin_not_configured"
    monkeypatch.setenv("MIA_TRADING_PIN", PIN_VALUE)
    res = client.post("/api/invest/orders", json=body, headers=PIN)
    assert res.status_code == 409 and "API 키" in res.json()["detail"]
    assert client.get("/api/invest/account").status_code == 409
    assert client.post("/api/invest/agent-sessions", json={"symbol": "005930"}, headers=PIN).status_code == 409
    # 키 입력 같은 설정 변경은 PIN 이 정해져 있으면 PIN 필요
    assert client.put("/api/invest/config/keys", json={"TOSS_CLIENT_ID": "x"}).status_code == 403
    assert client.put("/api/invest/config/keys", json={"TOSS_CLIENT_ID": "x"}, headers=PIN).status_code == 200


# ---- 관심 그룹 ----
def test_watch_groups_and_search(client, at_open):
    hits = client.get("/api/invest/search", params={"q": "KODEX"}).json()
    assert hits and all(h["security_type"] == "ETF" for h in hits)  # ETF(상장지수펀드)도 검색된다
    groups = client.get("/api/invest/watch-groups").json()
    assert [g["name"] for g in groups] == ["관심"] and groups[0]["items"] == []
    groups = client.post("/api/invest/watch-groups", json={"name": "반도체"}).json()
    semi = groups[1]["id"]
    client.post("/api/invest/watchlist", json={"symbol": "000660"})  # 기본 그룹
    client.post("/api/invest/watchlist", json={"symbol": "000660", "group_id": semi})  # 여러 그룹에 같이
    client.post("/api/invest/watchlist", json={"symbol": "091160", "group_id": semi})
    groups = client.get("/api/invest/watch-groups").json()
    assert [i["symbol"] for i in groups[1]["items"]] == ["000660", "091160"]
    assert groups[1]["items"][1]["security_type"] == "ETF" and groups[1]["items"][1]["price"]
    flat = client.get("/api/invest/watchlist").json()
    assert [(w["symbol"], w["group_ids"]) for w in flat] == [("000660", [1, semi]), ("091160", [semi])]
    assert client.patch(f"/api/invest/watch-groups/{semi}", json={"name": "칩"}).json()[1]["name"] == "칩"
    client.delete("/api/invest/watchlist/000660", params={"group_id": 1})
    assert [w["symbol"] for w in client.get("/api/invest/watchlist").json()] == ["000660", "091160"]
    assert client.post("/api/invest/watchlist", json={"symbol": "005930", "group_id": 999}).status_code == 404
    client.delete(f"/api/invest/watch-groups/{semi}")
    assert client.get("/api/invest/watchlist").json() == []
    assert client.delete("/api/invest/watch-groups/1").status_code == 409  # 마지막 그룹은 남긴다


# ---- 토스 클라이언트 ----
class FakeToss:
    """토스 Open API 흉내: 경로별 응답을 정해 두고 호출을 기록한다."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict, bytes | None]] = []
        self.routes: dict[tuple[str, str], list] = {}
        self.tokens = 0

    def on(self, method: str, path: str, *responses):
        self.routes[(method, path)] = list(responses)

    def __call__(self, method, url, headers=None, data=None, timeout=15):
        path = url.replace(toss.BASE_URL, "")
        self.calls.append((method, path, headers or {}, data))
        if path == "/oauth2/token":
            self.tokens += 1
            return 200, json.dumps({"access_token": f"tok{self.tokens}", "expires_in": 86400}).encode(), {}
        key = (method, path.split("?")[0])
        queue = self.routes.get(key)
        if not queue:
            return 404, json.dumps({"error": {"code": "not-found", "message": key[1]}}).encode(), {}
        resp = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(resp, Exception):
            raise resp
        status, body = resp[0], resp[1]
        return status, json.dumps(body).encode(), (resp[2] if len(resp) > 2 else {})


def make_client(fake: FakeToss, **kw) -> TossClient:
    return TossClient("cid", "secret", http=fake, sleep=lambda s: None, **kw)


def test_toss_client_retries_401_and_429_and_parses_candles():
    fake = FakeToss()
    fake.on("GET", "/api/v1/candles",
            (401, {"error": {"code": "expiredToken"}}),
            (429, {"error": {"code": "rate-limit-exceeded"}}, {"Retry-After": "1"}),
            (200, {"result": {"candles": [
                {"timestamp": "2026-10-13T00:00:00+09:00", "openPrice": "100", "highPrice": "110", "lowPrice": "95",
                 "closePrice": "105", "volume": "1000", "currency": "KRW"},
                {"timestamp": "2026-10-12T00:00:00+09:00", "openPrice": "90", "highPrice": "101", "lowPrice": "89",
                 "closePrice": "100", "volume": "900", "currency": "KRW"}], "nextBefore": None}}))
    m = market.TossMarket(make_client(fake))
    cs = m.candles("005930", "1d", 2)
    assert [c["t"] for c in cs] == ["2026-10-12", "2026-10-13"] and cs[-1]["c"] == 105
    assert fake.tokens == 2  # 401 → 토큰 새로 받음
    assert sum(1 for c in fake.calls if c[1].startswith("/api/v1/candles")) == 3


def test_toss_create_order_is_uncertain_on_500_and_sends_limit_body():
    fake = FakeToss()
    fake.on("GET", "/api/v1/accounts", (200, {"result": [{"accountSeq": 7, "accountNo": "12345678", "accountType": "BROKERAGE"}]}))
    fake.on("POST", "/api/v1/orders", (500, {"error": {"code": "internalError"}}))
    client = make_client(fake)
    with pytest.raises(TossUncertain):
        client.create_order(symbol="005930", side="BUY", order_type="LIMIT", quantity=3, price=70000,
                            client_order_id="mallot-1-x")
    posts = [c for c in fake.calls if c[0] == "POST" and c[1] == "/api/v1/orders"]
    assert len(posts) == 1  # 500 은 다시 보내지 않는다 (중복 주문 위험)
    body = json.loads(posts[0][3])
    assert body == {"clientOrderId": "mallot-1-x", "symbol": "005930", "side": "BUY", "orderType": "LIMIT",
                    "quantity": "3", "price": "70000"}
    assert posts[0][2]["X-Tossinvest-Account"] == "7"  # 순번이 하나뿐이라 그 계좌
    fake.on("POST", "/api/v1/orders", (422, {"error": {"code": "insufficientBuyingPower", "message": "매수 가능 금액 부족"}}))
    with pytest.raises(TossError) as e:
        client.create_order(symbol="005930", side="BUY", order_type="MARKET", quantity=1, price=None,
                            client_order_id="mallot-2-x")
    assert not isinstance(e.value, TossUncertain) and "매수 가능 금액 부족" in str(e.value)


# ---- 샘플 시세 ----
def test_sample_market_is_deterministic_and_consistent(at_open):
    m = SampleMarket()
    a, b = m.candles("005930", "1d", 50), SampleMarket().candles("005930", "1d", 50)
    assert a == b and len(a) == 50 and a[-1]["t"] == "2026-10-13"
    mins = m.candles("005930", "1m", 500)
    assert mins[-1]["c"] == a[-1]["c"] and max(x["h"] for x in mins) == a[-1]["h"]
    book = m.orderbook("005930")
    assert book["asks"][0]["price"] > book["bids"][0]["price"]
    assert tick_size(70_000) == 100 and tick_size(4_000) == 5 and tick_size(600_000) == 1000
    assert all(c["c"] % tick_size(c["c"]) == 0 for c in a)  # 원화 가격은 호가 단위 격자 위
    bond = next(i for i in m.indicators() if i["symbol"] == "KR_BOND_3Y")
    assert 0 < bond["price"] < 20 and bond["price"] != int(bond["price"])  # 금리는 소수 (정수로 뭉개지지 않음)
    with pytest.raises(market.MarketError):
        m.info("999999")


def test_market_api_endpoints(client, at_open):
    assert client.get("/api/invest/status").json()["provider"] == "sample"
    hits = client.get("/api/invest/search", params={"q": "삼성"}).json()
    assert hits[0]["symbol"] == "005930"
    snap = client.get("/api/invest/stocks/005930").json()
    assert snap["name"] == "삼성전자" and snap["price"] and snap["prev_close"] and snap["technicals"]["rsi14"] is not None
    assert len(client.get("/api/invest/stocks/005930/candles", params={"count": 120}).json()["candles"]) == 120
    assert client.get("/api/invest/stocks/005930/orderbook").json()["bids"]
    assert client.get("/api/invest/stocks/999999").status_code == 404
    overview = client.get("/api/invest/market").json()
    assert [i["symbol"] for i in overview["indicators"]][:2] == ["KOSPI", "KOSDAQ"] and overview["session"]["is_open"]
    assert client.get("/api/invest/rankings", params={"kind": "gainers"}).json()[0]["rank"] == 1
    wl = client.post("/api/invest/watchlist", json={"symbol": "000660"}).json()
    assert wl[0]["name"] == "SK하이닉스" and wl[0]["price"]
    assert client.delete("/api/invest/watchlist/000660").json() == []


def test_match_account_accepts_seq_or_account_number():
    accounts = [{"accountSeq": 1, "accountNo": "12345678901"}, {"accountSeq": 2, "accountNo": "98765432100"}]
    assert match_account(accounts, None) == "1" and match_account(accounts, "2") == "2"
    assert match_account(accounts, "987-65-432100") == "2"  # 계좌번호를 넣어도 순번으로
    with pytest.raises(TossError) as e:
        match_account(accounts, "55555")
    assert "계좌 순번(1, 2)" in str(e.value)
    with pytest.raises(TossError):
        match_account([], None)


def test_resolve_account_maps_account_number_once():
    fake = FakeToss()
    fake.on("GET", "/api/v1/accounts", (200, {"result": [{"accountSeq": 3, "accountNo": "11122233344"}]}))
    fake.on("GET", "/api/v1/holdings", (200, {"result": {"items": []}}))
    client = make_client(fake, account_seq="11122233344")
    client.holdings()
    client.holdings()
    assert [c[2].get("X-Tossinvest-Account") for c in fake.calls if c[1] == "/api/v1/holdings"] == ["3", "3"]
    assert sum(1 for c in fake.calls if c[1] == "/api/v1/accounts") == 1  # 계좌 목록은 한 번만


# ---- 실계좌 매매 (가짜 토스 계좌) ----
def _order(client, **kw):
    body = {"symbol": "005930", "side": "buy", "order_type": "market", "quantity": 1, "confirm_live": True, **kw}
    return client.post("/api/invest/orders", json=body, headers=PIN)


def test_market_buy_fills_and_limit_order_waits(client, broker, at_open):
    snap = client.get("/api/invest/stocks/005930").json()
    price = snap["price"]
    res = client.post("/api/invest/orders", json={"symbol": "005930", "side": "buy", "order_type": "market", "quantity": 3}, headers=PIN)
    assert res.status_code == 428  # 실계좌 주문은 확인(confirm_live) 필요
    res = _order(client, quantity=3)
    assert res.status_code == 201, res.text
    order = res.json()
    assert order["status"] == "filled" and order["avg_fill_price"] == price and order["broker_order_id"] == "ord-1"
    sent = broker.posts()[0]
    assert sent["clientOrderId"].startswith(f"mallot-{order['id']}-") and sent["orderType"] == "MARKET"
    acct = client.get("/api/invest/account").json()
    assert acct["cash"] == pytest.approx(10_000_000 - 3 * price)
    assert acct["holdings"][0]["quantity"] == 3 and acct["holdings"][0]["name"] == "삼성전자"
    # 현재가보다 낮은 지정가 매수 → 미체결로 남고 매수 가능 금액을 잡아 둔다
    low = price - snap["tick"] * 20
    pending = _order(client, order_type="limit", quantity=2, price=low).json()
    assert pending["status"] == "open"
    info = client.get("/api/invest/order-info/005930").json()
    assert info["buying_power"] == pytest.approx(10_000_000 - 3 * price - 2 * low) and info["sellable"] == 3
    assert client.post(f"/api/invest/orders/{pending['id']}/cancel").json()["status"] == "cancelled"
    # 호가 단위가 안 맞는 가격 / 보유보다 많은 매도
    bad = _order(client, order_type="limit", price=low + 1)
    assert bad.status_code == 400 and "호가 단위" in bad.json()["detail"]
    over = _order(client, side="sell", quantity=4)
    assert over.status_code == 422 and "판매 가능 수량" in over.json()["detail"]["message"]
    assert _order(client, side="sell", quantity=3).json()["status"] == "filled"
    assert client.get("/api/invest/account").json()["holdings"] == []


def test_account_number_in_env_is_mapped_to_seq(client, broker, monkeypatch, at_open):
    monkeypatch.setenv("TOSS_ACCOUNT_SEQ", ACCOUNT_NO)  # 순번 자리에 계좌번호를 넣은 경우
    assert _order(client).status_code == 201
    assert next(c[2] for c in broker.calls if c[1] == "/api/v1/orders")["X-Tossinvest-Account"] == "1"
    test = client.post("/api/invest/config/test").json()
    assert test["selected_seq"] == "1" and test["account_error"] is None and test["accounts"][0]["account_no"] == "••••8901"
    monkeypatch.setenv("TOSS_ACCOUNT_SEQ", "999")
    toss.clear_token_cache()
    res = client.get("/api/invest/account")
    assert res.status_code == 502 and "계좌 순번(1)" in res.json()["detail"]
    assert client.post("/api/invest/config/test").json()["account_error"]


def test_limits_and_halt_block_orders(client, conn, broker, at_open):
    config.save_trading(conn, limits_={"max_order_krw": 100_000})
    res = _order(client, quantity=5)
    assert res.status_code == 422 and "건당 한도" in res.json()["detail"]["message"]
    kakao = client.get("/api/invest/stocks/035720").json()["price"]
    config.save_trading(conn, limits_={"max_order_krw": 5_000_000, "daily_max_krw": kakao * 6})
    assert _order(client, symbol="035720", quantity=5).status_code == 201
    res = _order(client, symbol="035720", quantity=3)
    assert res.status_code == 422 and "하루 한도" in res.json()["detail"]["message"]
    config.save_trading(conn, limits_={"daily_max_krw": 50_000_000, "max_position_pct": 5})
    res = _order(client, quantity=10)
    assert res.status_code == 422 and "종목 비중" in res.json()["detail"]["message"]
    config.save_trading(conn, halt=True)
    assert _order(client, symbol="035720", side="sell").status_code == 409
    # 해외 주식은 아직 주문 불가
    config.save_trading(conn, halt=False)
    assert _order(client, symbol="AAPL").status_code == 400


def test_agent_order_rejected_by_guard_is_recorded(conn, broker, at_open):
    config.save_trading(conn, limits_={"max_order_krw": 10_000})
    with pytest.raises(trading.TradeError):
        trading.place_order(conn, symbol="005930", side="buy", order_type="market", quantity=1, source="agent",
                            reason="테스트")
    row = conn.execute("SELECT status, source, error FROM invest_orders").fetchone()
    assert row["status"] == "rejected" and row["source"] == "agent" and "건당 한도" in row["error"]
    assert broker.posts() == []


def test_uncertain_order_is_marked_failed_and_not_retried(client, broker, at_open):
    broker.fail_next_order = TossUncertain("timeout")
    order = _order(client).json()
    assert order["status"] == "failed" and "토스증권 앱에서" in order["error"]
    broker.fail_next_order = (500, {"error": {"code": "internalError"}})
    assert _order(client).json()["status"] == "failed"
    assert broker.orders == {}  # 두 번 모두 다시 보내지 않았다
    broker.fail_next_order = (422, {"error": {"code": "insufficient", "message": "주문 조건 불일치"}})
    rejected = _order(client).json()
    assert rejected["status"] == "rejected" and "주문 조건 불일치" in rejected["error"]


def test_agent_order_waits_for_approval_unless_enabled(client, conn, broker, at_open):
    order = trading.place_order(conn, symbol="005930", side="buy", order_type="market", quantity=1, source="agent")
    assert order["status"] == "pending_approval" and broker.posts() == []
    assert client.post(f"/api/invest/orders/{order['id']}/approve", json={}, headers=PIN).status_code == 428
    approved = client.post(f"/api/invest/orders/{order['id']}/approve", json={"confirm_live": True}, headers=PIN).json()
    assert approved["broker_order_id"] == "ord-1" and approved["status"] == "filled"
    rejected = trading.place_order(conn, symbol="005930", side="buy", order_type="market", quantity=1, source="agent")
    assert client.post(f"/api/invest/orders/{rejected['id']}/reject").json()["status"] == "rejected"
    config.save_trading(conn, agent_auto=True)
    direct = trading.place_order(conn, symbol="005930", side="buy", order_type="market", quantity=1, source="agent")
    assert direct["status"] == "filled" and len(broker.posts()) == 2


def test_actions_need_pin_with_lockout(client, broker, at_open):
    body = {"symbol": "005930", "side": "buy", "order_type": "market", "quantity": 1, "confirm_live": True}
    res = client.post("/api/invest/orders", json=body)
    assert res.status_code == 403 and res.json()["detail"]["code"] == "pin_required"
    assert client.post("/api/invest/agent-sessions", json={"symbol": "005930"}).status_code == 403
    assert client.put("/api/invest/config/trading", json={"limits": {"max_order_krw": 9e9}}).status_code == 403
    assert client.put("/api/invest/config/keys", json={"TOSS_ACCOUNT_SEQ": "8"}).status_code == 403
    assert client.post("/api/invest/agents/reset").status_code == 403
    # 전체 중지 켜기는 PIN 없이도
    assert client.put("/api/invest/config/trading", json={"halted": True}).json()["halted"] is True
    assert client.put("/api/invest/config/trading", json={"halted": False}).status_code == 403
    assert client.put("/api/invest/config/trading", json={"halted": False}, headers=PIN).json()["halted"] is False
    for _ in range(4):
        assert client.post("/api/invest/orders", json=body, headers={"X-Trading-Pin": "0000"}).json()["detail"]["code"] == "pin_invalid"
    res = client.post("/api/invest/orders", json=body, headers={"X-Trading-Pin": "0000"})
    assert res.status_code == 403
    locked = client.post("/api/invest/orders", json=body, headers=PIN)  # 5번 틀린 뒤에는 맞아도 잠김
    assert locked.status_code == 429 and locked.json()["detail"]["code"] == "pin_locked"


def test_etf_tick_differs_from_stocks(client, broker, at_open):
    """ETF·ETN 호가는 2,000원 미만 1원·이상 5원 — 주식 호가표로 재면 맞는 지정가를 앱이 거절한다."""
    assert tick_size(31_915) == 50 and tick_size(31_915, security_type="ETF") == 5 and tick_size(1_500, "KRW", "ETN") == 1
    assert tick_size(31_915, "USD", "ETF") == 0.01 and tick_size(31_915, security_type="STOCK") == 50
    snap = client.get("/api/invest/stocks/069500").json()
    assert snap["security_type"] == "ETF" and snap["tick"] == 5
    good = (snap["price"] // 5 - 1) * 5
    assert good % tick_size(good) != 0                                                 # 주식 호가표로는 틀린 가격
    order = _order(client, symbol="069500", order_type="limit", quantity=1, price=good)
    assert order.status_code == 201 and order.json()["price"] == good
    bad = _order(client, symbol="069500", order_type="limit", quantity=1, price=good + 3)
    assert bad.status_code == 400 and "호가 단위(5원)" in bad.json()["detail"]


def test_session_bars_keep_only_the_latest_regular_session():
    bar = lambda t: {"t": t, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}  # noqa: E731
    day = "2026-11-02T"
    bars = [bar(t) for t in ("2026-10-30T15:29:00+09:00", "2026-10-30T19:25:00+09:00", f"{day}08:30:00+09:00", f"{day}09:00:00+09:00",
                             f"{day}09:01:00+09:00", f"{day}15:30:00+09:00", f"{day}16:05:00+09:00")]
    assert [b["t"][11:16] for b in market.session_bars(bars)] == ["09:00", "09:01", "15:30"]   # 전날·시간외 뺌
    assert [b["t"][:16] for b in market.session_bars(bars[:3])] == ["2026-10-30T15:29"]        # 개장 전이면 직전 거래일
    assert market.session_bars(bars[1:3]) == [] and market.session_bars([]) == []


def test_quote_and_technicals_use_the_same_range_and_volume_basis(client, broker, at_open, conn):
    """같은 말을 하는 숫자는 도구끼리 같다 — 기간 고저(일봉 고가·저가)와 20일 평균 거래량(오늘 제외)."""
    import asyncio
    from app.invest import agents

    p = SampleMarket()
    s = agents.Session(conn=conn, scope={"scope": "stream", "label": "x", "candidates": [{"symbol": "005930", "name": "삼성전자"}]},
                       run_id=None, provider=p)
    tools = agents._handlers(s)
    quote = asyncio.run(tools["get_quote"]({}))
    tech = asyncio.run(tools["get_technicals"]({}))
    assert tech["high_in_range"] == quote["range_high"] and tech["low_in_range"] == quote["range_low"]
    assert round(quote["volume"] / quote["avg_volume_20"], 2) == tech["volume_vs_20d_avg"]


def test_position_limit_counts_leverage_exposure(client, broker, at_open, monkeypatch):
    """2배 ETF 는 평가액 × 2 로 비중 한도를 잰다 — 40% 한도면 평가액 20% 까지."""
    real = SampleMarket.info
    monkeypatch.setattr(SampleMarket, "info", lambda self, sym: {**real(self, sym), "leverage": 2.0 if sym == "069500" else None})
    price = client.get("/api/invest/stocks/069500").json()["price"]
    limits = {"max_order_krw": 100_000_000, "daily_max_krw": 100_000_000, "max_position_pct": 40}
    conn = connect()
    config.save_trading(conn, limits_=limits)
    big = int(10_000_000 * 0.25 // price)
    refused = _order(client, symbol="069500", quantity=big)
    detail = refused.json()["detail"]
    assert refused.status_code == 422 and "노출" in detail["message"] and "× 2배" in detail["message"]
    small = int(10_000_000 * 0.15 // price)
    assert _order(client, symbol="069500", quantity=small).status_code == 201           # 평가액 15% = 노출 30%
    assert _order(client, symbol="005930", quantity=1).status_code == 201               # 일반 종목은 그대로
