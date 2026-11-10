import pytest

from app.core.db import connect, init_db
from app.runs import runner
from app.runs.runner import JobError, JobSpec, RunContext


@pytest.fixture
def jobs(monkeypatch):
    monkeypatch.setattr(runner, "REGISTRY", {})

    def ok(ctx: RunContext, params: dict) -> dict:
        ctx.log("hello")
        ctx.metrics(1, score=0.5)
        (ctx.artifact_dir / "out.txt").write_text("x")
        ctx.set_artifact(ctx.artifact_dir)
        return {"echo": params["n"]}

    def bad(_ctx: RunContext, _params: dict) -> dict:
        raise JobError("일부러 실패")

    runner.register(JobSpec("ok", "성공 작업", "", ok, defaults={"n": 1}))
    runner.register(JobSpec("bad", "실패 작업", "", bad))


def test_run_records_logs_metrics_and_summary(client, jobs):
    with connect() as conn:
        run_id = runner.start_run(conn, "ok", {"n": 7})
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "succeeded" and run["summary"] == {"echo": 7} and run["artifact_path"] == f"runs/{run_id}"
    assert client.get(f"/api/runs/{run_id}/metrics").json() == {"score": [[1, 0.5]]}
    assert [log["message"] for log in client.get(f"/api/runs/{run_id}/logs").json()] == ["시작: 성공 작업", "hello", "완료"]
    assert [r["id"] for r in client.get("/api/runs", params={"kind": "ok"}).json()] == [run_id]


def test_failed_run_and_cancel_rules(client, jobs):
    with connect() as conn:
        run_id = runner.start_run(conn, "bad")
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "failed" and run["error"] == "일부러 실패"
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 409  # 끝난 실행은 취소할 수 없다
    with pytest.raises(JobError):
        with connect() as conn:
            runner.start_run(conn, "unknown")
    assert client.delete(f"/api/runs/{run_id}").status_code == 204
    assert client.get(f"/api/runs/{run_id}").status_code == 404


def test_runs_api_cannot_start_jobs(client):
    # 실행은 각 기능의 엔드포인트(PIN 확인 등)를 거쳐서만 시작한다
    assert client.post("/api/runs", json={"kind": "agent_action"}).status_code == 405


def test_recover_marks_interrupted_runs_failed():
    init_db()
    with connect() as conn:
        conn.execute("INSERT INTO runs (kind, name, status) VALUES ('ok', 'x', 'running')")
        conn.commit()
        assert runner.recover_interrupted(conn) == 1
        assert tuple(conn.execute("SELECT status, error FROM runs").fetchone()) == ("failed", "서버가 재시작되어 중단됨")


def test_interrupted_stream_keeps_its_orders():
    """서버 재시작으로 끊긴 상시 운용 — 그때까지 낸 주문으로 요약이 남고, 예전에 요약 없이 끊긴 실행도 다음 시작 때 채운다."""
    import json

    from app.core.db import data_dir
    from app.invest.advice import assistant
    from app.invest.jobs import agent_action  # noqa: F401 — on_interrupted 등록
    from app.invest.ops import briefing

    init_db()
    with connect() as conn:
        params = json.dumps({"scope": "autopilot", "stream": True, "autopilot": True, "trigger": "stream"})
        old = conn.execute("INSERT INTO runs (kind, name, status, error, params_json, artifact_path) VALUES "
                           "('agent_action', '상시 운용 · 11-10', 'failed', ?, ?, 'runs/old')", (runner.INTERRUPTED, params)).lastrowid
        live = conn.execute("INSERT INTO runs (kind, name, status, params_json) VALUES ('agent_action', '상시 운용 · 11-11', 'running', ?)",
                            (params,)).lastrowid
        for rid, sym, side, st in ((old, "030200", "buy", "filled"), (old, "035420", "sell", "filled"), (old, "005930", "buy", "cancelled")):
            conn.execute("INSERT INTO invest_orders (symbol, name, side, order_type, quantity, status, run_id) VALUES (?, ?, ?, 'market', 1, ?, ?)",
                         (sym, {"030200": "KT", "035420": "NAVER"}.get(sym, "삼성전자"), side, st, rid))
        conn.commit()
        folder = data_dir() / "runs" / "old"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / agent_action.MESSAGES).write_text('{"seq": 1, "type": "start"}\n', encoding="utf-8")
        assert runner.recover_interrupted(conn) == 1                                   # 돌던 것 하나를 실패로
        s = json.loads(conn.execute("SELECT summary_json FROM runs WHERE id = ?", (old,)).fetchone()[0])
        assert s["interrupted"] and [o["symbol"] for o in s["orders"]] == ["030200", "035420", "005930"]
        assert s["minutes"]["headline"] == "서버 재시작으로 상시 운용이 끊김 — 그때까지 주문 3건(체결 2건)"
        assert "KT 매수, NAVER 매도" in s["minutes"]["summary"]
        last = json.loads((folder / agent_action.MESSAGES).read_text(encoding="utf-8").splitlines()[-1])
        assert last["type"] == "end" and last["seq"] == 2                                # 채팅방이 '진행 중'으로 남지 않게
        assert json.loads(conn.execute("SELECT summary_json FROM runs WHERE id = ?", (live,)).fetchone()[0])["minutes"]["headline"].endswith("낸 주문 없음")
        assert runner.recover_interrupted(conn) == 0                                    # 두 번 해도 그대로
        assert len((folder / agent_action.MESSAGES).read_text(encoding="utf-8").splitlines()) == 2
        meetings = briefing._meetings(conn, {})
        assert meetings[-1]["id"] == old and meetings[-1]["status"] == "interrupted"
        ctx = assistant._context(conn, assistant.add(conn, "user", "어제 왜 실패했어?")["id"])
        assert f"#{old} 상시 운용 · 11-10 · 중단(서버 재시작 — 실패 아님)" in ctx and "KT buy" in ctx
