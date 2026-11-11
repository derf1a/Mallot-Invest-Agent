"""유명인 발언 · 소문 — 시장을 움직이는 인물(CEO · 정책 결정자 · 유명 투자자)의 말과 그를 둘러싼 소문을 뉴스로 모은다.

- 왜 뉴스로: X · 커뮤니티 원글은 공식 API 가 유료이거나 없다. 시장을 움직일 만한 발언 · 소문은 대개 몇 분~몇 시간 안에 기사가 되므로
  Google 뉴스 RSS(news.fetch, 15분 캐시)로 그 인물 이름을 검색한다. 원글이 아니라 '보도된 것'이라는 점을 결과에 밝힌다.
- 인물 목록(settings invest_figures): 이름 · 다른 표기 · 역할 · 관련 주제 · 관련 종목. 처음에는 DEFAULT, 화면 · 비서(채팅)가 더하고 뺀다.
- 분류(tag, 코드 정규식): scandal(사생활 · 수사 · 해임 · 퇴진 등 '핵심 인물 위험') > rumor(확인 안 된 소문 · 의혹) > statement(발언 · 전망 · 게시) > news.
  소문은 사실로 다루지 않는다 — 공시(조회공시 · 해명)나 회사 발표로 확인될 때까지 '소문' 표시를 붙여 넘긴다.
- 연결(related): 인물에 걸어 둔 종목 + 제목에 이름이 나온 종목(names 로 받음).
기사는 invest_news 에 '@이름' 링크로 쌓여 시장 뉴스(링크 '')와 섞이지 않는다.
"""

import json
import re
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta

from app.invest.marketdata import news

S_FIGURES = "invest_figures"
MAX_FIGURES = 20
PER_FIGURE = 8
DAYS = 3
DEFAULT_FIGURES: list[dict] = [
    {"name": "젠슨 황", "aliases": ["젠슨황", "Jensen Huang"], "role": "엔비디아 CEO", "topics": ["반도체", "AI"],
     "symbols": ["000660", "005930", "042700"]},
    {"name": "일론 머스크", "aliases": ["머스크", "Elon Musk"], "role": "테슬라 · 스페이스X · xAI CEO", "topics": ["전기차", "2차전지", "로봇", "우주"],
     "symbols": ["373220", "006400"]},
    {"name": "도널드 트럼프", "aliases": ["트럼프"], "role": "미국 대통령", "topics": ["관세", "무역", "환율"], "symbols": []},
    {"name": "연준 의장", "aliases": ["FOMC"], "role": "미국 기준금리 결정", "topics": ["금리", "달러"], "symbols": []},
    {"name": "샘 올트먼", "aliases": ["올트먼", "Sam Altman"], "role": "오픈AI CEO", "topics": ["AI", "데이터센터"], "symbols": ["000660"]},
    {"name": "워런 버핏", "aliases": ["버핏", "Warren Buffett"], "role": "버크셔 해서웨이 · 가치투자", "topics": ["가치투자"], "symbols": []},
    {"name": "캐시 우드", "aliases": ["Cathie Wood"], "role": "ARK 인베스트 · 성장주", "topics": ["성장주", "혁신기술"], "symbols": []},
]
KIND_KO = {"scandal": "개인 스캔들 · 수사", "rumor": "확인 안 된 소문", "statement": "발언", "news": "관련 기사"}
SCANDAL = re.compile(r"불륜|내연|연인|사생활|스캔들|열애|이혼|폭로|건강\s?이상|사망설|체포|기소|구속|수사|소환|성추문|밀반입|밀수"
                     r"|해임|경질|퇴진|사임|축출|내치")
RUMOR = re.compile(r"썰|루머|의혹|찌라시|풍문|카더라|소문|[가-힣]설(?=\s|$|[,.…'\"!?)\]])")
STATEMENT = re.compile(r"발언|말했|밝혔|언급|강조|경고|전망|예상|인터뷰|트윗|SNS|엑스\(X\)|X에|게시|올렸|연설|키노트|기조연설|서한|주주서한|공개석상")
_SYMBOL = re.compile(r"^\d{6}$")


class FigureError(Exception):
    pass


def _clean(f: dict) -> dict | None:
    name = str(f.get("name") or "").strip()[:30]
    if not name:
        return None
    strs = lambda v, n, m: [str(x).strip()[:m] for x in (v or []) if str(x).strip()][:n]  # noqa: E731
    return {"name": name, "aliases": strs(f.get("aliases"), 4, 30), "role": str(f.get("role") or "").strip()[:60],
            "topics": strs(f.get("topics"), 6, 20), "symbols": [s for s in strs(f.get("symbols"), 8, 6) if _SYMBOL.match(s)]}


def get_figures(conn: sqlite3.Connection) -> list[dict]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (S_FIGURES,)).fetchone()
    try:
        raw = json.loads(row["value"]) if row else None
    except ValueError:
        raw = None
    if not isinstance(raw, list):
        return [dict(f) for f in DEFAULT_FIGURES]
    return [c for f in raw if isinstance(f, dict) and (c := _clean(f))]


def save_figures(conn: sqlite3.Connection, figures: list[dict]) -> list[dict]:
    clean = [c for f in figures if (c := _clean(f))]
    names = [f["name"] for f in clean]
    if len(set(names)) != len(names):
        raise FigureError("같은 이름이 두 번 있어요")
    if len(clean) > MAX_FIGURES:
        raise FigureError(f"인물은 {MAX_FIGURES}명까지예요")
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (S_FIGURES, json.dumps(clean, ensure_ascii=False)))
    conn.commit()
    return clean


def upsert(conn: sqlite3.Connection, figure: dict) -> list[dict]:
    """같은 이름이면 고치고, 없으면 더한다."""
    c = _clean(figure)
    if c is None:
        raise FigureError("이름이 필요해요")
    items = [f for f in get_figures(conn) if f["name"] != c["name"]]
    return save_figures(conn, [*items, c])


def remove(conn: sqlite3.Connection, name: str) -> list[dict]:
    items = get_figures(conn)
    left = [f for f in items if f["name"] != name.strip()]
    if len(left) == len(items):
        raise FigureError(f"목록에 없는 인물이에요: {name}")
    return save_figures(conn, left)


def query(f: dict) -> str:
    """'"젠슨 황" OR "젠슨황"' — 이름과 다른 표기 하나."""
    return " OR ".join(f'"{x}"' for x in [f["name"], *f["aliases"][:1]])


def tag(text: str) -> str:
    for kind, rx in (("scandal", SCANDAL), ("rumor", RUMOR), ("statement", STATEMENT)):
        if rx.search(text):
            return kind
    return "news"


def _recent_enough(n: dict, since: str) -> bool:
    return (n.get("published_at") or n.get("fetched_at") or "")[:16] >= since


def collect(conn: sqlite3.Connection, *, names: dict[str, str] | None = None, only: list[str] | None = None, days: int = DAYS,
            per: int = PER_FIGURE, force: bool = False, now: datetime | None = None,
            on_error: Callable[[str], None] | None = None) -> list[dict]:
    """인물마다 최근 days 일 기사 → [{name, role, topics, symbols, items: [{title, source, published_at, url, kind, kind_ko, related}]}].
    only 로 인물을 고르고, names({종목: 이름})가 있으면 제목에 그 이름이 나온 종목을 related 에 더한다."""
    since = ((now or datetime.now()) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    out = []
    for f in get_figures(conn):
        if only is not None and f["name"] not in only:
            continue
        try:
            rows = news.fetch(conn, query(f), f"@{f['name']}", per, force)
        except news.NewsError as e:
            if on_error:
                on_error(f"{f['name']}: {e}")
            rows = []
        items = []
        for n in rows:
            if not _recent_enough(n, since):
                continue
            text = f"{n['title']} {n.get('snippet') or ''}"
            kind = tag(text)
            related = list(dict.fromkeys([*f["symbols"], *[s for s, nm in (names or {}).items() if nm and len(nm) >= 2 and nm in n["title"]]]))
            items.append({"title": n["title"], "source": n["source"], "published_at": n["published_at"], "url": n["url"],
                          "kind": kind, "kind_ko": KIND_KO[kind], "related": related})
        out.append({**f, "items": items})
    return out


def for_symbol(conn: sqlite3.Connection, symbol: str, name: str, *, days: int = DAYS, force: bool = False) -> list[dict]:
    """한 종목에 닿는 인물 기사 — 그 종목을 걸어 둔 인물만 새로 묻고, 다른 인물은 이미 받은 기사에서 제목에 종목 이름이 나온 것만."""
    linked = [f["name"] for f in get_figures(conn) if symbol in f["symbols"]]
    out = []
    for f in collect(conn, names={symbol: name}, only=linked, days=days, force=force):
        hits = [x for x in f["items"] if symbol in x["related"]]
        if hits:
            out.append({"name": f["name"], "role": f["role"], "items": hits[:5]})
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    for f in get_figures(conn):
        if f["name"] in linked or not name or len(name) < 2:
            continue
        rows = [n for n in news.recent(conn, symbols=[f"@{f['name']}"], limit=40) if name in n["title"] and _recent_enough(n, since)]
        if rows:
            out.append({"name": f["name"], "role": f["role"], "items": [
                {"title": n["title"], "source": n["source"], "published_at": n["published_at"], "url": n["url"],
                 "kind": (k := tag(f"{n['title']} {n.get('snippet') or ''}")), "kind_ko": KIND_KO[k], "related": [symbol]} for n in rows[:5]]})
    return out
