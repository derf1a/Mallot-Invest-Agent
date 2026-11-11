"""재료: DART 공시 분류 · 희석 · 자사주 · 점수, 뉴스 검색어 늘리기, 유명인 발언 · 소문, 가치평가 · 리스크 감시 · 비서 연결.
공시 · 기사는 모두 가짜 (실제 회사 공시 형식만 따랐다)."""

import json
from datetime import date, datetime

import pytest

from app.core.db import connect, init_db
from app.invest.analysis import dart, disclosures, valuation
from app.invest.debate import agents
from app.invest.marketdata import figures, news
from app.invest.marketdata.market import KST, SampleMarket


@pytest.fixture
def conn():
    init_db()
    c = connect()
    yield c
    c.close()


@pytest.mark.parametrize("name, kind, impact", [
    ("[기재정정]주요사항보고서(유상증자결정)", "rights", -2),
    ("주요사항보고서(자기주식취득신탁계약체결결정)", "buyback", 1),
    ("주요사항보고서(자기주식취득신탁계약해지결정)", "buyback_cancel", -1),
    ("주요사항보고서(전환사채권발행결정)", "cb", -1),
    ("전환가액의조정              (제8회차)", "cb_reprice", -1),
    ("풍문또는보도에대한해명(미확정)", "rumor_answer", 0),
    ("단일판매ㆍ공급계약체결(자율공시)", "contract", 1),
    ("연결재무제표기준영업(잠정)실적(공정공시)", "earnings", 0),
    ("반기또는분기매출액미달사실발생", "sales_short", -1),
    ("주식소각결정", "cancel_shares", 2),
    ("현금ㆍ현물배당결정", "dividend", 1),
    ("기타시장안내              (상장적격성 실질심사 사유 추가 관련 절차 미진행)", "delisting", -3),
    ("임원ㆍ주요주주특정증권등소유상황보고서", "insider", 0),
])
def test_classify_real_report_names(name, kind, impact):
    c = disclosures.classify(name)
    assert (c["kind"], c["impact"]) == (kind, impact)


def test_classify_unknown_and_corrections():
    assert disclosures.classify("현금ㆍ현물배당을위한주주명부폐쇄(기준일)결정") is None   # 배당 결정이 아니라 기준일 안내
    assert disclosures.is_correction("[첨부정정]주요사항보고서(전환사채권발행결정)")
    assert disclosures.base_name("[기재정정]주요사항보고서(유상증자결정)") == "주요사항보고서(유상증자결정)"


def test_parse_detail_and_rights_impact():
    r = disclosures.parse_detail("rights", {"nstk_ostk_cnt": "3,000,000", "bfic_tisstk_ostk": "10,000,000", "ic_mthn": "제3자배정증자"})
    assert r["dilution"] == 0.3 and disclosures.rights_impact(r) == -3                       # 30% 희석 → 큰 악재
    small = disclosures.parse_detail("rights", {"nstk_ostk_cnt": "500,000", "bfic_tisstk_ostk": "10,000,000", "ic_mthn": "제3자배정증자"})
    assert disclosures.rights_impact(small) == -1 and disclosures.rights_impact({**small, "method": "주주배정후 실권주 일반공모"}) == -2
    cb = disclosures.parse_detail("cb", {"cvisstk_cnt": "1,000,000", "cvisstk_tisstk_vs": "3.01", "bd_fta": "60,000,000,000", "cv_prc": "30,000"})
    assert cb["dilution"] == pytest.approx(0.0301) and cb["amount"] == 6e10
    bb = disclosures.parse_detail("buyback", {"aqpln_stk_ostk": "200,000", "aqpln_prc_ostk": "2,000,000,000", "aq_mth": "장내매수"})
    assert bb["shares"] == 200_000 and disclosures.parse_detail("cb", {"cvisstk_tisstk_vs": "-"})["dilution"] is None


LIST = [  # 최근 것부터 — 가짜 회사 '테스트전자'
    {"rcept_no": "20261110000001", "report_nm": "주요사항보고서(유상증자결정)", "rcept_dt": "20261110", "flr_nm": "테스트전자", "rm": "코"},
    {"rcept_no": "20261111000002", "report_nm": "[기재정정]주요사항보고서(유상증자결정)", "rcept_dt": "20261111", "flr_nm": "테스트전자", "rm": "코"},
    {"rcept_no": "20261105000003", "report_nm": "주요사항보고서(자기주식취득결정)", "rcept_dt": "20261105", "flr_nm": "테스트전자", "rm": ""},
    {"rcept_no": "20261101000004", "report_nm": "풍문또는보도에대한해명(미확정)", "rcept_dt": "20261101", "flr_nm": "테스트전자", "rm": "코"},
    {"rcept_no": "20260901000005", "report_nm": "단일판매ㆍ공급계약체결", "rcept_dt": "20260901", "flr_nm": "테스트전자", "rm": "코"},
    {"rcept_no": "20261109000006", "report_nm": "임원ㆍ주요주주특정증권등소유상황보고서", "rcept_dt": "20261109", "flr_nm": "홍길동", "rm": ""},
]
DETAIL = {
    # 실제 API 처럼: 원 결정일(11-10)로 찾히고, 가장 최근 정정(11-11)의 접수번호만 돌려준다
    "rights": [{"rcept_no": "20261111000002", "decided": "20261110", "nstk_ostk_cnt": "1,000,000", "bfic_tisstk_ostk": "10,000,000",
                "ic_mthn": "주주배정증자"}],
    "buyback": [{"rcept_no": "20261105000003", "aqpln_stk_ostk": "100,000", "aqpln_prc_ostk": "1,000,000,000"}],
}


@pytest.fixture
def fake_dart(conn, monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "k" * 40)
    conn.execute("INSERT INTO invest_dart_corps (symbol, corp_code, corp_name, refreshed_at) VALUES ('123450', '00999999', '테스트전자', '2026-11-01')")
    conn.commit()
    calls = []
    monkeypatch.setattr(dart, "disclosure_list", lambda code, bgn, end, max_pages=3: calls.append(("list", code, bgn, end)) or list(LIST))
    monkeypatch.setattr(dart, "decision_detail", lambda kind, code, bgn, end: calls.append((kind, bgn, end)) or
                        [r for r in DETAIL.get(kind, []) if bgn <= r.get("decided", r["rcept_no"][:8]) <= end])
    return calls


def test_refresh_and_summary(conn, fake_dart):
    now = datetime(2026, 11, 11, 10, 0)
    out = disclosures.refresh(conn, "123450", now)
    assert out == {"ok": True, "saved": 6, "reason": ""}
    assert fake_dart[0] == ("list", "00999999", "20260813", "20261111")
    assert disclosures.refresh(conn, "123450", now.replace(minute=20)) == {"ok": True, "saved": 0, "reason": "최근에 받았어요"}  # TTL
    assert len([c for c in fake_dart if c[0] == "list"]) == 1
    s = disclosures.summary(conn, "123450", shares=10_000_000, today=date(2026, 11, 11))
    # 유상증자 -2 (정정은 원 공시가 있어 세지 않음) + 자사주 +1 + 해명 0 + 공급계약(70일 전) +1×0.5 = -0.5
    assert s["score"] == -0.5 and s["count"] == 6
    assert s["dilution"]["pct"] == 0.1 and s["buyback"] == {"shares": 100000.0, "amount": 1e9, "pct": 0.01}
    assert s["flags"] == ["유상증자", "풍문 · 보도 해명"] and s["rumors"][0]["date"] == "2026-11-01"
    assert [x["label"] for x in s["items"]] == ["유상증자", "자사주 매입", "풍문 · 보도 해명", "공급계약 · 수주"]  # 정정 · 내부자 보고는 빼고
    assert s["items"][0]["url"].endswith("rcpNo=20261110000001")


def test_refresh_without_key_or_corp(conn, monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    assert disclosures.refresh(conn, "005930")["ok"] is False
    monkeypatch.setenv("DART_API_KEY", "k" * 40)
    assert "고유번호" in disclosures.refresh(conn, "005930")["reason"]
    assert disclosures.summary(conn, "005930")["score"] == 0


def test_valuation_report_carries_events(conn, fake_dart, monkeypatch):
    from app.invest.marketdata import market
    monkeypatch.setattr(market, "now_kst", lambda: datetime(2026, 11, 11, 10, 0, tzinfo=KST))
    monkeypatch.setattr(valuation, "evaluate", lambda c, p, syms, index=None: {"123450": {
        "score": 70, "grade": "B", "coverage": 1.0, "reason": "", "pillars": {}, "items": [], "missing": [], "peer": None, "basis": {},
        "indicators": {"market_cap": 1e11, "fair_value": 1.32e11}}})
    monkeypatch.setattr(valuation, "sector_index", lambda p: {"123450": {"sector": "IT", "name": "테스트전자"}})
    r = valuation.report(conn, SampleMarket(), "123450")
    assert r["events"]["disclosures"]["score"] == -0.5 and r["events"]["people"] == []
    d = r["fair_value_diluted"]
    assert d["dilution"] == 0.1 and d["fair_value"] == pytest.approx(1.2e11) and d["upside"] == pytest.approx(0.2)


def test_symbol_queries_and_fetch_symbol(conn, monkeypatch):
    seen = []
    rss = ('<?xml version="1.0"?><rss><channel><item><title>테스트전자, 대형 수주 - 한경</title>'
           '<link>https://news.google.com/a{n}</link><source>한경</source><pubDate>Tue, 10 Nov 2026 01:00:00 GMT</pubDate></item>'
           '</channel></rss>')

    def http(method, url, headers=None, data=None, timeout=15):
        seen.append(url)
        if len(seen) == 1:
            return 500, b""                                                              # 첫 검색어는 실패
        return 200, rss.replace("{n}", str(len(seen))).encode()
    monkeypatch.setattr(news, "HTTP", http)
    assert news.symbol_queries("테스트전자") == ["테스트전자 주가", "테스트전자 실적 OR 수주 OR 계약 OR 공시"]
    errors = []
    items = news.fetch_symbol(conn, "123450", "테스트전자", on_error=errors.append)
    assert [n["title"] for n in items] == ["테스트전자, 대형 수주"] and len(errors) == 1   # 하나가 실패해도 나머지는 쓴다


FIG_RSS = """<?xml version="1.0"?><rss><channel>
<item><title>젠슨 황 "HBM 수요 더 늘 것" 발언 - 연합</title><link>https://news.google.com/f1</link><source>연합</source>
<pubDate>Wed, 11 Nov 2026 00:00:00 GMT</pubDate></item>
<item><title>젠슨 황 불륜설 확산…엔비디아 "사실무근" - 매경</title><link>https://news.google.com/f2</link><source>매경</source>
<pubDate>Tue, 10 Nov 2026 22:00:00 GMT</pubDate></item>
<item><title>젠슨 황, 삼성전자와 협력 루머 - 뉴스1</title><link>https://news.google.com/f3</link><source>뉴스1</source>
<pubDate>Tue, 10 Nov 2026 20:00:00 GMT</pubDate></item>
<item><title>오래된 젠슨 황 기사 - 옛날</title><link>https://news.google.com/f4</link><source>옛날</source>
<pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_figure_tags_and_related(conn, monkeypatch):
    assert figures.tag("젠슨 황 불륜설 확산") == "scandal" and figures.tag("A사 매각설") == "rumor"
    assert figures.tag("머스크, X에 게시") == "statement" and figures.tag("테슬라 신차 출시") == "news"
    assert figures.tag("반도체 설비 투자") == "news"                                      # '설비'의 '설'은 소문이 아니다
    figures.save_figures(conn, [{"name": "젠슨 황", "aliases": ["젠슨황"], "role": "엔비디아 CEO", "symbols": ["000660"]}])
    urls = []
    monkeypatch.setattr(news, "HTTP", lambda m, url, **kw: urls.append(url) or (200, FIG_RSS.encode()))
    [f] = figures.collect(conn, names={"005930": "삼성전자"}, now=datetime(2026, 11, 11, 12, 0))
    assert "%22%EC%A0%A0%EC%8A%A8+%ED%99%A9%22" in urls[0] or "%22%EC%A0%A0%EC%8A%A8%20%ED%99%A9%22" in urls[0]  # "젠슨 황" 그대로 검색
    assert [(i["kind"], i["related"]) for i in f["items"]] == [("statement", ["000660"]), ("scandal", ["000660"]),
                                                               ("rumor", ["000660", "005930"])]   # 오래된 기사는 뺀다
    assert news.recent(conn, market=True) == []                                        # 시장 뉴스와 섞이지 않는다
    hit = figures.for_symbol(conn, "005930", "삼성전자")
    assert [(p["name"], [i["kind"] for i in p["items"]]) for p in hit] == [("젠슨 황", ["rumor"])]


def test_figure_list_editing(conn):
    assert [f["name"] for f in figures.get_figures(conn)][:2] == ["젠슨 황", "일론 머스크"]     # 처음엔 기본 목록
    items = figures.upsert(conn, {"name": "보이지 않는 손", "role": "", "symbols": ["005930", "bad"]})
    assert items[-1] == {"name": "보이지 않는 손", "aliases": [], "role": "", "topics": [], "symbols": ["005930"]}
    figures.remove(conn, "워런 버핏")
    assert "워런 버핏" not in [f["name"] for f in figures.get_figures(conn)]
    with pytest.raises(figures.FigureError):
        figures.remove(conn, "없는 사람")


def test_saved_roster_gets_new_tools_once(conn):
    old = [dict(a, tools=[t for t in a["tools"] if t not in ("get_disclosures", "get_figure_news")]) for a in agents.DEFAULT_ROSTER]
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (agents.ROSTER_KEY, json.dumps(old, ensure_ascii=False)))
    conn.commit()
    roster = {a["key"]: a["tools"] for a in agents.get_roster(conn)}
    assert "get_disclosures" in roster["value"] and "get_figure_news" in roster["news"] and "get_disclosures" in roster["risk"]
    saved = agents.get_roster(conn)
    news_agent = next(a for a in saved if a["key"] == "news")
    news_agent["tools"].remove("get_figure_news")                                       # 관리자가 빼면
    agents.save_roster(conn, saved)
    assert "get_figure_news" not in next(a for a in agents.get_roster(conn) if a["key"] == "news")["tools"]   # 다시 넣지 않는다


def test_risk_watch_alerts_on_bad_disclosure(conn, fake_dart, monkeypatch):
    from app.invest.ops import desk_agents
    now = datetime(2026, 11, 11, 10, 0, tzinfo=KST)
    out = desk_agents.disclosure_alerts(conn, {"123450": "테스트전자"}, now)
    assert [(a["kind"], a["disclosure"]["label"]) for a in out] == [("disclosure", "유상증자")]   # 오늘 정정 공시 (-2)
    assert desk_agents._alert_line(out[0]).startswith("테스트전자(123450) 공시 · 유상증자")
    assert desk_agents.disclosure_alerts(conn, {"123450": "테스트전자"}, now.replace(day=12)) == []   # 다음 날엔 새 공시가 없으면 조용


def test_assistant_adds_figure_by_chat(client, broker, monkeypatch):
    from app.invest.advice import assistant
    from tests.test_invest_assistant import Fake, say, thread
    fake = Fake([("update_figure", {"name": "보이지 않는 손", "role": "국내 개인 투자 인플루언서"}),
                 ("update_figure", {"name": "워런 버핏", "remove": True}),
                 ("update_figure", {"name": ""}),
                 ("get_figures", {})], reply="지켜볼게요")
    monkeypatch.setattr(assistant, "LLM", fake)
    say(client, "보이지 않는 손도 지켜봐 줘, 버핏은 빼고")
    assert fake.results[0]["ok"] and fake.results[1] == {"ok": True, "removed": "워런 버핏"} and fake.results[2]["ok"] is False
    assert "보이지 않는 손" in [f["name"] for f in fake.results[3]["figures"]]
    events = [m["text"] for m in thread(client) if m["role"] == "event"]
    assert "설정 변경 · 지켜볼 인물: 없음 → 보이지 않는 손(국내 개인 투자 인플루언서)" in events
    assert "설정 변경 · 지켜볼 인물: 워런 버핏 → 뺌" in events


def test_detail_retried_when_missing_and_unknown_dilution(conn, fake_dart, monkeypatch):
    real = dict(DETAIL)
    DETAIL.clear()                                                                       # 당일이라 상세가 아직 없음
    try:
        disclosures.refresh(conn, "123450", datetime(2026, 11, 11, 10, 0))
        s = disclosures.summary(conn, "123450", today=date(2026, 11, 11))
        assert s["dilution"]["pct"] is None and s["dilution"]["unknown"] == ["2026-11-10 유상증자"]   # 0 이 아니라 모름
        DETAIL.update(real)
        disclosures.refresh(conn, "123450", datetime(2026, 11, 11, 11, 0))              # TTL 지나 다시 — 상세를 채운다
        assert disclosures.summary(conn, "123450", today=date(2026, 11, 11))["dilution"]["pct"] == 0.1
    finally:
        DETAIL.clear()
        DETAIL.update(real)


def test_key_person_words_are_scandal():
    assert figures.tag('"젠슨황 연인, 엔비디아칩 中 밀반입"') == "scandal"
    assert figures.tag("이사회, 젠슨 황 내치나") == "scandal" and figures.tag("CEO 사임 발표") == "scandal"
