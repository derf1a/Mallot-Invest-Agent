"""투자 뉴스 수집 — Google 뉴스 RSS 검색(공개 피드)으로 종목별·시장 뉴스를 모아 invest_news 에 쌓는다.

- 종목: "<종목명> 주가" / 시장: "코스피 증시", "미국 증시"
- 같은 질의는 15분 안에 다시 받지 않는다 (캐시). 기사 본문은 가져오지 않고 제목·출처·시각·요약 문구만 쓴다.
- 요청은 실험실 리서치와 같은 http_request — 공인 주소만, 리다이렉트도 공인 주소로만, 본문 크기 제한.
"""

import hashlib
import html
import re
import sqlite3
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

from app.core import net

RSS_URL = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
MARKET_QUERIES = ("코스피 증시", "미국 증시")
CACHE_MINUTES = 15
_TAG = re.compile(r"<[^>]+>")

# 테스트에서 바꿔 끼운다: (method, url, headers=None, data=None, timeout=15) -> (status, body)
HTTP: Callable[..., tuple[int, bytes]] = lambda *a, **kw: net.HTTP_REQUEST(*a, **kw)  # noqa: E731


class NewsError(Exception):
    pass


def symbol_query(name: str) -> str:
    return f"{name} 주가"


def parse_rss(body: bytes) -> list[dict]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise NewsError(f"뉴스 피드를 해석하지 못했습니다: {e}") from None
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or not link.startswith("https://"):
            continue
        source_el = it.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]  # Google 뉴스 제목 끝의 " - 언론사"
        published = None
        if it.findtext("pubDate"):
            try:
                published = parsedate_to_datetime(it.findtext("pubDate")).astimezone().strftime("%Y-%m-%d %H:%M")
            except (TypeError, ValueError):
                pass
        snippet = html.unescape(_TAG.sub(" ", it.findtext("description") or ""))
        snippet = re.sub(r"\s+", " ", snippet).strip()
        if snippet.startswith(title):
            snippet = snippet[len(title):].strip(" -")
        if source and snippet.endswith(source):  # Google 뉴스 설명은 "제목 언론사" 형태가 많다
            snippet = snippet[: -len(source)].strip(" -")
        items.append({"title": html.unescape(title), "url": link, "source": source, "published_at": published,
                      "snippet": snippet[:300]})
    return items


def fetch(conn: sqlite3.Connection, query: str, symbol: str | None = None, limit: int = 15,
          force: bool = False) -> list[dict]:
    """질의 하나를 받아 저장하고, 그 질의의 최근 기사들을 돌려준다."""
    since = (datetime.now() - timedelta(minutes=CACHE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    fresh = conn.execute("SELECT 1 FROM invest_news WHERE query = ? AND fetched_at >= ? LIMIT 1",
                         (query, since)).fetchone()
    if force or not fresh:
        status, body = HTTP("GET", RSS_URL.format(q=urllib.parse.quote(query)), headers={"Accept": "application/rss+xml"})
        if status != 200:
            raise NewsError(f"뉴스를 받지 못했습니다 (HTTP {status or '연결 실패'}): {query}")
        for item in parse_rss(body)[:limit]:
            url_hash = hashlib.sha256(item["url"].encode()).hexdigest()
            conn.execute(
                "INSERT INTO invest_news (url_hash, url, title, source, published_at, snippet, query) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(url_hash) DO UPDATE SET "
                "fetched_at = datetime('now', 'localtime'), query = excluded.query",
                (url_hash, item["url"], item["title"], item["source"], item["published_at"], item["snippet"], query))
            news_id = conn.execute("SELECT id FROM invest_news WHERE url_hash = ?", (url_hash,)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO invest_news_links (news_id, symbol) VALUES (?, ?)", (news_id, symbol or ""))
        conn.commit()
    return recent(conn, query=query, limit=limit)


def recent(conn: sqlite3.Connection, *, query: str | None = None, symbols: list[str] | None = None,
           market: bool = False, limit: int = 50) -> list[dict]:
    """최근 기사. query 로 거르거나, 종목들(symbols)·시장(market) 링크로 거른다. 각 기사에 연결된 종목 목록(symbols)."""
    where, args = [], []
    if query is not None:
        where.append("n.query = ?")
        args.append(query)
    if symbols is not None or market:
        keys = list(symbols or []) + ([""] if market else [])
        if not keys:
            return []
        where.append(f"n.id IN (SELECT news_id FROM invest_news_links WHERE symbol IN ({','.join('?' * len(keys))}))")
        args += keys
    sql = ("SELECT n.*, (SELECT group_concat(symbol) FROM invest_news_links l WHERE l.news_id = n.id) AS links "
           "FROM invest_news n" + (f" WHERE {' AND '.join(where)}" if where else ""))
    rows = conn.execute(sql + " ORDER BY COALESCE(n.published_at, n.fetched_at) DESC, n.id DESC LIMIT ?", (*args, limit))
    out = []
    for r in rows:
        d = dict(r)
        links = [x for x in (d.pop("links") or "").split(",")]
        d["symbols"] = [x for x in links if x]
        d["market"] = "" in links
        d["symbol"] = d["symbols"][0] if d["symbols"] else None
        out.append(d)
    return out


def collect(conn: sqlite3.Connection, targets: list[dict], include_market: bool, per_query: int = 10,
            force: bool = False, on_error: Callable[[str], None] | None = None) -> dict[str | None, list[dict]]:
    """대상 종목들({symbol, name})과 시장 뉴스를 모은다. {symbol 또는 None(시장): 기사 목록}"""
    out: dict[str | None, list[dict]] = {}
    for t in targets:
        try:
            out[t["symbol"]] = fetch(conn, symbol_query(t["name"] or t["symbol"]), t["symbol"], per_query, force)
        except NewsError as e:
            out[t["symbol"]] = []
            if on_error:
                on_error(str(e))
    if include_market:
        merged: list[dict] = []
        for q in MARKET_QUERIES:
            try:
                merged += fetch(conn, q, None, per_query, force)
            except NewsError as e:
                if on_error:
                    on_error(str(e))
        seen, uniq = set(), []
        for n in sorted(merged, key=lambda n: n.get("published_at") or "", reverse=True):
            if n["id"] not in seen:
                seen.add(n["id"])
                uniq.append(n)
        out[None] = uniq
    return out
