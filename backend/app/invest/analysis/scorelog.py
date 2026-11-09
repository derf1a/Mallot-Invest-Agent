"""판단 기록 — 섹터 평가·가치 스크린·발굴 목록·회의 후보를 낼 때마다 '무엇을 어떤 기준으로 몇 점을 줘서 골랐는지'를 데이터로 남긴다.

- invest_score_rules: 그때의 기준표 전부(가중치·구간·한도). 내용 해시가 키라 기준을 고치면 자동으로 새 줄이 생긴다 — 버전을 손으로 올리지 않는다.
- invest_score_runs: 판단 한 번 (종류·시각·실행·시장 국면·기준 해시·훑은 수·고른 사람·요약).
- invest_score_rows: 줄마다 점수·결정(picked·passed·excluded·overweight·underweight)·이유·판단 때 가격 + detail(지표·항목별 점수·가치평가 근거).

뒤에 '왜 샀나', '그 점수가 맞았나'를 볼 수 있게 고르지 않은 종목도 같이 남긴다. 기록 실패가 리서치·회의를 멈추지 않게 record 는 예외를 삼킨다.
"""

import hashlib
import json
import logging
import sqlite3
from collections.abc import Iterator
from datetime import datetime

from app.invest.marketdata import market

KINDS = {"sector": "섹터 평가", "value": "가치 스크린", "ideas": "종목 발굴", "meeting": "회의 후보"}
log = logging.getLogger(__name__)


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def save_rules(conn: sqlite3.Connection, rules: dict, now: datetime | None = None) -> str:
    text = _dumps(rules)
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    conn.execute("INSERT OR IGNORE INTO invest_score_rules (hash, created_at, rules) VALUES (?, ?, ?)",
                 (digest, (now or market.now_kst()).strftime("%Y-%m-%d %H:%M:%S"), text))
    return digest


def record(conn: sqlite3.Connection, kind: str, rows: list[dict], rules: dict, *, run_id: int | None = None, regime: str = "",
           pool: int | None = None, by: str = "", summary: str = "", notes: list[str] = (), meta: dict | None = None,
           now: datetime | None = None) -> int | None:
    """판단 한 번을 남긴다 → 실행 id. rows: [{subject, name, score, value_score, price, decision, pick_rank, reason, sources, detail}] (순서 = 순위)."""
    now = now or market.now_kst()
    try:
        digest = save_rules(conn, rules, now)
        picked = sum(1 for r in rows if r.get("decision") in ("picked", "overweight"))
        cur = conn.execute(
            "INSERT INTO invest_score_runs (created_at, day, kind, run_id, regime, rules_hash, pool, picked, picked_by, summary, notes, meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now.strftime("%Y-%m-%d %H:%M:%S"), now.date().isoformat(), kind, run_id, regime, digest,
             len(rows) if pool is None else pool, picked, by, summary, _dumps(list(notes)), _dumps(meta or {})))
        run = cur.lastrowid
        conn.executemany(
            "INSERT INTO invest_score_rows (score_run_id, rank, subject, name, score, value_score, price, decision, pick_rank, reason, sources, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(run, i + 1, r["subject"], r.get("name") or "", r.get("score"), r.get("value_score"), r.get("price"),
              r.get("decision") or "passed", r.get("pick_rank"), (r.get("reason") or "")[:500], _dumps(r.get("sources") or []),
              _dumps(r.get("detail") or {})) for i, r in enumerate(rows)])
        conn.commit()
        return run
    except sqlite3.Error:
        log.exception("판단 기록을 남기지 못했습니다 (%s)", kind)
        conn.rollback()
        return None


def _run(r: sqlite3.Row) -> dict:
    return {**dict(r), "kind_label": KINDS.get(r["kind"], r["kind"]), "notes": json.loads(r["notes"]), "meta": json.loads(r["meta"])}


def _row(r: sqlite3.Row) -> dict:
    return {**dict(r), "sources": json.loads(r["sources"]), "detail": json.loads(r["detail"])}


def runs(conn: sqlite3.Connection, kind: str | None = None, limit: int = 50, before: int | None = None) -> list[dict]:
    where, args = [], []
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if before:
        where.append("id < ?")
        args.append(before)
    sql = "SELECT * FROM invest_score_runs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    return [_run(r) for r in conn.execute(sql, (*args, limit))]


def get_run(conn: sqlite3.Connection, run_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM invest_score_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    rows = conn.execute("SELECT * FROM invest_score_rows WHERE score_run_id = ? ORDER BY rank", (run_id,)).fetchall()
    return {**_run(row), "rows": [_row(r) for r in rows]}


def rules(conn: sqlite3.Connection, digest: str) -> dict | None:
    row = conn.execute("SELECT * FROM invest_score_rules WHERE hash = ?", (digest,)).fetchone()
    return None if row is None else {"hash": row["hash"], "created_at": row["created_at"], "rules": json.loads(row["rules"])}


def history(conn: sqlite3.Connection, subject: str, limit: int = 60) -> list[dict]:
    """한 종목(또는 섹터)이 받은 점수·결정의 기록 — 최근 것부터."""
    rows = conn.execute(
        "SELECT x.*, r.created_at, r.kind, r.regime, r.rules_hash, r.picked_by FROM invest_score_rows x "
        "JOIN invest_score_runs r ON r.id = x.score_run_id WHERE x.subject = ? ORDER BY x.score_run_id DESC LIMIT ?", (subject, limit))
    return [_row(r) for r in rows]


def export(conn: sqlite3.Connection, kind: str | None = None, since: str | None = None) -> Iterator[str]:
    """JSONL — 판단 한 줄에 줄 하나 (실행 정보를 같이). 분석할 때 파일로 받는다."""
    where, args = [], []
    if kind:
        where.append("r.kind = ?")
        args.append(kind)
    if since:
        where.append("r.day >= ?")
        args.append(since)
    sql = ("SELECT x.*, r.created_at, r.day, r.kind, r.run_id, r.regime, r.rules_hash, r.picked_by FROM invest_score_rows x "
           "JOIN invest_score_runs r ON r.id = x.score_run_id" + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY x.score_run_id, x.rank")
    for r in conn.execute(sql, args):
        yield json.dumps(_row(r), ensure_ascii=False) + "\n"
