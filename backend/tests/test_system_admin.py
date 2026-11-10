"""시스템 관리자: 대기 요청 정리(LLM 결정을 코드가 검사), GitHub 올리기 · 맞추기(가짜 HTTP), API."""

import json

import pytest

from app.core import net
from app.core.db import connect, init_db
from app.system import agent, github, issues


@pytest.fixture
def conn():
    init_db()
    c = connect()
    yield c
    c.close()


def fake_llm(actions: list[dict], seen: list | None = None):
    async def llm(feature, system, prompt, schema, model):
        if seen is not None:
            seen.append(prompt)
        return {"actions": actions}
    return llm


def _reports(conn) -> list[int]:
    texts = [("가치 투자자", "018260은 compare_candidates에서 종목명과 유형이 비어 있고 코드로만 표시됩니다."),
             ("차티스트", "018260 종목은 compare_candidates에서 이름이 코드로만 표시됩니다."),
             ("비서", "상시 운용 판단 #6이 failed — '서버가 재시작되어 중단됨'"),
             ("리스크 매니저", "SK하이닉스 비중을 줄이는 게 좋겠습니다")]
    return [issues.report(conn, t, by)["id"] for by, t in texts]


def test_triage_groups_answers_and_validates(conn, monkeypatch):
    a, b, c, d = _reports(conn)
    seen: list[str] = []
    monkeypatch.setattr(agent, "LLM", fake_llm([
        {"report_ids": [a, b], "action": "issue", "title": "compare_candidates 가 일부 종목 이름·유형을 비움", "body": "현상: …"},
        {"report_ids": [c], "action": "answer", "answer": "서버 재시작 때문 — 주문은 유효"},
        {"report_ids": [d], "action": "ignore"},
        {"report_ids": [999], "action": "issue", "title": "없는 요청"},       # 대기 중이 아닌 id — 건너뜀
        {"report_ids": [a], "action": "link", "issue_id": 1},                # 이미 다룬 요청 — 건너뜀
    ], seen))
    out = agent.triage(conn)
    assert out == {"pending": 4, "issue": 1, "link": 0, "answer": 1, "ignore": 1, "skipped": 2}
    assert "요청 1 · 가치 투자자" in seen[0] and "[열린 이슈] 없음" in seen[0]
    [x] = issues.open_issues(conn)
    assert x["title"] == "compare_candidates 가 일부 종목 이름·유형을 비움" and x["reports"] == 2
    assert issues.counts(conn) == {"open": 1, "resolved": 0, "pending": 0}
    assert "서버 재시작 때문" in issues.brief(conn) and "compare_candidates" in issues.brief(conn)
    assert agent.triage(conn) == {"pending": 0}                                     # 대기가 없으면 LLM 을 부르지 않는다
    e = issues.report(conn, "하나투어(039130)는 compare_candidates에서 이름이 코드로만 표시됩니다.", "가치 투자자")["id"]
    monkeypatch.setattr(agent, "LLM", fake_llm([{"report_ids": [e], "action": "link", "issue_id": x["id"]}], seen))
    assert agent.triage(conn)["link"] == 1 and issues.get(conn, x["id"])["reports"] == 3
    assert f"- 이슈 {x['id']}: compare_candidates" in seen[-1]


def test_triage_keeps_reports_when_llm_fails(conn, monkeypatch):
    from app.llm.client import LLMError
    _reports(conn)

    async def broken(*_):
        raise LLMError("모델 오류")
    monkeypatch.setattr(agent, "LLM", broken)
    out = agent.triage(conn)
    assert out["pending"] == 4 and "모델 오류" in out["error"] and issues.counts(conn)["pending"] == 4


class FakeGitHub:
    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.remote: list[dict] = []

    def __call__(self, method, url, headers=None, data=None, timeout=15):
        body = json.loads(data) if data else None
        self.calls.append((method, url, body))
        assert headers["Authorization"] == "Bearer tok"
        if method == "POST":
            n = 40 + len(self.remote)
            self.remote.append({"number": n, "html_url": f"https://github.com/o/r/issues/{n}", "title": body["title"], "body": body["body"],
                                "state": "open", "created_at": "2026-11-11T10:00:00Z", "closed_at": None})
            return 201, json.dumps(self.remote[-1]).encode()
        return 200, json.dumps(self.remote).encode()


def test_publish_scrubs_and_sync_resolves(conn, monkeypatch):
    assert agent.publish(conn) == {"published": 0, "github": False}                # 토큰 없으면 앱 안에서만
    monkeypatch.setenv(github.TOKEN_ENV, "tok")
    monkeypatch.setenv(github.REPO_ENV, "o/r")
    gh = FakeGitHub()
    monkeypatch.setattr(net, "HTTP_REQUEST", gh)
    iid = issues.create(conn, "get_valuation 재무 없음", "예: 1,200,000원 · 3주 · 계좌 12345678901", "가치 투자자")
    assert agent.publish(conn) == {"published": 1, "github": True}
    method, url, body = gh.calls[0]
    assert url == "https://api.github.com/repos/o/r/issues" and body["labels"] == [github.LABEL]
    assert "[금액]" in body["body"] and "[수량]" in body["body"] and "[번호]" in body["body"] and "1,200,000" not in body["body"]
    assert "시스템 관리자 마테우스가 정리" in body["body"]
    assert issues.get(conn, iid)["number"] == 40 and agent.publish(conn)["published"] == 0   # 두 번 올리지 않는다
    gh.remote[0].update(state="closed", closed_at="2026-11-11T12:00:00Z")
    assert agent.sync(conn) == {"synced": 1, "changed": 1, "github": True}
    assert issues.get(conn, iid)["status"] == "resolved"


def test_system_api(client, monkeypatch):
    with connect() as c:
        _reports(c)
    st = client.get("/api/system/issues").json()
    assert st["counts"]["pending"] == 4 and len(st["pending"]) == 4 and st["github"]["configured"] is False
    assert st["admin"]["name"] == "마테우스"
    monkeypatch.setattr(agent, "LLM", fake_llm([{"report_ids": [1, 2], "action": "issue", "title": "이름 빈칸", "body": "현상"}]))
    out = client.post("/api/system/triage").json()
    assert out["result"]["triage"]["issue"] == 1 and out["counts"]["open"] == 1
    iid = out["issues"][0]["id"]
    detail = client.get(f"/api/system/issues/{iid}").json()
    assert [r["by"] for r in detail["requests"]] == ["가치 투자자", "차티스트"] and detail["events"][0]["kind"] == "opened"
    assert client.post(f"/api/system/issues/{iid}/resolve", json={"text": "고침"}).json()["status"] == "resolved"
    assert client.post(f"/api/system/issues/{iid}/reopen", json={}).json()["status"] == "open"
    assert client.get("/api/system/issues/999").status_code == 404
    assert client.get("/api/system/issues?status=bad").status_code == 422
