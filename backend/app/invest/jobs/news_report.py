"""작업: LLM 뉴스 리포트 — 보유·관심·시장 뉴스를 모아 종목별로 분석하고 보고서로 묶는다 (투자 탭 'AI 리포트').

단계: 대상 정하기 → 뉴스 수집(Google 뉴스 RSS) → 종목별 LLM 분석(동시 3개) → 종합 보고서 → report.json·report.md 저장.
"""

import asyncio
import json

from app.invest import market, news, report, trading, watch
from app.invest.market import MarketError
from app.invest.trading import TradeError
from app.runs.runner import JobError, JobSpec, RunContext, register

SCOPES = {"holdings": "보유 종목", "watchlist": "관심 종목", "market": "시장 전체", "all": "보유·관심·시장",
          "symbols": "선택 종목"}
DEFAULTS = {"scope": "all", "symbols": [], "per_symbol": 8, "max_symbols": 8, "refresh": True, "model": ""}


def resolve_targets(conn, scope: str, symbols: list[str], max_symbols: int,
                    warn=lambda m: None) -> tuple[list[dict], bool]:
    """(대상 종목 [{symbol, name}], 시장 뉴스 포함 여부)"""
    if scope not in SCOPES:
        raise JobError(f"알 수 없는 범위입니다: {scope}")
    targets: list[dict] = []
    if scope in ("holdings", "all"):
        try:
            for h in trading.account(conn).summary()["holdings"]:
                targets.append({"symbol": h["symbol"], "name": h.get("name") or h["symbol"]})
        except (TradeError, MarketError) as e:
            warn(f"보유 종목을 불러오지 못했습니다: {e}")
    if scope in ("watchlist", "all"):
        targets += [{"symbol": r["symbol"], "name": r["name"] or r["symbol"]} for r in watch.symbols(conn)]
    if scope == "symbols":
        p = market.provider()
        for s in symbols:
            try:
                targets.append({"symbol": s, "name": p.info(s).get("name") or s})
            except MarketError as e:
                warn(str(e))
        if not targets:
            raise JobError("분석할 종목이 없습니다")
    seen, uniq = set(), []
    for t in targets:
        if t["symbol"] not in seen:
            seen.add(t["symbol"])
            uniq.append(t)
    if len(uniq) > max_symbols:
        warn(f"종목이 {len(uniq)}개라 앞의 {max_symbols}개만 분석합니다")
        uniq = uniq[:max_symbols]
    return uniq, scope in ("market", "all")


def run(ctx: RunContext, params: dict) -> dict:
    scope = str(params.get("scope") or "all")
    per_symbol = max(3, min(int(params.get("per_symbol") or 8), 20))
    targets, include_market = resolve_targets(ctx.conn, scope, [str(s) for s in params.get("symbols") or []],
                                              max(1, min(int(params.get("max_symbols") or 8), 15)),
                                              warn=lambda m: ctx.log(m, "warn"))
    if not targets and not include_market:
        raise JobError("분석할 대상이 없습니다 — 보유 종목이나 관심 종목을 먼저 추가하세요")
    ctx.log(f"대상: {', '.join(t['name'] for t in targets) or '없음'}" + (" + 시장 전체" if include_market else ""))

    # 1) 뉴스 수집
    collected = news.collect(ctx.conn, targets, include_market, per_query=per_symbol,
                             force=bool(params.get("refresh", True)), on_error=lambda m: ctx.log(m, "warn"))
    total = sum(len(v) for v in collected.values())
    ctx.log(f"뉴스 {total}건 수집")
    if total == 0:
        raise JobError("수집한 뉴스가 없습니다 (네트워크 또는 피드 확인)")
    ctx.progress(0.2)
    ctx.check_cancel()

    p = market.provider()
    items = []
    for t in targets:
        try:
            snap = market.snapshot(p, t["symbol"])
        except MarketError:
            snap = None
        items.append(({**t, "label": f"{t['name']} ({t['symbol']})"}, collected.get(t["symbol"], [])[:per_symbol], snap))
    if include_market:
        items.append(({"symbol": None, "name": "시장 전체", "label": "시장 전체"}, collected.get(None, [])[:per_symbol * 2],
                      None))

    # 2) 종목별 분석
    model = str(params.get("model") or "") or None
    done = {"n": 0}

    def on_done(target: dict, analysis: dict) -> None:
        done["n"] += 1
        ctx.metrics(done["n"], sentiment=analysis["sentiment_score"], key_points=len(analysis["key_points"]))
        ctx.progress(0.2 + 0.6 * done["n"] / len(items))

    results = asyncio.run(report.analyze_all(items, model, on_done))
    dropped = sum(r[1] for r in results)
    if dropped:
        ctx.log(f"목록에 없는 기사 번호 {dropped}개를 지웠습니다", "warn")
    sections = [{"symbol": it[0]["symbol"], "label": it[0]["label"], "analysis": res[0],
                 "news_ids": [n["id"] for n in it[1]]} for it, res in zip(items, results)]
    ctx.check_cancel()

    # 3) 종합 보고서
    composed = asyncio.run(report.compose(SCOPES[scope], sections, model))
    used = {i for it in items for i in (n["id"] for n in it[1])}
    news_list = [{k: n[k] for k in ("id", "title", "source", "published_at", "url", "symbols")}
                 for it in items for n in it[1] if n["id"] in used]
    seen: set[int] = set()
    news_list = [n for n in news_list if not (n["id"] in seen or seen.add(n["id"]))]
    full = {**composed, "scope": scope, "scope_label": SCOPES[scope], "sections": sections, "news": news_list,
            "provider": p.key, "citations_dropped": dropped}
    out = ctx.artifact_dir
    (out / "report.json").write_text(json.dumps(full, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "report.md").write_text(report.to_markdown(full), encoding="utf-8")
    ctx.set_artifact(out)
    ctx.log(f"보고서: {composed['title']}")
    return {"title": composed["title"], "headline": composed["headline"], "scope": scope,
            "symbols": [t["symbol"] for t in targets], "news_count": len(news_list), "citations_dropped": dropped,
            "sentiments": {s["symbol"] or "MARKET": s["analysis"]["sentiment_score"] for s in sections}}


register(JobSpec(
    kind="news_report",
    title="LLM 뉴스 리포트",
    description="보유·관심·시장 뉴스를 모아 종목별로 분석하고 하나의 보고서로 묶습니다 (근거 기사 번호 검증).",
    run=run,
    defaults=DEFAULTS,
    uses_llm=True,
))
