"""관심 그룹·관심 종목 — 토스 앱의 관심 그룹은 Open API 로 제공되지 않아 앱 안에서 관리한다 (검색해서 추가).

한 종목이 여러 그룹에 들어갈 수 있다. 그룹은 최소 하나 남는다(마지막 그룹은 지울 수 없음).
"""

import sqlite3

from app.invest.marketdata import market
from app.invest.marketdata.market import MarketError
from app.invest.broker.toss import TossError


class WatchError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def default_group(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM invest_watch_groups ORDER BY sort_order, id LIMIT 1").fetchone()
    if row:
        return row[0]
    cur = conn.execute("INSERT INTO invest_watch_groups (name) VALUES ('관심')")
    conn.commit()
    return cur.lastrowid


def _group(conn: sqlite3.Connection, group_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM invest_watch_groups WHERE id = ?", (group_id,)).fetchone()
    if row is None:
        raise WatchError("관심 그룹을 찾을 수 없습니다", 404)
    return row


def _quotes(symbols: list[str]) -> dict[str, dict]:
    if not symbols:
        return {}
    try:
        return market.provider().quotes(symbols)
    except (MarketError, TossError):
        return {}


def symbols(conn: sqlite3.Connection) -> list[dict]:
    """모든 그룹의 관심 종목 (중복 없이, 그룹 순서 → 그룹 안 순서). [{symbol, name, market, security_type, group_ids}]"""
    out: dict[str, dict] = {}
    for r in conn.execute("SELECT w.* FROM invest_watchlist w JOIN invest_watch_groups g ON g.id = w.group_id "
                          "ORDER BY g.sort_order, g.id, w.sort_order, w.added_at"):
        item = out.setdefault(r["symbol"], {"symbol": r["symbol"], "name": r["name"], "market": r["market"],
                                            "security_type": r["security_type"], "group_ids": []})
        item["group_ids"].append(r["group_id"])
    return list(out.values())


def flat(conn: sqlite3.Connection) -> list[dict]:
    """머리글 ☆·시장 탭용 — 중복 없는 관심 종목 + 현재가."""
    items = symbols(conn)
    quotes = _quotes([i["symbol"] for i in items])
    for i in items:
        i["price"] = (quotes.get(i["symbol"]) or {}).get("price")
    return items


def groups(conn: sqlite3.Connection) -> list[dict]:
    default_group(conn)
    rows = [dict(r) for r in conn.execute("SELECT * FROM invest_watch_groups ORDER BY sort_order, id")]
    items = [dict(r) for r in conn.execute("SELECT * FROM invest_watchlist ORDER BY sort_order, added_at")]
    quotes = _quotes(sorted({i["symbol"] for i in items}))
    for g in rows:
        g["items"] = [{**i, "price": (quotes.get(i["symbol"]) or {}).get("price")} for i in items
                      if i["group_id"] == g["id"]]
    return rows


def add(conn: sqlite3.Connection, symbol: str, group_id: int | None = None, name: str = "", mkt: str = "",
        security_type: str = "") -> None:
    symbol = symbol.strip()
    gid = group_id if group_id is not None else default_group(conn)
    _group(conn, gid)
    if not name:
        try:
            info = market.provider().info(symbol)
        except (MarketError, TossError) as e:
            raise WatchError(str(e), getattr(e, "status", None) or 502) from None
        name, mkt, security_type = info.get("name") or "", info.get("market") or "", info.get("security_type") or ""
    nxt = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM invest_watchlist WHERE group_id = ?",
                       (gid,)).fetchone()[0]
    conn.execute("INSERT OR IGNORE INTO invest_watchlist (group_id, symbol, name, market, security_type, sort_order) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (gid, symbol, name, mkt, security_type, nxt))
    conn.commit()


def remove(conn: sqlite3.Connection, symbol: str, group_id: int | None = None) -> None:
    if group_id is None:
        conn.execute("DELETE FROM invest_watchlist WHERE symbol = ?", (symbol,))
    else:
        conn.execute("DELETE FROM invest_watchlist WHERE symbol = ? AND group_id = ?", (symbol, group_id))
    conn.commit()


def _name(name: str) -> str:
    name = name.strip()[:30]
    if not name:
        raise WatchError("그룹 이름을 입력하세요")
    return name


def create_group(conn: sqlite3.Connection, name: str) -> int:
    nxt = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM invest_watch_groups").fetchone()[0]
    cur = conn.execute("INSERT INTO invest_watch_groups (name, sort_order) VALUES (?, ?)", (_name(name), nxt))
    conn.commit()
    return cur.lastrowid


def rename_group(conn: sqlite3.Connection, group_id: int, name: str) -> None:
    _group(conn, group_id)
    conn.execute("UPDATE invest_watch_groups SET name = ? WHERE id = ?", (_name(name), group_id))
    conn.commit()


def delete_group(conn: sqlite3.Connection, group_id: int) -> None:
    _group(conn, group_id)
    if conn.execute("SELECT COUNT(*) FROM invest_watch_groups").fetchone()[0] <= 1:
        raise WatchError("마지막 관심 그룹은 지울 수 없습니다", 409)
    conn.execute("DELETE FROM invest_watch_groups WHERE id = ?", (group_id,))
    conn.commit()
