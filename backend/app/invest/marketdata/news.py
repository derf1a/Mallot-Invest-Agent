"""투자 뉴스 수집 — Google 뉴스 RSS 검색(공개 피드)으로 종목별·시장 뉴스를 모아 invest_news 에 쌓는다.

- 종목: "<종목명> 주가" + "<종목명> 실적 OR 수주 OR 계약 OR 공시"(EVENT_TERMS — '주가'가 없는 실적 · 계약 기사도 걸리게) /
  시장: "코스피 증시", "미국 증시". 유명인 발언은 figures.py 가 같은 fetch 로 받는다.
- 이름이 지명·흔한 낱말과 같은 종목(PLACE_NAMES · GENERIC_NAMES)은 종목코드 검색어를 더하고, 지역 · 다른 회사 기사를
  relevant() 로 거른다 (예: 보령 → '보령시 수산기업 대보㈜' 기사).
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


EVENT_TERMS = ("실적", "수주", "계약", "공시")


# 이름이 지명과 같은 종목 — 그 지역 기사가 섞인다 (보령 → 보령시 · 보령산 해삼 · 보령 수산기업 대보㈜)
PLACE_NAMES = frozenset({"보령", "서산", "진도", "영광", "풍산", "대덕", "율촌", "영흥"})
# 이름이 흔한 낱말인 짧은 종목 — 주식과 상관없는 기사가 많이 걸린다
GENERIC_NAMES = frozenset({"대상", "동방", "동양", "태양", "나노", "노을", "레몬", "레이", "캐리", "핑거", "배럴", "전방", "삼일",
                           "화신", "선진", "우성", "신흥"})
# 헷갈리는 이름의 기사는 제목 · 요약에 이 말 중 하나가 있어야 종목(회사) 기사로 본다 — 종목코드 검색어로 온 기사는 빼고
FINANCE_TERMS = ("주가", "주식", "증시", "증권", "코스피", "코스닥", "상장", "시총", "시가총액", "실적", "매출", "영업이익", "영업익",
                 "순이익", "흑자", "적자", "수익", "공시", "배당", "자사주", "목표가", "투자", "투입", "수주", "계약", "공급", "인수",
                 "합병", "분할", "주주", "지분", "회사채", "대표", "경영", "사업", "생산", "증설", "공장", "수출", "출시", "허가",
                 "인증", "임상", "신약", "제약", "유통", "금융", "채권", "오버행")
# 지명 뒤에 붙는 말 — '보령시', '보령시의', '보령산', '보령항' 은 회사가 아니라 그 지역 이야기다
_PLACE_SUFFIX = r"(?:시청|시장|시민|시의회|군청|해수욕장|지역|시|군|구|읍|면|산|항)(?:[은는이가의에와과도를을로]|에서)?(?![가-힣])"
_CORP_MARK = r"(?:㈜|\(주\))"


def ambiguous(name: str) -> bool:
    return name in PLACE_NAMES or name in GENERIC_NAMES


def symbol_query(name: str) -> str:
    return f"{name} 주가"


def symbol_queries(name: str, symbol: str | None = None) -> list[str]:
    """종목 하나의 검색어들 — 주가 기사 + 실적 · 수주 · 계약 · 공시 기사 (Google 뉴스 검색은 OR 를 받는다).
    헷갈리는 이름이면 지명 기사를 빼는 검색어(-보령시)와 종목코드 검색어를 쓴다."""
    events = f"{name} " + " OR ".join(EVENT_TERMS)
    if not ambiguous(name):
        return [symbol_query(name), events]
    if name in PLACE_NAMES:
        events += f" -{name}시 -{name}군"
    return [symbol_query(name), events] + ([f"{name} {symbol}"] if symbol else [])


def relevant(name: str, symbol: str | None, item: dict, by_code: bool = False) -> bool:
    """이 기사가 그 종목 이야기인가 — 헷갈리는 이름(ambiguous)만 거르고 나머지는 모두 통과.
    by_code: 종목코드 검색어로 온 기사 — 본문에 코드가 있다는 뜻이라 지역 · 다른 회사 표시만 본다."""
    if not ambiguous(name):
        return True
    title = item.get("title") or ""
    text = f"{title} {item.get('snippet') or ''}"
    if symbol and symbol in text:
        return True  # 종목코드가 나오면 종목 기사
    if name not in title:
        return False
    n = re.escape(name)
    if name in PLACE_NAMES and re.search(n + _PLACE_SUFFIX, title):
        return False
    if re.search(_CORP_MARK, title) and not re.search(rf"{_CORP_MARK}\s*{n}|{n}\s*{_CORP_MARK}", title):
        return False  # 제목의 ㈜ · (주)가 다른 회사에 붙어 있다 — '보령 대보㈜ ...'
    return by_code or any(t in text for t in FINANCE_TERMS)


def _merge(groups: list[list[dict]], limit: int) -> list[dict]:
    seen, out = set(), []
    for n in sorted((n for g in groups for n in g), key=lambda n: n.get("published_at") or "", reverse=True):
        if n["id"] not in seen:
            seen.add(n["id"])
            out.append(n)
    return out[:limit]


def fetch_symbol(conn: sqlite3.Connection, symbol: str, name: str, limit: int = 15, force: bool = False,
                 on_error: Callable[[str], None] | None = None) -> list[dict]:
    """종목 검색어들(symbol_queries)을 모두 받아 최신순으로 합친다. 하나가 실패해도 나머지는 쓴다 — 모두 실패하면 NewsError."""
    name = name or symbol
    groups, errors = [], []
    for q in symbol_queries(name, symbol):
        try:
            by_code = bool(symbol) and q.endswith(f" {symbol}")
            groups.append(fetch(conn, q, symbol, limit, force, keep=lambda n, c=by_code: relevant(name, symbol, n, c)))
        except NewsError as e:
            errors.append(str(e))
            if on_error:
                on_error(str(e))
    if not groups and errors:
        raise NewsError(errors[0])
    return _merge(groups, limit)


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
          force: bool = False, keep: Callable[[dict], bool] | None = None) -> list[dict]:
    """질의 하나를 받아 저장하고, 그 질의의 최근 기사들을 돌려준다. keep 이 있으면 통과한 기사만 저장 · 반환한다."""
    since = (datetime.now() - timedelta(minutes=CACHE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    fresh = conn.execute("SELECT 1 FROM invest_news WHERE query = ? AND fetched_at >= ? LIMIT 1",
                         (query, since)).fetchone()
    if force or not fresh:
        status, body = HTTP("GET", RSS_URL.format(q=urllib.parse.quote(query)), headers={"Accept": "application/rss+xml"})
        if status != 200:
            raise NewsError(f"뉴스를 받지 못했습니다 (HTTP {status or '연결 실패'}): {query}")
        for item in [n for n in parse_rss(body) if keep is None or keep(n)][:limit]:
            url_hash = hashlib.sha256(item["url"].encode()).hexdigest()
            conn.execute(
                "INSERT INTO invest_news (url_hash, url, title, source, published_at, snippet, query) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(url_hash) DO UPDATE SET "
                "fetched_at = datetime('now', 'localtime'), query = excluded.query",
                (url_hash, item["url"], item["title"], item["source"], item["published_at"], item["snippet"], query))
            news_id = conn.execute("SELECT id FROM invest_news WHERE url_hash = ?", (url_hash,)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO invest_news_links (news_id, symbol) VALUES (?, ?)", (news_id, symbol or ""))
        conn.commit()
    rows = recent(conn, query=query, limit=limit)
    return [n for n in rows if keep(n)] if keep else rows  # 규칙 전에 저장된 기사도 거른다


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
            out[t["symbol"]] = fetch_symbol(conn, t["symbol"], t["name"] or t["symbol"], per_query, force)
        except NewsError:
            out[t["symbol"]] = []  # 검색어마다 on_error 로 이미 알렸다
    if include_market:
        merged: list[dict] = []
        for q in MARKET_QUERIES:
            try:
                merged += fetch(conn, q, None, per_query, force)
            except NewsError as e:
                if on_error:
                    on_error(str(e))
        out[None] = _merge([merged], len(merged))
    return out
