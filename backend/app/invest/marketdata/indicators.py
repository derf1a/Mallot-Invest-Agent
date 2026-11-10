"""기술 지표 — 차트 이동평균선과 기술적 분석 에이전트 도구가 같이 쓴다. 입력은 오래된 것부터의 종가 목록."""

import math


def sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        out.append(total / n if i >= n - 1 else None)
    return out


def ema(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    k = 2 / (n + 1)
    prev: float | None = None
    for i, v in enumerate(values):
        if i < n - 1:
            out.append(None)
            continue
        prev = sum(values[i - n + 1:i + 1]) / n if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(values: list[float], n: int = 14) -> float | None:
    """Wilder RSI (마지막 값)."""
    if len(values) <= n:
        return None
    gains = [max(values[i] - values[i - 1], 0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0) for i in range(1, len(values))]
    avg_g, avg_l = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, loss in zip(gains[n:], losses[n:]):
        avg_g = (avg_g * (n - 1) + g) / n
        avg_l = (avg_l * (n - 1) + loss) / n
    if avg_l == 0:
        return 100.0
    return 100 - 100 / (1 + avg_g / avg_l)


def macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict | None:
    if len(values) < slow + signal:
        return None
    f, s = ema(values, fast), ema(values, slow)
    line = [a - b for a, b in zip(f, s) if a is not None and b is not None]
    sig = ema(line, signal)
    if sig[-1] is None:
        return None
    return {"macd": line[-1], "signal": sig[-1], "histogram": line[-1] - sig[-1]}


def volatility(values: list[float], window: int = 20) -> float | None:
    """일간 수익률 표준편차 × √252 (연율화, 소수)."""
    tail = values[-(window + 1):]
    if len(tail) < 3:
        return None
    rets = [math.log(tail[i] / tail[i - 1]) for i in range(1, len(tail)) if tail[i - 1] > 0 and tail[i] > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def change(values: list[float], days: int) -> float | None:
    """days 거래일 전 대비 수익률 (소수)."""
    if len(values) <= days or values[-days - 1] <= 0:
        return None
    return values[-1] / values[-days - 1] - 1


def summary(closes: list[float], volumes: list[float] | None = None) -> dict:
    """에이전트 도구에 넘기는 요약 — 숫자는 반올림해 토큰을 아낀다."""
    def r(v: float | None, nd: int = 2) -> float | None:
        return None if v is None else round(v, nd)

    last = closes[-1] if closes else None
    ma = {f"ma{n}": r(sma(closes, n)[-1] if len(closes) >= n else None) for n in (5, 20, 60, 120)}
    out = {
        "last_close": last,
        "bars": len(closes),
        **ma,
        "rsi14": r(rsi(closes)),
        "macd": {k: r(v) for k, v in (macd(closes) or {}).items()} or None,
        "volatility_20d_annualized": r(volatility(closes), 4),
        "return_5d": r(change(closes, 5), 4),
        "return_20d": r(change(closes, 20), 4),
        "return_60d": r(change(closes, 60), 4),
        "high_in_range": max(closes) if closes else None,
        "low_in_range": min(closes) if closes else None,
    }
    if volumes and len(volumes) >= 21:
        avg20 = sum(volumes[-21:-1]) / 20
        out["volume_vs_20d_avg"] = r(volumes[-1] / avg20, 2) if avg20 else None
    return out


ALIGN_KO = {1: "정배열", -1: "역배열", 0: "혼조"}


def alignment(close: float | None, ma5: float | None, ma20: float | None, ma60: float | None) -> dict | None:
    """이동평균 정렬 — 판정(trend)은 5·20·60일선의 순서만 본다(정배열 5>20>60 · 역배열 5<20<60 · 그 밖 혼조).
    선 순서는 늦게 바뀌어서, 급반등하면 종가가 모든 선 위인데도 역배열로 남는다 — 그래서 종가 위치(price_vs_ma)와
    둘이 엇갈릴 때의 설명(note)을 같이 준다. 값이 하나라도 없으면 None."""
    if not (close and ma5 and ma20 and ma60):
        return None
    trend = 1 if ma5 > ma20 > ma60 else -1 if ma5 < ma20 < ma60 else 0
    mas = (ma5, ma20, ma60)
    pos = "above_all" if all(close > m for m in mas) else "below_all" if all(close < m for m in mas) else "between"
    note = ("역배열이지만 종가는 5·20·60일선 모두 위 — 반등 중, 선 순서는 아직 안 바뀜" if trend == -1 and pos == "above_all" else
            "정배열이지만 종가는 5·20·60일선 모두 아래 — 꺾이는 중, 선 순서는 아직 안 바뀜" if trend == 1 and pos == "below_all" else None)
    return {"trend": trend, "label": ALIGN_KO[trend], "price_vs_ma": pos, "note": note}
