"""작업: 운용 데스크 리서치 — 시장 분석(시장 전략가) → 섹터 탐색(섹터 애널리스트) → 종목 발굴(종목 발굴 애널리스트).

자동 운용이 켜져 있으면 desk_agents.tick 이 장 시작 전·시장 급변 때 시작하고, 화면('지금 리서치')·비서(run_research)로도 시작한다.
단계마다 invest_desk 에 남기고 AI Trading 채팅방에 그 에이전트가 말한다. 산출물 desk.json(세 단계 전체). 숫자는 코드, LLM 은 판단과 이유.
"""

import json

from app.invest.ops import desk, desk_agents
from app.invest.marketdata import market
from app.invest.broker import config
from app.runs.runner import JobSpec, RunContext, register

DEFAULTS = {"trigger": "manual", "note": "", "model": None}


def run(ctx: RunContext, params: dict) -> dict:
    model = str(params.get("model") or config.agent_options(ctx.conn)["model"] or "") or None
    p = market.provider()
    trigger = str(params.get("trigger") or "manual")
    ctx.log(f"리서치 시작 · {desk.TRIGGER_KO.get(trigger, trigger)} · 시세 {p.title}"
            + (f" · {params['note']}" if params.get("note") else ""))
    out = desk_agents.research(ctx.conn, p, model=model, run_id=ctx.run_id, trigger=trigger, note=str(params.get("note") or ""),
                               log=ctx.log, progress=ctx.progress, check_cancel=ctx.check_cancel)
    ctx.set_artifact(ctx.artifact_dir)
    (ctx.artifact_dir / "desk.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    mv, sv, iv = out["market"], out["sector"], out["ideas"]
    return {"trigger": trigger, "regime": mv["regime"], "headline": mv["headline"],
            "overweight": [x["name"] for x in sv["overweight"]], "underweight": [x["name"] for x in sv["underweight"]],
            "ideas": [x["name"] for x in iv["picks"]], "pool": iv["pool"], "sectors": sv["count"],
            "by": {"market": mv["by"], "sector": sv["by"], "ideas": iv["by"]}, "provider": p.key}


register(JobSpec(
    kind="desk_research",
    title="운용 데스크 리서치",
    description="시장 전략가가 시장 국면을, 섹터 애널리스트가 토스 섹터 전체를 훑어 비중 확대·축소 섹터를, 종목 발굴 애널리스트가 확대 섹터·가치 "
                "스크린(OpenDART 재무)·랭킹·운용 지시에서 발굴 목록을 만듭니다. 회의(투자위원회)는 이 목록과 보유로 안건을 정합니다. 판단 근거는 모두 기록됩니다.",
    run=run,
    defaults=DEFAULTS,
    uses_llm=True,
))
