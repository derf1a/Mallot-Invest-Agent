"""판단 기록·가치평가 API — 리서치·회의가 무엇을 어떤 기준으로 몇 점을 줘서 골랐는지 읽는다 (기록은 scorelog, 평가는 valuation).

모두 읽기 전용이라 PIN 이 없다.
"""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from app.core.db import get_conn
from app.invest.debate import track
from app.invest.analysis import scorelog, valuation
from app.invest.marketdata import market

router = APIRouter(prefix="/api/invest", tags=["invest"])
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


@router.get("/score-runs")
def list_runs(conn: Conn, kind: str | None = None, before: int | None = None,
              limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[dict]:
    return scorelog.runs(conn, kind, limit, before)


@router.get("/score-runs/export")
def export_runs(conn: Conn, kind: str | None = None, since: str | None = None) -> PlainTextResponse:
    """JSONL — 판단 한 줄에 줄 하나."""
    return PlainTextResponse("".join(scorelog.export(conn, kind, since)), media_type="application/x-ndjson",
                             headers={"Content-Disposition": 'attachment; filename="mallot-score-log.jsonl"'})


@router.get("/score-runs/{run_id}")
def read_run(run_id: int, conn: Conn) -> dict:
    run = scorelog.get_run(conn, run_id)
    if run is None:
        raise HTTPException(404, "판단 기록을 찾을 수 없습니다")
    return run


@router.get("/score-rules/{digest}")
def read_rules(digest: str, conn: Conn) -> dict:
    rules = scorelog.rules(conn, digest)
    if rules is None:
        raise HTTPException(404, "기준표를 찾을 수 없습니다")
    return rules


@router.get("/scores/{subject}")
def subject_history(subject: str, conn: Conn, limit: Annotated[int, Query(ge=1, le=300)] = 60) -> list[dict]:
    return scorelog.history(conn, subject, limit)


@router.get("/valuation/status")
def valuation_status(conn: Conn) -> dict:
    return valuation.status(conn)


@router.get("/valuation/{symbol}")
def valuation_report(symbol: str, conn: Conn) -> dict:
    return valuation.report(conn, market.provider(), symbol.strip())


@router.get("/agent-scores")
def agent_scores(conn: Conn) -> dict:
    """에이전트별 지난 판단 성적 — 의견 무게의 성과 배수."""
    return {"scores": track.scores(conn, market.provider(), market.now_kst()), "horizon": track.HORIZON, "benchmark": track.BENCHMARK}
