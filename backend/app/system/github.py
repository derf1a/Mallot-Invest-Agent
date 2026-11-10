"""GitHub 이슈 — 시스템 관리자가 정리한 이슈를 올리고, 닫혔는지 읽어 온다 (agent.py).

- 설정: .env 의 MIA_GITHUB_TOKEN(이슈 쓰기 권한 — fine-grained 토큰이면 이 저장소의 Issues: Read and write) ·
  MIA_GITHUB_REPO(기본 아래 REPO). 부를 때마다 .env 를 다시 읽어서 토큰을 넣으면 서버를 다시 켜지 않아도 따른다. 토큰이 없으면
  올리지 않고 앱 안에서만 정리한다.
- 앱이 올린 이슈에는 LABEL 을 붙이고, 읽어 올 때도 그 라벨만 본다 — 사람이 연 다른 이슈는 건드리지 않는다.
- 저장소가 공개일 수 있다: 올리기 전에 issues.scrub 로 금액·수량·계좌 형식을 지운다(내용 규칙은 agent.SYSTEM).
"""

import json
import os

from app.core import envfile, net

TOKEN_ENV = "MIA_GITHUB_TOKEN"
REPO_ENV = "MIA_GITHUB_REPO"
REPO = "derf1a/Mallot-Invest-Agent"
LABEL = "agent-report"
API = "https://api.github.com"


class GitHubError(Exception):
    pass


def _env(key: str) -> str:
    return (envfile.read().get(key) or os.environ.get(key) or "").strip()


def repo() -> str:
    return _env(REPO_ENV) or REPO


def configured() -> bool:
    return bool(_env(TOKEN_ENV))


def _call(method: str, path: str, body: dict | None = None) -> dict | list:
    token = _env(TOKEN_ENV)
    if not token:
        raise GitHubError(f"{TOKEN_ENV} 가 없어요")
    status, raw = net.HTTP_REQUEST(method, f"{API}/repos/{repo()}{path}",
                                   headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                            "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"},
                                   data=json.dumps(body).encode() if body is not None else None)
    if not 200 <= status < 300:
        try:
            msg = json.loads(raw).get("message") or ""
        except (ValueError, AttributeError):
            msg = raw[:200].decode(errors="replace")
        raise GitHubError(f"GitHub {status}: {msg}" if status else f"GitHub 연결 실패: {msg}")
    return json.loads(raw) if raw else {}


def create_issue(title: str, body: str) -> tuple[int, str]:
    """→ (번호, 주소)."""
    g = _call("POST", "/issues", {"title": title, "body": body, "labels": [LABEL]})
    return int(g["number"]), str(g.get("html_url") or "")


def list_issues() -> list[dict]:
    """앱이 올린(LABEL) 이슈 전부 — issues.sync_github 가 받는 모양으로. 풀 리퀘스트는 뺀다."""
    out: list[dict] = []
    for page in range(1, 6):  # 100개씩 5쪽까지
        rows = _call("GET", f"/issues?state=all&labels={LABEL}&per_page=100&page={page}")
        if not isinstance(rows, list):
            break
        out += [{"number": int(g["number"]), "url": str(g.get("html_url") or ""), "title": str(g.get("title") or ""),
                 "body": str(g.get("body") or ""), "state": str(g.get("state") or "open"), "created_at": str(g.get("created_at") or ""),
                 "closed_at": g.get("closed_at")} for g in rows if "pull_request" not in g]
        if len(rows) < 100:
            break
    return out
