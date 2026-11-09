"""API 요청 통계 (메모리, 서버가 켜진 뒤부터). 경로 템플릿(/api/transactions/{tx_id}) 단위로 집계한다."""

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from threading import Lock

STARTED_AT = time.time()


@dataclass
class RouteStats:
    count: int = 0
    errors: int = 0          # 5xx
    client_errors: int = 0   # 4xx
    latencies: deque = field(default_factory=lambda: deque(maxlen=500))
    last_at: float = 0.0


_stats: dict[str, RouteStats] = defaultdict(RouteStats)
_lock = Lock()


def observe(method: str, path: str, status: int, ms: float) -> None:
    with _lock:
        s = _stats[f"{method} {path}"]
        s.count += 1
        s.errors += status >= 500
        s.client_errors += 400 <= status < 500
        s.latencies.append(ms)
        s.last_at = time.time()


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(len(v) * q))], 1)


def snapshot() -> dict:
    with _lock:
        rows = [
            {"route": k, "count": s.count, "errors": s.errors, "client_errors": s.client_errors,
             "p50_ms": _pct(list(s.latencies), 0.5), "p95_ms": _pct(list(s.latencies), 0.95),
             "last_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s.last_at)) if s.last_at else None}
            for k, s in _stats.items()
        ]
    rows.sort(key=lambda r: r["count"], reverse=True)
    return {
        "since": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(STARTED_AT)),
        "uptime_sec": int(time.time() - STARTED_AT),
        "total": sum(r["count"] for r in rows),
        "errors": sum(r["errors"] for r in rows),
        "routes": rows,
    }


def reset() -> None:
    with _lock:
        _stats.clear()
