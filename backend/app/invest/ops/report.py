"""LLM 뉴스 리포트 — 보유·관심·시장 뉴스를 종목별로 분석하고 하나의 보고서로 묶는다.

1) 종목별 분석 (동시에, 최대 3개): 번호 붙인 기사 목록 + 시세 요약만 근거로 감성·핵심·위험·촉매를 뽑는다.
2) 종합 보고서: 종목별 분석 결과만 근거로 헤드라인·시장 요약·포트폴리오 관점·점검할 것.
근거 규칙: 기사에 없는 숫자·사실을 지어내지 않는다. 핵심 포인트마다 근거 기사 번호를 단다 — 코드가 받은 목록에 없는 번호는
지우고 그 수를 기록한다(citations_dropped). 매수·매도 권유가 아니라 정보 정리다.
"""

import asyncio
from collections.abc import Awaitable, Callable

from app.llm.client import structured

SENTIMENTS = ("positive", "neutral", "negative", "mixed")
SENTIMENT_KO = {"positive": "긍정", "neutral": "중립", "negative": "부정", "mixed": "혼재"}

_IDS = {"type": "array", "items": {"type": "integer"}}
ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "sentiment": {"type": "string", "enum": list(SENTIMENTS)},
        "sentiment_score": {"type": "number", "minimum": -1, "maximum": 1},
        "summary": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "object", "properties": {
            "point": {"type": "string"}, "news_ids": _IDS}, "required": ["point", "news_ids"]}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "catalysts": {"type": "array", "items": {"type": "string"}},
        "watch": {"type": "array", "items": {"type": "string"}},
        "information_gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["sentiment", "sentiment_score", "summary", "key_points", "risks", "catalysts"],
}
REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "headline": {"type": "string"},
        "market_summary": {"type": "string"},
        "portfolio_view": {"type": "string"},
        "highlights": {"type": "array", "items": {"type": "object", "properties": {
            "symbol": {"type": "string"}, "verdict": {"type": "string"}}, "required": ["symbol", "verdict"]}},
        "action_items": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "headline", "market_summary", "highlights", "action_items", "risks"],
}

SYSTEM = """당신은 개인 투자자를 돕는 금융 뉴스 애널리스트입니다.
- 근거는 사용자가 준 기사 목록(번호·제목·출처·시각·요약 문구)과 시세 요약뿐입니다. 기사 본문은 없습니다.
- 자료에 없는 숫자·실적·사실을 지어내지 마세요. 제목만으로 단정하기 어려우면 그렇다고 쓰세요.
- 핵심 포인트마다 근거 기사 번호(news_ids)를 다세요. 목록에 없는 번호는 쓰지 마세요.
- 매수·매도를 권유하지 말고, 판단에 필요한 정보를 정리하세요. 한국어로 간결하게."""

LlmJson = Callable[[str, str, str, dict, str | None], Awaitable[dict]]


async def _default_llm(feature: str, system: str, prompt: str, schema: dict, model: str | None) -> dict:
    return await structured(feature, prompt, schema, model=model, system_prompt=system)


LLM: LlmJson = _default_llm  # 테스트에서 가짜로 바꾼다


def _news_lines(news: list[dict]) -> str:
    return "\n".join(f"[{n['id']}] {n['title']} ({n.get('source') or '출처 미상'}, {n.get('published_at') or '시각 미상'})"
                     + (f" — {n['snippet'][:160]}" if n.get("snippet") else "") for n in news) or "(기사 없음)"


def _market_lines(snap: dict | None) -> str:
    if not snap:
        return "(시세 정보 없음)"
    t = snap.get("technicals") or {}
    parts = [f"현재가 {snap.get('price')} {snap.get('currency', '')}"]
    if snap.get("change_rate") is not None:
        parts.append(f"전일 대비 {snap['change_rate'] * 100:+.2f}%")
    for k, label in (("return_20d", "20일 수익률"), ("return_60d", "60일 수익률")):
        if t.get(k) is not None:
            parts.append(f"{label} {t[k] * 100:+.1f}%")
    if t.get("rsi14") is not None:
        parts.append(f"RSI14 {t['rsi14']}")
    return ", ".join(parts) + (" (샘플 데이터 — 실제 시세 아님)" if snap.get("provider") == "sample" else "")


def clean_analysis(raw: dict, allowed: set[int]) -> tuple[dict, int]:
    """형식을 맞추고 받은 목록에 없는 기사 번호를 지운다. (분석, 지운 번호 수)"""
    dropped = 0
    points = []
    for p in raw.get("key_points") or []:
        if not isinstance(p, dict) or not str(p.get("point") or "").strip():
            continue
        ids = [i for i in p.get("news_ids") or [] if isinstance(i, int)]
        keep = [i for i in ids if i in allowed]
        dropped += len(ids) - len(keep)
        points.append({"point": str(p["point"]).strip(), "news_ids": keep})
    sentiment = raw.get("sentiment") if raw.get("sentiment") in SENTIMENTS else "neutral"
    try:
        score = max(-1.0, min(1.0, float(raw.get("sentiment_score") or 0)))
    except (TypeError, ValueError):
        score = 0.0
    strs = lambda k: [str(x).strip() for x in raw.get(k) or [] if str(x).strip()]  # noqa: E731
    return {"sentiment": sentiment, "sentiment_score": score, "summary": str(raw.get("summary") or "").strip(),
            "key_points": points, "risks": strs("risks"), "catalysts": strs("catalysts"), "watch": strs("watch"),
            "information_gaps": strs("information_gaps")}, dropped


async def analyze(target: dict, news: list[dict], snap: dict | None, model: str | None) -> tuple[dict, int]:
    label = "시장 전체" if target["symbol"] is None else f"{target['name']}({target['symbol']})"
    prompt = (f"분석 대상: {label}\n시세 요약: {_market_lines(snap)}\n\n기사 목록:\n{_news_lines(news)}\n\n"
              "이 대상에 대해 뉴스 흐름을 분석하세요. 기사가 없거나 부족하면 information_gaps 에 쓰고 sentiment 는 neutral 로.")
    raw = await LLM("news_analysis", SYSTEM, prompt, ANALYSIS_SCHEMA, model)
    return clean_analysis(raw if isinstance(raw, dict) else {}, {n["id"] for n in news})


async def compose(scope_label: str, sections: list[dict], model: str | None) -> dict:
    lines = []
    for s in sections:
        a = s["analysis"]
        lines.append(f"## {s['label']} — {SENTIMENT_KO[a['sentiment']]} ({a['sentiment_score']:+.2f})\n요약: {a['summary']}\n"
                     + "".join(f"- {p['point']}\n" for p in a["key_points"])
                     + (f"위험: {'; '.join(a['risks'])}\n" if a["risks"] else "")
                     + (f"촉매: {'; '.join(a['catalysts'])}\n" if a["catalysts"] else ""))
    prompt = (f"범위: {scope_label}\n아래는 대상별 뉴스 분석입니다. 이것만 근거로 종합 보고서를 쓰세요.\n"
              "highlights 의 symbol 은 종목 코드(시장 전체는 MARKET)를 쓰세요.\n\n" + "\n".join(lines))
    raw = await LLM("news_report", SYSTEM, prompt, REPORT_SCHEMA, model)
    raw = raw if isinstance(raw, dict) else {}
    strs = lambda k: [str(x).strip() for x in raw.get(k) or [] if str(x).strip()]  # noqa: E731
    return {"title": str(raw.get("title") or f"{scope_label} 뉴스 리포트").strip(),
            "headline": str(raw.get("headline") or "").strip(),
            "market_summary": str(raw.get("market_summary") or "").strip(),
            "portfolio_view": str(raw.get("portfolio_view") or "").strip(),
            "highlights": [{"symbol": str(h.get("symbol") or ""), "verdict": str(h.get("verdict") or "")}
                           for h in raw.get("highlights") or [] if isinstance(h, dict)],
            "action_items": strs("action_items"), "risks": strs("risks")}


async def analyze_all(items: list[tuple[dict, list[dict], dict | None]], model: str | None,
                      on_done: Callable[[dict, dict], None] | None = None) -> list[tuple[dict, int]]:
    sem = asyncio.Semaphore(3)

    async def one(target, news, snap):
        async with sem:
            result = await analyze(target, news, snap, model)
        if on_done:
            on_done(target, result[0])
        return result

    return await asyncio.gather(*(one(*it) for it in items))


def to_markdown(report: dict) -> str:
    out = [f"# {report['title']}", "", f"> {report['headline']}" if report.get("headline") else "", ""]
    if report.get("market_summary"):
        out += ["## 시장", report["market_summary"], ""]
    if report.get("portfolio_view"):
        out += ["## 포트폴리오 관점", report["portfolio_view"], ""]
    for s in report.get("sections") or []:
        a = s["analysis"]
        out += [f"## {s['label']} · {SENTIMENT_KO[a['sentiment']]} ({a['sentiment_score']:+.2f})", a["summary"], ""]
        for p in a["key_points"]:
            refs = "".join(f" [{i}]" for i in p["news_ids"])
            out.append(f"- {p['point']}{refs}")
        if a["risks"]:
            out.append(f"- 위험: {'; '.join(a['risks'])}")
        out.append("")
    if report.get("action_items"):
        out += ["## 점검할 것", *[f"- {x}" for x in report["action_items"]], ""]
    if report.get("news"):
        out += ["## 기사", *[f"[{n['id']}] {n['title']} — {n.get('source', '')} {n['url']}" for n in report["news"]]]
    return "\n".join(out).strip() + "\n"
