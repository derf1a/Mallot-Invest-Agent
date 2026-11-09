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
