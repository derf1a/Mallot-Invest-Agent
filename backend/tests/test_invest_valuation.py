"""가치평가·판단 기록: OpenDART 응답 해석, 최근 12개월 계산, 구간 점수·S-RIM(손으로 계산한 값), 동기화, 종목 점수 그룹·시장 국면 가중치,
발굴의 보유 제외·섹터 한도·급등 건너뛰기, 섹터 전체 평가, 기록·API."""

import asyncio
import io
import json
import zipfile
from datetime import date, datetime

import pytest

from app.invest.analysis import dart, scorelog, screening, valuation
from app.invest.debate import agents
from app.invest.marketdata import market
from app.invest.marketdata.market import SampleMarket
from app.invest.ops import desk_agents
from tests.test_invest import at_open  # noqa: F401 — 장중 시각 고정 fixture
from tests.test_invest_autopilot import conn, pilot  # noqa: F401 — fixture


def row(symbol, account, *, fs="CFS", year=2025, reprt="11011", cur=None, cur_add=None, prev=None, prev_add=None, prev2=None, currency="KRW"):
    return {"stock_code": symbol, "account_nm": account, "fs_div": fs, "bsns_year": str(year), "reprt_code": reprt, "currency": currency,
            "rcept_no": "20260331000123", "thstrm_dt": "2025.12.31", "thstrm_amount": cur, "thstrm_add_amount": cur_add, "frmtrm_amount": prev,
            "frmtrm_add_amount": prev_add, "bfefrmtrm_amount": prev2}


ANNUAL = [row("005930", "매출액", cur="1,000", prev="800"), row("005930", "영업이익", cur="200", prev="100"),
          row("005930", "당기순이익(손실)", cur="120", prev="100", prev2="80"), row("005930", "자본총계", cur="1,000", prev="900", prev2="800"),
          row("005930", "부채총계", cur="500"), row("005930", "자산총계", cur="1,500"), row("005930", "유동자산", cur="600"), row("005930", "유동부채", cur="300"),
          row("005930", "매출액", fs="OFS", cur="1"), row("000660", "매출액", fs="OFS", cur="50"), row("000660", "기타포괄손익", fs="OFS", cur="9")]


def annual_report() -> dict:
    return valuation.parse_rows(ANNUAL)["005930"]


# ---- OpenDART ----
def test_corp_codes_unzips_and_keeps_listed_only(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "k" * 40)
    xml = ("<result><list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><stock_code>005930</stock_code><modify_date>20261001</modify_date></list>"
           "<list><corp_code>00000001</corp_code><corp_name>비상장</corp_name><stock_code> </stock_code><modify_date>20261001</modify_date></list></result>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CORPCODE.xml", xml)
    monkeypatch.setattr(dart, "HTTP", lambda url, timeout: (200, buf.getvalue()))
    assert dart.corp_codes() == [{"symbol": "005930", "corp_code": "00126380", "corp_name": "삼성전자", "modify_date": "20261001"}]
    monkeypatch.setattr(dart, "HTTP", lambda url, timeout: (200, "<result><status>010</status><message>등록되지 않은 키입니다.</message></result>".encode()))
    with pytest.raises(dart.DartError) as e:
        dart.corp_codes()
    assert e.value.code == "010" and e.value.fatal


def test_dart_errors_never_show_the_key(monkeypatch):
    secret = "s3cret" * 6
    monkeypatch.setenv("DART_API_KEY", secret)
    seen = []

    def http(url, timeout):
        seen.append(url)
        return 200, json.dumps({"status": "020", "message": "요청 제한을 초과하였습니다."}).encode()

    monkeypatch.setattr(dart, "HTTP", http)
    with pytest.raises(dart.DartError) as e:
        dart.multi_accounts(["00126380", "00164779"], 2025, "11011")
    assert secret in seen[0] and "corp_code=00126380%2C00164779" in seen[0] and "reprt_code=11011" in seen[0]
    assert secret not in str(e.value) and e.value.fatal
    monkeypatch.setattr(dart, "HTTP", lambda url, timeout: (200, b'{"status": "013", "message": "no data"}'))
    assert dart.multi_accounts(["00126380"], 2025, "11011") == []
    monkeypatch.setattr(dart, "HTTP", lambda url, timeout: (500, b""))
    with pytest.raises(dart.DartError, match="HTTP 500"):
        dart.multi_accounts(["00126380"], 2025, "11011")


def test_no_key_means_no_calls(conn, monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    monkeypatch.setattr(dart, "HTTP", lambda *a: pytest.fail("키 없이 호출"))
    out = valuation.refresh(conn)
    assert out["ok"] is False and "DART_API_KEY" in out["reason"]


def test_parse_prefers_consolidated_and_handles_numbers():
    reps = valuation.parse_rows(ANNUAL)
    a = reps["005930"]
    assert a["fs_div"] == "CFS" and a["accounts"]["revenue"]["cur"] == 1000.0 and a["accounts"]["net_income"]["prev2"] == 80.0
    assert reps["000660"]["fs_div"] == "OFS" and list(reps["000660"]["accounts"]) == ["revenue"]        # 모르는 계정은 버린다
    assert valuation._amount("-1,234") == -1234.0 and valuation._amount("-") is None and valuation._amount(None) is None


def test_reports_due_follows_the_calendar():
    assert valuation.reports_due(date(2026, 3, 1)) == [(2025, "11011"), (2025, "11014"), (2025, "11012"), (2025, "11013"), (2024, "11011")]
    assert valuation.reports_due(date(2026, 11, 2))[:4] == [(2026, "11014"), (2026, "11012"), (2026, "11013"), (2025, "11011")]
    assert (2026, "11014") not in valuation.reports_due(date(2026, 8, 1)) and (2026, "11012") in valuation.reports_due(date(2026, 8, 1))


# ---- 최근 12개월 ----
def test_financials_annual_only():
    f = valuation.financials([annual_report()])
    assert (f["net_income"], f["equity"], f["ttm"]) == (120.0, 1000.0, False)
    assert (f["revenue_now"], f["revenue_prior"]) == (1000.0, 800.0)
    assert f["roe_history"] == [0.12, 100 / 900, 0.1]
    assert f["report"] == "2025 사업보고서"


def test_financials_rolls_interim_into_twelve_months():
    half = valuation.parse_rows([row("005930", "매출액", year=2026, reprt="11012", cur="300", cur_add="700", prev="250", prev_add="500"),
                                 row("005930", "영업이익", year=2026, reprt="11012", cur="50", cur_add="130", prev="40", prev_add="90"),
                                 row("005930", "당기순이익", year=2026, reprt="11012", cur="40", cur_add="90", prev="30", prev_add="60"),
                                 row("005930", "자본총계", year=2026, reprt="11012", cur="1,100", prev="1,000")])["005930"]
    first = valuation.parse_rows([row("005930", "매출액", year=2026, reprt="11013", cur="300", prev="200")])["005930"]
    f = valuation.financials([annual_report(), first, half])
    assert f["ttm"] and f["report"] == "2026 반기"                    # 가장 최근 분기 보고서
    assert f["revenue"] == 1000 + 700 - 500 and f["net_income"] == 120 + 90 - 60
    assert (f["revenue_now"], f["revenue_prior"]) == (700.0, 500.0)     # 성장률은 같은 기간끼리
    assert f["equity"] == 1100.0 and f["liabilities"] == 500.0          # 최신 값이 없으면 사업보고서
    assert f["roe_history"] == [150 / 1100, 0.12, 100 / 900]


def test_financials_needs_annual_and_won():
    assert valuation.financials([]) is None
    usd = valuation.parse_rows([row("005930", "매출액", cur="1", currency="USD")])["005930"]
    assert valuation.financials([usd]) is None


# ---- 평가 ----
def test_band_points_edges():
    assert valuation.band_points("per", 8) == 100 and valuation.band_points("per", 8.1) == 80 and valuation.band_points("per", 31) == 15
    assert valuation.band_points("roe", 0.2) == 100 and valuation.band_points("roe", -0.01) == 0
    assert valuation.band_points("upside", -0.3) == 35 and valuation.band_points("upside", -0.31) == 15


def test_fair_value_formula():
    f = valuation.fair_value(1000.0, [0.20, 0.10, None])
    roe = (0.20 * 3 + 0.10 * 2) / 5
    assert f["roe_used"] == pytest.approx(0.16)
    assert f["value"] == pytest.approx(1000 + 1000 * (roe - 0.10) * 0.9 / (1 + 0.10 - 0.9))
    assert f["value"] == pytest.approx(1270.0)                            # 1000 + 1000 × 0.06 × 0.9 ÷ 0.2
    assert valuation.fair_value(1000.0, [-0.5])["value"] == 0.0         # 음수는 0
    assert valuation.fair_value(0, [0.1]) is None and valuation.fair_value(100.0, [None]) is None


def test_assess_hand_calculated():
    """시가총액 2,400: PER 20(35)·PBR 2.4(35), ROE 12%(65)·영업이익률 20%(100), 매출 +25%(80)·영업이익 +100%(100), 부채 50%(100)·유동 200%(100)."""
    fin = valuation.financials([annual_report()])
    v = valuation.assess(fin, 2400.0)
    ind = v["indicators"]
    assert ind["per"] == 20 and ind["pbr"] == 2.4 and ind["roe"] == 0.12 and ind["op_margin"] == 0.2 and ind["debt_ratio"] == 0.5
    assert ind["rev_growth"] == 0.25 and ind["op_growth"] == 1.0 and ind["current_ratio"] == 2.0
    roe = (0.12 * 3 + 100 / 900 * 2 + 0.1) / 6
    fair = 1000 + 1000 * (roe - 0.1) * 0.9 / 0.2
    assert ind["fair_value"] == pytest.approx(fair) and ind["upside"] == pytest.approx(fair / 2400 - 1)
    pts = {x["key"]: x["points"] for x in v["items"]}
    assert (pts["per"], pts["pbr"], pts["roe"], pts["op_margin"], pts["rev_growth"], pts["op_growth"], pts["debt_ratio"], pts["current_ratio"]) == (35, 35, 65, 100, 80, 100, 100, 100)
    upside_pts = valuation.band_points("upside", ind["upside"])
    cheap = round((35 + 35 + upside_pts) / 3, 1)
    assert v["pillars"]["cheap"]["score"] == cheap and v["pillars"]["quality"]["score"] == 82.5
    assert v["pillars"]["growth"]["score"] == 90 and v["pillars"]["safety"]["score"] == 100
    assert v["score"] == round(cheap * 0.4 + 82.5 * 0.25 + 90 * 0.2 + 100 * 0.15, 1) and set(v["missing"]) == {"per_rel", "pbr_rel"}
    assert v["grade"] == valuation.grade(v["score"]) and v["coverage"] == round(9 / 11, 2)


def test_assess_peers_loss_financials_and_coverage():
    fin = valuation.financials([annual_report()])
    v = valuation.assess(fin, 2400.0, peer={"per": 40.0, "pbr": 2.4})
    pts = {x["key"]: x for x in v["items"]}
    assert pts["per_rel"]["value"] == 0.5 and pts["per_rel"]["points"] == 100 and pts["pbr_rel"]["points"] == 60 and v["missing"] == []
    loss = valuation.assess({**fin, "net_income": -50.0}, 2400.0)
    assert loss["indicators"]["per"] is None and loss["indicators"]["loss"]
    assert next(x for x in loss["items"] if x["key"] == "per")["note"] == "적자" and next(x for x in loss["items"] if x["key"] == "per")["points"] == 0
    bank = valuation.assess(fin, 2400.0, financial=True)
    assert not ({"op_margin", "debt_ratio", "current_ratio", "rev_growth", "op_growth"} & {x["key"] for x in bank["items"]}) and bank["financial"]
    turned = valuation.assess({**fin, "op_income_now": 10.0, "op_income_prior": -5.0}, 2400.0)
    assert next(x for x in turned["items"] if x["key"] == "op_growth")["points"] == valuation.TURNAROUND_POINTS
    thin = valuation.assess({"equity": None, "net_income": None}, None)
    assert thin["score"] is None and thin["grade"] is None and "항목" in thin["reason"]


def test_refresh_stores_and_skips_what_it_has(conn, monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "k" * 40)
    monkeypatch.setattr(valuation, "sleep", lambda s: None)
    monkeypatch.setattr(dart, "corp_codes", lambda: [{"symbol": "005930", "corp_code": "00126380", "corp_name": "삼성전자", "modify_date": ""},
                                                     {"symbol": "000660", "corp_code": "00164779", "corp_name": "SK하이닉스", "modify_date": ""}])
    calls = []

    def accounts(corps, year, code):
        calls.append((tuple(corps), year, code))
        return ANNUAL if (year, code) == (2025, "11011") and "00126380" in corps else []

    monkeypatch.setattr(dart, "multi_accounts", accounts)
    now = datetime(2026, 11, 2, 8, 20)
    out = valuation.refresh(conn, now=now)
    assert out["ok"] and out["saved"] == 2 and out["calls"] == len(valuation.reports_due(now.date()))
    assert valuation.status(conn)["symbols"] == 2 and valuation.status(conn)["synced_day"] == "2026-11-02"
    calls.clear()
    assert valuation.refresh(conn, now=now)["calls"] == 0 and calls == []                                 # 오늘 끝냄
    tomorrow = valuation.refresh(conn, now=datetime(2026, 11, 3, 8, 20))
    assert tomorrow["ok"] and ((("00126380", "00164779"), 2025, "11011") not in calls)                     # 있는 보고서는 다시 묻지 않는다


def test_refresh_stops_on_limit_and_keeps_what_it_got(conn, monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "k" * 40)
    monkeypatch.setattr(valuation, "sleep", lambda s: None)
    monkeypatch.setattr(dart, "corp_codes", lambda: [{"symbol": "005930", "corp_code": "00126380", "corp_name": "", "modify_date": ""}])
    seen = []

    def accounts(corps, year, code):
        seen.append((year, code))
        if len(seen) == 2:
            raise dart.DartError("OpenDART 오류 020: 요청 제한", "020")
        return []

    monkeypatch.setattr(dart, "multi_accounts", accounts)
    out = valuation.refresh(conn, now=datetime(2026, 11, 2, 8, 20))
    assert not out["ok"] and "020" in out["reason"] and len(seen) == 2
    st = valuation.state(conn)
    assert "synced_day" not in st and "020" in st["last_error"]                                            # 내일 다시


def fill(conn, symbol: str, equity: float, net: float) -> None:
    rep = valuation.parse_rows([row(symbol, "자본총계", cur=str(equity), prev=str(equity)), row(symbol, "당기순이익", cur=str(net), prev=str(net)),
                                row(symbol, "매출액", cur="1000", prev="900"), row(symbol, "영업이익", cur="100", prev="90"), row(symbol, "부채총계", cur="500"),
                                row(symbol, "유동자산", cur="500"), row(symbol, "유동부채", cur="250")])[symbol]
    valuation._save_report(conn, symbol, rep, "2026-11-02 08:00:00")
    conn.commit()


INDEX = {"000001": {"sector": "IT", "name": "싼회사", "market_cap": 60_000_000_000, "trading_amount": 500_000_000},
         "000002": {"sector": "IT", "name": "비싼회사", "market_cap": 900_000_000_000, "trading_amount": 500_000_000},
         "000003": {"sector": "IT", "name": "적자회사", "market_cap": 60_000_000_000, "trading_amount": 500_000_000},
         "000004": {"sector": "IT", "name": "작은회사", "market_cap": 10_000_000_000, "trading_amount": 500_000_000},
         "000005": {"sector": "IT", "name": "안 팔리는 회사", "market_cap": 60_000_000_000, "trading_amount": 1_000},
         "000006": {"sector": "금융", "name": "은행", "market_cap": 60_000_000_000, "trading_amount": None}}


def test_value_screen_filters_and_ranks(conn):
    for s, net in (("000001", 15e9), ("000002", 15e9), ("000003", -9e9), ("000004", 15e9), ("000005", 15e9), ("000006", 15e9)):
        fill(conn, s, 100e9, net)
    out = valuation.screen(conn, SampleMarket(), index=INDEX)
    names = [r["name"] for r in out["rows"]]
    assert "작은회사" not in names and "안 팔리는 회사" not in names and out["scored"] == 4 and out["universe"] == 6
    assert names.index("싼회사") < names.index("비싼회사")
    assert "적자회사" in names and "적자회사" not in [r["name"] for r in out["picks"]]                         # 기록엔 남고 발굴엔 안 간다
    assert next(r for r in out["rows"] if r["name"] == "은행")["valuation"]["financial"] is True               # 거래대금 없음은 통과
    held = valuation.screen(conn, SampleMarket(), index=INDEX, exclude={"000001"})
    assert "싼회사" not in [r["name"] for r in held["picks"]]


def test_report_explains_when_missing(conn, monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    out = valuation.report(conn, SampleMarket(), "005930")
    assert out["available"] is False and "DART_API_KEY" in out["reason"]
    fill(conn, "005930", 100e9, 15e9)
    monkeypatch.setattr(valuation, "sector_index", lambda p, notes=None: {"005930": {"sector": "IT", "name": "삼성전자", "market_cap": 120e9, "trading_amount": None}})
    got = valuation.report(conn, SampleMarket(), "005930")
    assert got["available"] and got["indicators"]["per"] == 8 and got["sector"] == "IT" and got["grade"] in "ABCDE"


# ---- 종목 점수 ----
BASE = {"trend": 1, "change_20d": 0.1, "change_5d": 0.02, "volume_ratio": 1.0, "rsi": 50, "change_1d": 0.0, "price": 100, "drawdown": -0.02, "uptrend": True}


def test_factors_group_and_add_up():
    f = screening.factors(BASE, ["관심"], {"score": 80.0, "grade": "A"}, "neutral")
    g = f["groups"]
    assert g["trend"]["raw"] == round(1 + 0.5 + 0.12, 3) and g["value"]["raw"] == 0.9 and g["source"]["raw"] == 0.3 and g["risk"]["raw"] == 0
    assert f["score"] == round(sum(x["points"] for x in g.values()), 2) == round(1.62 + 0.9 + 0.3, 2)
    assert {x["key"] for x in f["items"]} == {"trend", "m20", "m5", "value", "source:관심"}
    assert screening.factors(None, ["관심"]) is None


def test_regime_changes_weights():
    v = {"score": 80.0, "grade": "A"}
    on, off = screening.factors(BASE, [], v, "risk_on"), screening.factors(BASE, [], v, "risk_off")
    assert on["score"] == round(1.62 * 1.2 + 0.9 * 0.6, 2) and off["score"] == round(1.62 * 0.5 + 0.9 * 1.4, 2)
    assert on["score"] > off["score"] and off["groups"]["value"]["weight"] == 1.4
    cheap_flat, hot_pricey = {**BASE, "trend": 0, "change_20d": 0.0, "change_5d": 0.0}, {**BASE, "change_20d": 0.3, "change_5d": 0.15}
    cheap, pricey = {"score": 90.0, "grade": "A"}, {"score": 20.0, "grade": "E"}
    assert screening.score(cheap_flat, [], cheap, "risk_off") > screening.score(hot_pricey, [], pricey, "risk_off")   # 위험 회피면 싼 종목이 앞
    assert screening.score(cheap_flat, [], cheap, "risk_on") < screening.score(hot_pricey, [], pricey, "risk_on")     # 위험 선호면 뒤집힌다
    assert screening.factors(BASE, [], None, "unknown")["regime"] == "neutral"


def test_chase_and_pullback():
    ahn = {**BASE, "change_20d": 0.577, "change_5d": 0.514, "rsi": 69.6}
    f = screening.factors(ahn, ["거래대금 상위"])
    chase = next(x for x in f["items"] if x["key"] == "chase")
    assert chase["points"] == round(-(0.514 - 0.20) * 5, 3) == -1.57                                       # 오늘 안랩 사례
    assert screening.factors({**BASE, "change_5d": 0.9}, [])["groups"]["risk"]["raw"] == -2.0                # 상한
    dip = {**BASE, "drawdown": -0.1, "rsi": 45}
    assert next(x for x in screening.factors(dip, [])["items"] if x["key"] == "pullback")["points"] == 1.0
    for m in ({**dip, "uptrend": False}, {**dip, "drawdown": -0.3}, {**dip, "drawdown": -0.02}, {**dip, "rsi": 70}):
        assert all(x["key"] != "pullback" for x in screening.factors(m, [])["items"])


def rows_for_pick() -> list[dict]:
    base = lambda sym, sector, score, **m: {"symbol": sym, "name": sym, "security_type": "", "sources": m.pop("sources", ["거래대금 상위"]),  # noqa: E731
                                            "score": score, "sector": sector, "metrics": {**BASE, **m}}
    return [base("000001", "바이오", 5.0, change_5d=0.5), base("000002", "바이오", 4.0), base("000003", "바이오", 3.5), base("000004", "바이오", 3.4),
            base("000005", "바이오", 3.3), base("000006", "게임", 3.0, rsi=80), base("000007", "게임", 2.0), base("000008", None, 1.0),
            base("000009", "게임", 0.5, change_5d=0.6, sources=["운용 지시"])]


def test_strict_code_pick_applies_the_rules():
    got = screening.code_pick(rows_for_pick(), 5, strict=True)
    syms = [x["symbol"] for x in got["picks"]]
    assert syms == ["000009", "000002", "000003", "000004", "000007"]                                       # 운용 지시 먼저, 그다음 규칙대로
    assert "급등 직후" in got["dropped"]["000001"] and "과열" in got["dropped"]["000006"] and "3개 한도" in got["dropped"]["000005"]
    assert [x["symbol"] for x in screening.code_pick(rows_for_pick(), 2)["picks"]] == ["000009", "000001"]   # 회의 후보는 그대로


def test_strict_llm_pick_enforces_sector_cap(monkeypatch):
    async def fake(feature, system, prompt, schema, model):
        return {"picks": [{"symbol": s, "reason": "이유"} for s in ("000001", "000002", "000003", "000004", "000005")], "summary": "바이오 몰빵"}

    monkeypatch.setattr(screening, "LLM", fake)
    got = screening.llm_pick(rows_for_pick(), "", 5, strict=True)
    syms = [x["symbol"] for x in got["picks"]]
    assert syms[:3] == ["000001", "000002", "000003"]                                                        # LLM 은 이유를 들어 급등주를 고를 수 있다
    assert "3개 한도" in got["dropped"]["000004"] and "000006" not in syms and "000007" in syms               # 채울 때는 과열 건너뜀


# ---- 파이프라인 ----
class WideMarket(SampleMarket):
    """토스처럼 섹터가 197개 — 앞 60개만 보면 뒤쪽 강세 섹터를 놓친다."""

    def sectors(self):
        return [{"id": f"X{i}", "name": f"섹터{i}", "depth": 0 if i < 39 else 1, "parent": None if i < 39 else f"X{i % 39}"} for i in range(197)]

    def sector_perf(self, sector_id):
        i = int(sector_id[1:])
        return {"id": sector_id, "name": f"섹터{i}", "change_rates": {"1w": 0.09 if i == 196 else -0.01, "1mo": 0.1 if i == 196 else -0.02}}


def test_sector_table_covers_every_sector_and_logs_it(conn):
    notes: list[str] = []
    rows = desk_agents.sector_table(WideMarket(), notes)
    assert len(rows) == 197 and rows[0]["name"] == "섹터196" and rows[0]["parent"] == "X1"
    text = desk_agents.sector_text(rows, set(), {"regime": "neutral", "headline": "h"}, "")
    assert "[X196]" in text and "위쪽·아래쪽" in text and text.count("\n- [") >= desk_agents.SECTOR_ROWS
    out = desk_agents.sector_view(conn, WideMarket(), {"regime": "risk_off", "headline": "h"}, "", None, notes, run_id=7)
    assert out["count"] == 197 and out["overweight"][0]["name"] == "섹터196"
    run = scorelog.get_run(conn, scorelog.runs(conn, "sector")[0]["id"])
    assert len(run["rows"]) == 197 and run["regime"] == "risk_off" and run["run_id"] == 7 and run["picked"] == len(out["overweight"])
    assert run["rows"][0]["decision"] == "overweight" and run["rows"][0]["detail"]["rates"]["1mo"] == 0.1


def test_sector_view_drops_parent_with_child(conn, monkeypatch):
    async def fake(feature, system, prompt, schema, model):
        return {"overweight": [{"id": "S10", "reason": "IT"}, {"id": "S11", "reason": "반도체는 IT 안"}, {"id": "S20", "reason": "2차전지"}], "underweight": [], "summary": ""}

    monkeypatch.setattr(desk_agents, "LLM", fake)
    out = desk_agents.sector_view(conn, SampleMarket(), {"regime": "neutral", "headline": "h"}, "", None, [])
    assert [x["id"] for x in out["overweight"]] == ["S10", "S20"]


def test_pool_adds_sector_mid_caps_and_value(conn):
    class P(SampleMarket):
        def sector(self, sector_id):
            stocks = [{"symbol": f"00{i:04d}", "name": f"s{i}", "market_cap": 1e12 - i, "trading_amount": float(i)} for i in range(30)]
            return {**super().sector(sector_id), "stocks": stocks}

    notes: list[str] = []
    rows = screening.pool(conn, P(), [], [{"id": "S20", "name": "2차전지"}], [], notes, value=[{"symbol": "111111", "name": "숨은 가치주", "security_type": "STOCK"}])
    syms = [r["symbol"] for r in rows]
    assert syms[:5] == [f"00{i:04d}" for i in range(5)] and syms[5:8] == ["000029", "000028", "000027"]      # 시총 상위 + 거래대금 상위
    assert rows[8]["symbol"] == "111111" and rows[8]["sources"] == ["가치 상위"]


def test_research_excludes_holdings_and_records_everything(conn, broker, at_open, monkeypatch):
    from app.invest.ops import autopilot
    fill(conn, "005930", 100e9, 15e9)
    autopilot.add_intent(conn, "NAVER 를 눈여겨보자")                # 운용 지시에 나와도 보유면 발굴이 아니라 회의에서 본다
    monkeypatch.setattr(desk_agents, "_held", lambda c, p, notes: {"035420"})
    out = desk_agents.research(conn, SampleMarket(), run_id=None)
    iv = out["ideas"]
    assert "035420" not in {x["symbol"] for x in iv["picks"]}
    run = scorelog.get_run(conn, scorelog.runs(conn, "ideas")[0]["id"])
    by = {r["subject"]: r for r in run["rows"]}
    assert run["pool"] == iv["pool"] == len(run["rows"]) and run["regime"] == out["market"]["regime"] and run["picked"] == len(iv["picks"])
    assert by["035420"]["decision"] == "excluded" and "보유" in by["035420"]["reason"]
    pick = next(r for r in run["rows"] if r["decision"] == "picked")
    assert pick["pick_rank"] and pick["price"] and pick["detail"]["factors"]["items"] and pick["detail"]["metrics"]["rsi"] is not None
    assert {r["decision"] for r in run["rows"]} <= {"picked", "passed", "excluded"}
    rules = scorelog.rules(conn, run["rules_hash"])["rules"]
    assert rules["weights"]["risk_off"]["value"] == 1.4 and rules["valuation"]["bands"]["per"]["steps"][0] == [8, 100]


def test_meeting_logs_only_real_decisions(conn, broker, at_open):
    from app.invest.debate import scopes
    scopes.resolve(conn, SampleMarket(), {"scope": "scan", "intents": []}, 3)
    assert scorelog.runs(conn, "meeting") == []
    scopes.resolve(conn, SampleMarket(), {"scope": "scan", "intents": [], "run_id": 9, "agenda": "안건"}, 3, pick=lambda rows, t, n, c: screening.code_pick(rows, n))
    run = scorelog.runs(conn, "meeting")[0]
    assert run["run_id"] == 9 and run["meta"]["agenda"] == "안건" and run["picked"] == 3


# ---- 기록·API ----
def test_rules_are_stored_once_and_change_with_content(conn):
    a = scorelog.save_rules(conn, {"x": 1})
    assert a == scorelog.save_rules(conn, {"x": 1}) and a != scorelog.save_rules(conn, {"x": 2})
    assert conn.execute("SELECT COUNT(*) AS n FROM invest_score_rules").fetchone()["n"] == 2


def test_record_never_blocks_research(conn, monkeypatch):
    assert scorelog.record(conn, "nope", [], {"x": 1}) is None                                               # CHECK 위반 → 삼킨다
    assert scorelog.runs(conn) == []


def test_score_api_reads_back(client):
    from app.core.db import connect
    db = connect()
    rid = scorelog.record(db, "ideas", [{"subject": "005930", "name": "삼성전자", "score": 3.2, "value_score": 71.0, "price": 70000, "decision": "picked", "pick_rank": 1,
                                         "reason": "싸다", "sources": ["가치 상위"], "detail": {"sector": "반도체"}},
                                        {"subject": "000660", "name": "SK하이닉스", "score": 1.0, "decision": "excluded", "reason": "보유 종목"}],
                          {"v": 1}, regime="risk_off", by="llm", summary="요약", notes=["메모"])
    db.close()
    got = client.get(f"/api/invest/score-runs/{rid}").json()
    assert got["kind_label"] == "종목 발굴" and got["rows"][0]["sources"] == ["가치 상위"] and got["rows"][1]["decision"] == "excluded" and got["notes"] == ["메모"]
    assert [r["id"] for r in client.get("/api/invest/score-runs?kind=ideas").json()] == [rid] and client.get("/api/invest/score-runs?kind=value").json() == []
    assert client.get(f"/api/invest/score-rules/{got['rules_hash']}").json()["rules"] == {"v": 1}
    assert client.get("/api/invest/score-rules/none").status_code == 404 and client.get("/api/invest/score-runs/999").status_code == 404
    hist = client.get("/api/invest/scores/005930").json()
    assert hist[0]["value_score"] == 71.0 and hist[0]["regime"] == "risk_off"
    lines = client.get("/api/invest/score-runs/export?kind=ideas").text.strip().split("\n")
    assert len(lines) == 2 and json.loads(lines[0])["subject"] == "005930" and json.loads(lines[0])["rules_hash"] == got["rules_hash"]
    st = client.get("/api/invest/valuation/status").json()
    assert st["symbols"] == 0 and "configured" in st


def test_dart_key_saves_without_touching_toss_token(client, monkeypatch):
    from app.invest import api as invest_api
    from app.invest.broker import config
    monkeypatch.setattr(config.envfile, "update", lambda values: None)
    cleared = []
    monkeypatch.setattr(invest_api, "clear_token_cache", lambda: cleared.append(1))
    r = client.put("/api/invest/config/keys", json={"DART_API_KEY": "k" * 40})
    assert r.status_code == 200 and cleared == [] and any(k["key"] == "DART_API_KEY" for k in r.json()["keys"])


def test_value_investor_has_the_tool():
    from app.invest.debate import agents
    assert "get_valuation" in next(a for a in agents.DEFAULT_ROSTER if a["key"] == "value")["tools"] and "get_valuation" in agents.TOOL_INFO


def test_value_investor_tool_answers_without_crashing(conn):  # noqa: F811
    """get_valuation 도구 — agents 에 valuation import 가 빠져 부를 때마다 NameError 였다."""
    s = agents.Session(conn=conn, scope={"label": "x", "candidates": [{"symbol": "005930", "name": "삼성전자"}]}, run_id=None,
                       provider=SampleMarket())
    out = asyncio.run(agents._handlers(s)["get_valuation"]({}))
    assert out["available"] is False and "OpenDART" in out["data_source"]
