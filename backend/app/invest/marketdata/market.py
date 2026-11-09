"""시세·종목 정보 제공자 — 토스증권 Open API(키가 있으면) 또는 샘플 데이터(키가 없을 때 화면 개발·시연용).

샘플 데이터는 종목 코드로 시드를 고정한 가상 시계열이다. 실제 시세가 아니며, 화면에 항상 '샘플 데이터'로 표시한다.
모든 제공자는 같은 정규화 형식을 돌려준다:
- 캔들: {"t": "YYYY-MM-DD"(일봉) 또는 ISO 시각(분봉), "o", "h", "l", "c", "v"} — 오래된 것부터
- 호가: {"asks": [{"price", "volume"}] 낮은 가격부터, "bids": [...] 높은 가격부터, "timestamp"}
"""

import hashlib
import math
import random
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.invest.marketdata import indicators
from app.invest.broker import config
from app.invest.broker.toss import TossClient, TossError, num, symbol_ok

KST = ZoneInfo("Asia/Seoul")
INDICATORS = [("KOSPI", "코스피", "pt"), ("KOSDAQ", "코스닥", "pt"), ("KR_BOND_3Y", "국고채 3년", "%"),
              ("KR_BOND_10Y", "국고채 10년", "%")]
RANKING_KINDS = {"amount": "MARKET_TRADING_AMOUNT", "volume": "MARKET_TRADING_VOLUME", "gainers": "TOP_GAINERS",
                 "losers": "TOP_LOSERS"}
UNIVERSE_MARKETS = ("KOSPI", "KOSDAQ", "KR_ETC", "NASDAQ", "NYSE", "AMEX")
UNIVERSE_TTL = timedelta(days=1)


ETP_TYPES = {"ETF", "ETN", "FOREIGN_ETF"}
REGULAR_OPEN, REGULAR_CLOSE = "09:00", "15:30"


class MarketError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def tick_size(price: float, currency: str = "KRW", security_type: str | None = None) -> float:
    """호가 단위 (KRX 2023년 통합 기준). ETF·ETN 은 주식과 달리 2,000원 미만 1원·이상 5원. 미국 주식은 0.01."""
    if currency != "KRW":
        return 0.01
    if security_type in ETP_TYPES:
        return 1 if price < 2_000 else 5
    for limit, tick in ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500)):
        if price < limit:
            return tick
    return 1_000


def now_kst() -> datetime:
    return datetime.now(KST)


def session_bars(bars: list[dict]) -> list[dict]:
    """분봉에서 가장 최근 정규장만 — 토스 분봉에는 전날과 시간외(NXT)가 섞여 있어 그대로 쓰면 시가·VWAP 이 오늘 값이 아니다."""
    regular = [b for b in bars if REGULAR_OPEN <= b["t"][11:16] <= REGULAR_CLOSE]
    day = regular[-1]["t"][:10] if regular else None
    return [b for b in regular if b["t"][:10] == day]


# ---- 짧은 캐시 (토스 호출 한도 보호) ----
_CACHE: dict[tuple, tuple[float, object]] = {}
_CACHE_LOCK = threading.Lock()


def cached(key: tuple, ttl: float, fn: Callable[[], object]) -> object:
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = fn()
    with _CACHE_LOCK:
        _CACHE[key] = (now + ttl, value)
    return value


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


# ---- 샘플 데이터 ----
SAMPLE_STOCKS: list[tuple[str, str, str, str, float]] = [
    ("005930", "삼성전자", "KOSPI", "KRW", 70_000), ("000660", "SK하이닉스", "KOSPI", "KRW", 180_000),
    ("373220", "LG에너지솔루션", "KOSPI", "KRW", 380_000), ("005380", "현대차", "KOSPI", "KRW", 230_000),
    ("035420", "NAVER", "KOSPI", "KRW", 190_000), ("035720", "카카오", "KOSPI", "KRW", 45_000),
    ("051910", "LG화학", "KOSPI", "KRW", 300_000), ("068270", "셀트리온", "KOSPI", "KRW", 180_000),
    ("105560", "KB금융", "KOSPI", "KRW", 80_000), ("247540", "에코프로비엠", "KOSDAQ", "KRW", 150_000),
    ("086520", "에코프로", "KOSDAQ", "KRW", 90_000), ("AAPL", "애플", "NASDAQ", "USD", 220.0),
    ("MSFT", "마이크로소프트", "NASDAQ", "USD", 420.0), ("NVDA", "엔비디아", "NASDAQ", "USD", 130.0),
    ("TSLA", "테슬라", "NASDAQ", "USD", 250.0),
    # ETF (상장지수펀드)
    ("069500", "KODEX 200", "KOSPI", "KRW", 35_000), ("091160", "KODEX 반도체", "KOSPI", "KRW", 40_000),
    ("305720", "KODEX 2차전지산업", "KOSPI", "KRW", 15_000), ("091170", "KODEX 은행", "KOSPI", "KRW", 9_000),
    ("244580", "KODEX 바이오", "KOSPI", "KRW", 8_000),
]
SAMPLE_ETFS = {"069500", "091160", "305720", "091170", "244580"}
# 샘플 섹터 (토스 섹터 API 흉내): id, 이름, 상위, 구성 주식(시가총액순), 관련 ETF
SAMPLE_SECTORS: list[dict] = [
    {"id": "S10", "name": "IT", "parent": None, "stocks": ["005930", "000660", "035420", "035720"], "etfs": []},
    {"id": "S11", "name": "반도체", "parent": "S10", "stocks": ["005930", "000660"], "etfs": ["091160"]},
    {"id": "S12", "name": "인터넷", "parent": "S10", "stocks": ["035420", "035720"], "etfs": []},
    {"id": "S20", "name": "2차전지", "parent": None, "stocks": ["373220", "051910", "247540", "086520"], "etfs": ["305720"]},
    {"id": "S30", "name": "자동차", "parent": None, "stocks": ["005380"], "etfs": []},
    {"id": "S40", "name": "바이오", "parent": None, "stocks": ["068270"], "etfs": ["244580"]},
    {"id": "S50", "name": "금융", "parent": None, "stocks": ["105560"], "etfs": ["091170"]},
]
_SAMPLE_SECTORS = {x["id"]: x for x in SAMPLE_SECTORS}
SAMPLE_USD_KRW = 1380.0
_SAMPLE = {s[0]: s for s in SAMPLE_STOCKS}
_SAMPLE_INDICATOR_BASE = {"KOSPI": 2_600.0, "KOSDAQ": 780.0, "KR_BOND_3Y": 3.1, "KR_BOND_10Y": 3.3}
_EPOCH = date(2025, 1, 1)
SESSION_MIN = 390  # 09:00 ~ 15:30


def _seed(*parts: object) -> int:
    return int(hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def _bdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


class SampleMarket:
    key = "sample"
    title = "샘플 데이터"

    def __init__(self, clock: Callable[[], datetime] | None = None):
        self.clock = clock or (lambda: now_kst())  # 호출 시점에 모듈 함수를 읽는다 (테스트에서 시각 고정)

    def _meta(self, symbol: str) -> tuple[str, str, str, str, float]:
        if symbol in _SAMPLE:
            return _SAMPLE[symbol]
        base = _SAMPLE_INDICATOR_BASE.get(symbol)
        if base:
            return (symbol, symbol, "INDEX", "IDX", base)  # 지수·국채 금리: 호가 단위 없이 소수 셋째 자리까지
        raise MarketError(f"샘플 데이터에 없는 종목입니다: {symbol} (토스증권 API를 연결하면 모든 종목을 볼 수 있어요)", 404)

    def _session_minute(self) -> tuple[date, int | None]:
        """(오늘 날짜, 장 시작 후 지난 분 — 장 전이면 None, 장 마감 후면 SESSION_MIN)."""
        now = self.clock()
        if now.weekday() >= 5:
            return now.date(), None
        minutes = (now.hour - 9) * 60 + now.minute
        if minutes < 0:
            return now.date(), None
        return now.date(), min(minutes, SESSION_MIN)

    def _raw_daily(self, symbol: str) -> list[dict]:
        """장 마감 기준 일봉 (오늘 봉은 하루 전체). 분봉 경로의 시작·끝점으로도 쓴다."""
        _, _, _, currency, base = self._meta(symbol)
        today, minute = self._session_minute()
        days = _bdays(_EPOCH, today)
        if minute is None and days and days[-1] == today:
            days = days[:-1]  # 오늘 장이 아직 안 열렸다
        rng = random.Random(_seed("daily", symbol))
        vol = rng.uniform(0.012, 0.028) if base > 100 else rng.uniform(0.004, 0.01)
        base_volume = rng.uniform(2e5, 3e6) if currency == "KRW" else rng.uniform(1e7, 6e7)
        price = base
        out = []
        for d in days:
            o = price * (1 + rng.gauss(0, vol / 4))
            c = o * math.exp(rng.gauss(0.0002, vol))
            h = max(o, c) * (1 + abs(rng.gauss(0, vol / 3)))
            low = min(o, c) * (1 - abs(rng.gauss(0, vol / 3)))
            out.append({"t": d.isoformat(), "o": o, "h": h, "l": low, "c": c,
                        "v": round(base_volume * math.exp(rng.gauss(0, 0.4)))})
            price = c
        return out

    def _daily(self, symbol: str) -> list[dict]:
        currency = self._meta(symbol)[3]
        out = self._raw_daily(symbol)
        today, minute = self._session_minute()
        if out:
            # 마지막 봉은 분봉 경로로 다시 만든다 — 분봉 차트와 일봉의 시고저종이 일치하도록 (장중이면 지금까지만)
            full = out[-1]
            live = full["t"] == today.isoformat() and minute is not None and minute < SESSION_MIN
            mins = self._minutes(symbol, full, minute if live else SESSION_MIN)
            out[-1] = {**full, "h": max(m["h"] for m in mins), "l": min(m["l"] for m in mins), "c": mins[-1]["c"],
                       "v": round(full["v"] * (minute + 1) / SESSION_MIN) if live else full["v"]}
        return [self._round(c, currency) for c in out]

    def _minutes(self, symbol: str, day: dict, upto: int) -> list[dict]:
        """그날 시가→종가를 잇는 브라운 브리지 분봉 (upto 분까지)."""
        rng = random.Random(_seed("minute", symbol, day["t"]))
        steps = [rng.gauss(0, 1) for _ in range(SESSION_MIN)]
        walk, acc = [], 0.0
        for s in steps:
            acc += s
            walk.append(acc)
        scale = abs(day["c"] - day["o"]) / 6 + day["o"] * 0.0008
        start = datetime.fromisoformat(day["t"]).replace(hour=9, tzinfo=KST)
        out, prev = [], day["o"]
        for i in range(upto + 1 if upto < SESSION_MIN else SESSION_MIN):
            frac = (i + 1) / SESSION_MIN
            bridge = walk[i] - frac * walk[-1]
            p = day["o"] + (day["c"] - day["o"]) * frac + bridge * scale / math.sqrt(SESSION_MIN) * 3
            out.append({"t": (start + timedelta(minutes=i + 1)).isoformat(), "o": prev, "h": max(prev, p),
                        "l": min(prev, p), "c": p, "v": round(abs(rng.gauss(0, 1)) * day["v"] / SESSION_MIN * 2)})
            prev = p
        return out

    @staticmethod
    def _round(c: dict, currency: str) -> dict:
        """원화 종목은 호가 단위 격자에 맞춘다 (실제 시세처럼). 미국 주식은 센트, 지수는 소수 셋째 자리."""
        def fit(p: float) -> float:
            if currency == "KRW":
                tick = tick_size(p)
                return round(p / tick) * tick
            return round(p, 2 if currency == "USD" else 3)
        return {**c, **{k: fit(c[k]) for k in ("o", "h", "l", "c")}}

    # ---- 제공자 인터페이스 ----
    def candles(self, symbol: str, interval: str = "1d", count: int = 200) -> list[dict]:
        daily = self._daily(symbol)
        if interval == "1d":
            return daily[-count:]
        today, minute = self._session_minute()
        currency = self._meta(symbol)[3]
        day = self._raw_daily(symbol)[-1]
        upto = minute if day["t"] == today.isoformat() and minute is not None else SESSION_MIN
        return [self._round(m, currency) for m in self._minutes(symbol, day, upto)][-count:]

    def quotes(self, symbols: list[str]) -> dict[str, dict]:
        out = {}
        for s in symbols:
            try:
                last = self._daily(s)[-1]
            except MarketError:
                continue
            out[s] = {"symbol": s, "price": last["c"], "currency": self._meta(s)[3],
                      "timestamp": self.clock().isoformat(timespec="seconds")}
        return out

    def info(self, symbol: str) -> dict:
        sym, name, market, currency, base = self._meta(symbol)
        rng = random.Random(_seed("info", symbol))
        return {"symbol": sym, "name": name, "english_name": "", "market": market, "currency": currency,
                "security_type": "ETF" if sym in SAMPLE_ETFS else "STOCK", "status": "ACTIVE", "is_common": True, "list_date": None,
                "shares_outstanding": round(rng.uniform(5e7, 6e9)), "suspended": False, "leverage": None}

    def orderbook(self, symbol: str) -> dict:
        price = self._daily(symbol)[-1]["c"]
        currency = self._meta(symbol)[3]
        tick = tick_size(price, currency, "ETF" if symbol in SAMPLE_ETFS else None)
        mid = round(price / tick) * tick
        rng = random.Random(_seed("book", symbol, self.clock().strftime("%Y%m%d%H%M")))
        asks = [{"price": round(mid + tick * (i + 1), 2), "volume": rng.randint(100, 30_000)} for i in range(10)]
        bids = [{"price": round(mid - tick * i, 2), "volume": rng.randint(100, 30_000)} for i in range(10)]
        return {"asks": asks, "bids": bids, "timestamp": self.clock().isoformat(timespec="seconds"), "currency": currency}

    def price_limits(self, symbol: str) -> dict:
        daily = self._daily(symbol)
        if self._meta(symbol)[3] != "KRW" or len(daily) < 2:
            return {"upper": None, "lower": None}
        base = daily[-2]["c"]
        return {"upper": round(base * 1.3), "lower": round(base * 0.7)}

    def investor_trading(self, symbol: str, count: int = 10) -> list[dict]:
        daily = self._daily(symbol)[-count:]
        out = []
        for d in reversed(daily):
            rng = random.Random(_seed("investor", symbol, d["t"]))
            f, i = rng.gauss(0, d["v"] * 0.08), rng.gauss(0, d["v"] * 0.06)
            out.append({"date": d["t"], "individual": round(-(f + i)), "foreigner": round(f), "institution": round(i)})
        return out

    def rankings(self, kind: str, count: int = 20, conn: sqlite3.Connection | None = None) -> list[dict]:
        rows = []
        for sym, name, market, currency, _ in SAMPLE_STOCKS:
            if currency != "KRW" or sym in SAMPLE_ETFS:  # 주식 랭킹 (ETF 제외)
                continue
            daily = self._daily(sym)
            last, prev = daily[-1], daily[-2]
            rows.append({"symbol": sym, "name": name, "price": last["c"], "change_rate": last["c"] / prev["c"] - 1,
                         "volume": last["v"], "amount": last["v"] * last["c"], "currency": currency})
        key = {"amount": "amount", "volume": "volume", "gainers": "change_rate", "losers": "change_rate"}[kind]
        rows.sort(key=lambda r: r[key], reverse=kind != "losers")
        return [{"rank": i + 1, **r} for i, r in enumerate(rows[:count])]

    def indicators(self) -> list[dict]:
        out = []
        for sym, name, unit in INDICATORS:
            daily = self._daily(sym)
            last, prev = daily[-1]["c"], daily[-2]["c"]
            out.append({"symbol": sym, "name": name, "unit": unit, "price": last, "change": last - prev,
                        "change_rate": last / prev - 1 if prev else None})
        return out

    def indicator_closes(self, symbol: str, count: int = 120) -> list[dict]:
        """지수 일봉 종가 [{t, c}] (오래된 것부터) — 벤치마크 비교용."""
        return [{"t": d["t"], "c": d["c"]} for d in self._daily(symbol)[-count:]]

    def session(self) -> dict:
        now = self.clock()
        today, minute = self._session_minute()
        is_open = minute is not None and minute < SESSION_MIN and now.weekday() < 5
        return {"date": today.isoformat(), "is_open": is_open, "start": "09:00", "end": "15:30",
                "note": "샘플: 주말만 휴장으로 계산 (공휴일 미반영)"}

    def universe(self, conn: sqlite3.Connection) -> list[dict]:
        """종목 전체 (운용 지시에서 종목 이름 찾기용)."""
        return [{"symbol": s, "name": n, "security_type": "ETF" if s in SAMPLE_ETFS else "STOCK"} for s, n, _, _, _ in SAMPLE_STOCKS]

    def search(self, conn: sqlite3.Connection, q: str, limit: int = 20) -> list[dict]:
        ql = q.strip().lower()
        hits = [{"symbol": s, "name": n, "market": m, "security_type": "ETF" if s in SAMPLE_ETFS else "STOCK"}
                for s, n, m, _, _ in SAMPLE_STOCKS if not ql or ql in s.lower() or ql in n.lower()]
        return hits[:limit]

    def names(self, conn: sqlite3.Connection, symbols: list[str]) -> dict[str, str]:
        return {s: _SAMPLE[s][1] for s in symbols if s in _SAMPLE}

    def usd_krw(self) -> float:
        return SAMPLE_USD_KRW

    def sectors(self) -> list[dict]:
        return [{"id": x["id"], "name": x["name"], "depth": 0 if x["parent"] is None else 1, "parent": x["parent"]}
                for x in SAMPLE_SECTORS]

    def sector(self, sector_id: str) -> dict:
        x = _SAMPLE_SECTORS.get(sector_id)
        if x is None:
            raise MarketError(f"섹터를 찾을 수 없습니다: {sector_id}", 404)
        return {**self.sector_perf(sector_id), "summary": f"샘플 섹터 — {x['name']} 관련 종목", "stocks": self.sector_stocks(sector_id),
                "etfs": [{"symbol": s, "name": _SAMPLE[s][1]} for s in x["etfs"]]}

    def sector_stocks(self, sector_id: str) -> list[dict]:
        x = _SAMPLE_SECTORS.get(sector_id)
        if x is None:
            raise MarketError(f"섹터를 찾을 수 없습니다: {sector_id}", 404)
        return [{"symbol": s, "name": _SAMPLE[s][1], "market_cap": None, "trading_amount": None} for s in x["stocks"]]

    def sector_perf(self, sector_id: str) -> dict:
        """섹터 이름과 기간 등락률만 (구성 주식 평균) — 섹터를 넓게 훑을 때."""
        x = _SAMPLE_SECTORS.get(sector_id)
        if x is None:
            raise MarketError(f"섹터를 찾을 수 없습니다: {sector_id}", 404)
        members = [self._daily(s) for s in x["stocks"]]
        rates = {k: sum(d[-1]["c"] / d[-1 - n]["c"] - 1 for d in members) / len(members) if members else None
                 for k, n in (("1d", 1), ("1w", 5), ("1mo", 20), ("3mo", 60))}
        return {"id": x["id"], "name": x["name"], "change_rates": rates}


# ---- 토스증권 ----
def _toss_candle(c: dict, interval: str) -> dict:
    t = c.get("timestamp") or ""
    return {"t": t[:10] if interval == "1d" else t, "o": num(c.get("openPrice")), "h": num(c.get("highPrice")),
            "l": num(c.get("lowPrice")), "c": num(c.get("closePrice")), "v": num(c.get("volume")) or 0}


class TossMarket:
    key = "toss"
    title = "토스증권"

    def __init__(self, client: TossClient | None = None):
        self.client = client or TossClient()

    def _wrap(self, fn: Callable[[], object]) -> object:
        try:
            return fn()
        except TossError as e:
            raise MarketError(str(e), 404 if e.status == 404 else 502) from None

    def candles(self, symbol: str, interval: str = "1d", count: int = 200) -> list[dict]:
        def load() -> list[dict]:
            out: list[dict] = []
            before = None
            while len(out) < count:  # 한 번에 200개까지 — 더 필요하면 nextBefore 로 이어 받는다
                page = self.client.candles(symbol, interval, min(200, count - len(out)), before)
                out += [_toss_candle(c, interval) for c in page.get("candles") or []]
                before = page.get("nextBefore")
                if not before or not page.get("candles"):
                    break
            return list(reversed(out))  # 토스는 최신순
        return self._wrap(lambda: cached(("candles", symbol, interval, count), 60 if interval == "1d" else 15, load))

    def quotes(self, symbols: list[str]) -> dict[str, dict]:
        if not symbols:
            return {}
        key = ("prices", tuple(sorted(symbols)))
        rows = self._wrap(lambda: cached(key, 3, lambda: self.client.prices(symbols)))
        return {r["symbol"]: {"symbol": r["symbol"], "price": num(r.get("lastPrice")), "currency": r.get("currency"),
                              "timestamp": r.get("timestamp")} for r in rows}

    def info(self, symbol: str) -> dict:
        rows = self._wrap(lambda: cached(("stocks", symbol), 3600, lambda: self.client.stocks([symbol])))
        if not rows:
            raise MarketError(f"종목을 찾을 수 없습니다: {symbol}", 404)
        r = rows[0]
        kr = r.get("koreanMarketDetail") or {}
        return {"symbol": r.get("symbol"), "name": r.get("name"), "english_name": r.get("englishName"),
                "market": r.get("market"), "currency": r.get("currency"), "security_type": r.get("securityType"),
                "status": r.get("status"), "is_common": r.get("isCommonShare"), "list_date": r.get("listDate"),
                "shares_outstanding": num(r.get("sharesOutstanding")),
                "suspended": bool(kr.get("krxTradingSuspended")) if isinstance(kr, dict) else False,
                "leverage": num(r.get("leverageFactor"))}

    def orderbook(self, symbol: str) -> dict:
        r = self._wrap(lambda: cached(("book", symbol), 2, lambda: self.client.orderbook(symbol)))
        level = lambda x: {"price": num(x.get("price")), "volume": num(x.get("volume"))}  # noqa: E731
        asks = sorted((level(a) for a in r.get("asks") or []), key=lambda x: x["price"] or 0)
        bids = sorted((level(b) for b in r.get("bids") or []), key=lambda x: -(x["price"] or 0))
        return {"asks": asks, "bids": bids, "timestamp": r.get("timestamp"), "currency": r.get("currency")}

    def price_limits(self, symbol: str) -> dict:
        r = self._wrap(lambda: cached(("limits", symbol), 300, lambda: self.client.price_limits(symbol)))
        return {"upper": num(r.get("upperLimitPrice")), "lower": num(r.get("lowerLimitPrice"))}

    def investor_trading(self, symbol: str, count: int = 10) -> list[dict]:
        r = self._wrap(lambda: cached(("investor", symbol, count), 600,
                                      lambda: self.client.investor_trading(symbol, count)))
        net = lambda g: num((g or {}).get("netBuyVolume")) if isinstance(g, dict) else None  # noqa: E731
        return [{"date": x.get("date"), "individual": net(x.get("individual")), "foreigner": net(x.get("foreigner")),
                 "institution": net(x.get("institution"))} for x in r.get("records") or [] if isinstance(x, dict)]

    def rankings(self, kind: str, count: int = 20, conn: sqlite3.Connection | None = None) -> list[dict]:
        duration = "1d" if kind in ("gainers", "losers") else "realtime"
        r = self._wrap(lambda: cached(("rank", kind, count), 30,
                                      lambda: self.client.rankings(RANKING_KINDS[kind], "KR", duration, count)))
        items = [x for x in r.get("rankings") or [] if isinstance(x, dict)]
        names = self.names(conn, [x["symbol"] for x in items]) if conn is not None else {}
        return [{"rank": x.get("rank"), "symbol": x.get("symbol"), "name": names.get(x.get("symbol"), ""),
                 "price": num((x.get("price") or {}).get("lastPrice")),
                 "change_rate": num((x.get("price") or {}).get("changeRate")),
                 "volume": num(x.get("tradingVolume")), "amount": num(x.get("tradingAmount")),
                 "currency": x.get("currency")} for x in items]

    def indicators(self) -> list[dict]:
        def load() -> list[dict]:
            prices = {r["symbol"]: num(r.get("lastPrice"))
                      for r in self.client.indicator_prices([s for s, _, _ in INDICATORS])}
            out = []
            for sym, name, unit in INDICATORS:
                prev = None
                try:
                    page = self.client.call("GET", f"/api/v1/market-indicators/{sym}/candles", group="MARKET_INDICATOR",
                                            params={"interval": "1d", "count": 2})
                    cs = (page or {}).get("candles") or []
                    prev = num(cs[1].get("closePrice")) if len(cs) > 1 else None
                except TossError:
                    pass
                last = prices.get(sym)
                out.append({"symbol": sym, "name": name, "unit": unit, "price": last,
                            "change": (last - prev) if last is not None and prev is not None else None,
                            "change_rate": (last / prev - 1) if last and prev else None})
            return out
        return self._wrap(lambda: cached(("indicators",), 30, load))

    def indicator_closes(self, symbol: str, count: int = 120) -> list[dict]:
        """지수 일봉 종가 [{t, c}] (오래된 것부터) — 벤치마크 비교용. 10분 캐시."""
        def load() -> list[dict]:
            page = self.client.call("GET", f"/api/v1/market-indicators/{symbol}/candles", group="MARKET_INDICATOR",
                                    params={"interval": "1d", "count": count})
            rows = [{"t": (c.get("timestamp") or "")[:10], "c": num(c.get("closePrice"))} for c in (page or {}).get("candles") or []
                    if isinstance(c, dict)]
            return sorted((r for r in rows if r["t"] and r["c"]), key=lambda r: r["t"])
        return self._wrap(lambda: cached(("indicator_closes", symbol, count), 600, load))

    def session(self) -> dict:
        r = self._wrap(lambda: cached(("calendar",), 600, self.client.market_calendar_kr))
        today = (r or {}).get("today") or {}
        regular = ((today.get("integrated") or {}).get("regularMarket") or {}) if isinstance(today, dict) else {}
        start, end = regular.get("startTime"), regular.get("endTime")
        is_open = False
        if start and end:
            try:
                now = now_kst()
                is_open = datetime.fromisoformat(start) <= now <= datetime.fromisoformat(end)
            except ValueError:
                pass
        hhmm = lambda v: v[11:16] if isinstance(v, str) and len(v) >= 16 else None  # noqa: E731
        return {"date": today.get("date"), "is_open": is_open, "start": hhmm(start), "end": hhmm(end),
                "note": None if start else "오늘은 휴장일입니다"}

    # --- 종목 검색용 목록 (하루에 한 번 갱신) ---
    def _refresh_universe(self, conn: sqlite3.Connection) -> None:
        rows = []
        for market in UNIVERSE_MARKETS:
            for r in self.client.stocks_all(market):
                if r.get("symbol") and r.get("name"):
                    rows.append((r["symbol"], r["name"], market, r.get("securityType") or ""))
        if rows:
            conn.execute("DELETE FROM invest_universe")
            conn.executemany("INSERT OR REPLACE INTO invest_universe (symbol, name, market, security_type) "
                             "VALUES (?, ?, ?, ?)", rows)
            conn.commit()

    def ensure_universe(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT COUNT(*), MIN(refreshed_at) FROM invest_universe").fetchone()
        if not row[0]:
            self._wrap(lambda: self._refresh_universe(conn))
        elif row[1] and datetime.fromisoformat(row[1]) < datetime.now() - UNIVERSE_TTL:
            threading.Thread(target=self._refresh_in_background, daemon=True).start()

    def _refresh_in_background(self) -> None:
        from app.core.db import connect
        conn = connect()
        try:
            self._refresh_universe(conn)
        except Exception:  # noqa: BLE001 — 다음 검색 때 다시 시도
            pass
        finally:
            conn.close()

    def universe(self, conn: sqlite3.Connection) -> list[dict]:
        """종목 전체 (운용 지시에서 종목 이름 찾기용, 하루 1회 갱신하는 검색 목록)."""
        self.ensure_universe(conn)
        return [dict(r) for r in conn.execute("SELECT symbol, name, security_type FROM invest_universe")]

    def search(self, conn: sqlite3.Connection, q: str, limit: int = 20) -> list[dict]:
        self.ensure_universe(conn)
        q = q.strip()
        if not q:
            return []
        rows = conn.execute(
            "SELECT symbol, name, market, security_type FROM invest_universe WHERE symbol LIKE ? OR name LIKE ? "
            "ORDER BY (upper(symbol) = upper(?)) DESC, (name = ?) DESC, (name LIKE ?) DESC, "
            "(market IN ('KOSPI', 'KOSDAQ')) DESC, length(name), symbol LIMIT ?",
            (f"{q}%", f"%{q}%", q, q, f"{q}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def names(self, conn: sqlite3.Connection | None, symbols: list[str]) -> dict[str, str]:
        if not symbols:
            return {}
        out: dict[str, str] = {}
        if conn is not None:
            marks = ",".join("?" * len(symbols))
            out = {r[0]: r[1] for r in conn.execute(
                f"SELECT symbol, name FROM invest_universe WHERE symbol IN ({marks})", symbols)}
        missing = [s for s in symbols if s not in out]
        if missing:
            try:
                for r in self.client.stocks(missing):
                    out[r.get("symbol")] = r.get("name") or ""
            except TossError:
                pass
        return out

    def usd_krw(self) -> float | None:
        return self._wrap(lambda: cached(("fx", "USD"), 60, lambda: self.client.exchange_rate("USD", "KRW")))

    def sectors(self) -> list[dict]:
        rows = self._wrap(lambda: cached(("sectors",), 86400, self.client.sectors))  # 자주 안 바뀜 — 하루 캐시
        return [{"id": str(r.get("sectorId")), "name": r.get("name") or "", "depth": r.get("depth"),
                 "parent": str(r["parentSectorId"]) if r.get("parentSectorId") else None} for r in rows]

    def sector_perf(self, sector_id: str) -> dict:
        """섹터 이름과 기간 등락률만 (요약 API 하나) — 섹터를 넓게 훑을 때."""
        detail = self._wrap(lambda: cached(("sector", sector_id), 600, lambda: self.client.sector(sector_id)))
        return _toss_perf(sector_id, detail)

    def sector(self, sector_id: str) -> dict:
        detail = self._wrap(lambda: cached(("sector", sector_id), 600, lambda: self.client.sector(sector_id)))
        etfs = self._wrap(lambda: cached(("sector_etfs", sector_id), 3600, lambda: self.client.sector_etfs(sector_id)))
        return {**_toss_perf(sector_id, detail), "summary": detail.get("summary"), "stocks": self.sector_stocks(sector_id),
                "etfs": [{"symbol": r.get("symbol"), "name": r.get("name") or ""} for r in etfs if r.get("symbol")]}

    def sector_stocks(self, sector_id: str) -> list[dict]:
        """섹터 구성 보통주(하위 섹터 포함, 시가총액순) — 시가총액은 매일, 거래대금은 당일 누적."""
        stocks = self._wrap(lambda: cached(("sector_stocks", sector_id), 600, lambda: self.client.sector_stocks(sector_id)))
        return [{"symbol": r.get("symbol"), "name": r.get("name") or "", "market_cap": num(r.get("marketCapitalization")),
                 "trading_amount": num(r.get("tradingAmount"))} for r in stocks if r.get("symbol")]


def _toss_perf(sector_id: str, detail: dict) -> dict:
    rates = {r.get("duration"): num(r.get("changeRate"))
             for r in ((detail.get("performance") or {}).get("changeRates") or []) if isinstance(r, dict)}
    return {"id": sector_id, "name": detail.get("name") or "", "change_rates": rates}


def provider() -> SampleMarket | TossMarket:
    return TossMarket() if config.toss_configured() else SampleMarket()


def snapshot(p: SampleMarket | TossMarket, symbol: str) -> dict:
    """종목 머리글·정보 탭: 현재가, 전일 대비, 오늘 시고저, 최근 범위 고저, 상하한가, 기본 정보."""
    symbol = symbol_ok(symbol) if p.key == "toss" else symbol.strip()
    info = p.info(symbol)
    daily = p.candles(symbol, "1d", 200)
    quote = p.quotes([symbol]).get(symbol) or {}
    price = quote.get("price") or (daily[-1]["c"] if daily else None)
    prev = daily[-2]["c"] if len(daily) >= 2 else None
    today = daily[-1] if daily else {}
    closes = [c["c"] for c in daily if c["c"] is not None]
    volumes = [c["v"] for c in daily]
    past = volumes[-21:-1] or volumes  # 오늘을 뺀 20일 — 기술 지표의 거래량 배수와 같은 기준
    try:
        limits = p.price_limits(symbol)
    except MarketError:
        limits = {"upper": None, "lower": None}
    currency = info.get("currency") or quote.get("currency") or "KRW"
    shares = info.get("shares_outstanding")
    return {
        **info,
        "provider": p.key,
        "price": price,
        "currency": currency,
        "prev_close": prev,
        "change": (price - prev) if price is not None and prev else None,
        "change_rate": (price / prev - 1) if price and prev else None,
        "open": today.get("o"), "high": today.get("h"), "low": today.get("l"), "volume": today.get("v"),
        "day": today.get("t"),
        "range_high": max((c["h"] for c in daily), default=None),
        "range_low": min((c["l"] for c in daily), default=None),
        "range_bars": len(daily),
        "avg_volume_20": round(sum(past) / len(past)) if past else None,
        "market_cap": price * shares if price and shares else None,
        "upper_limit": limits.get("upper"), "lower_limit": limits.get("lower"),
        "tick": tick_size(price or 0, currency, info.get("security_type")),
        "timestamp": quote.get("timestamp"),
        "technicals": indicators.summary(closes, volumes) if closes else None,
    }
