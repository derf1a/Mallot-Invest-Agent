"""시스템 관리자(마테우스) — 에이전트들이 알린 시스템 문제(issues.system_reports)를 정리한다.

1) 정리(triage, LLM 한 번): 대기 요청을 지금 열린 이슈와 함께 보고, 요청마다 하나를 고른다 —
   issue(새 이슈, 같은 문제는 한 이슈로 묶음) · link(이미 열린 이슈와 같은 문제) · answer(결함이 아님 — 설명으로 답함) · ignore(시스템 문제가 아님).
   코드가 검사한다: 대기 중인 요청 id 만, 열린 이슈 id 만, 한 요청은 한 번만. LLM 이 실패하면 아무것도 바꾸지 않고 다음에 다시.
2) 올리기(publish): GitHub 토큰이 있으면 아직 안 올린 이슈를 올린다(github.py, issues.scrub 로 금액·수량·계좌 형식을 지움).
3) 맞추기(sync): 앱이 올린 GitHub 이슈의 열림·닫힘을 읽어 온다 — 닫히면 해결, 다시 열리면 다시 열림.
answer·해결은 issues.brief 로 다른 에이전트 프롬프트에 들어가 같은 요청을 되풀이하지 않게 한다.
서비스(service.py)가 주기적으로 run 을 부르고, 화면(설정 > 시스템 이슈)에서 지금 정리할 수도 있다.
"""

import asyncio
import sqlite3
import threading
from collections.abc import Awaitable, Callable

from app.llm.client import LLMError, structured
from app.llm.routes import as_agent
from app.system import github, issues

KEY = "system"
FEATURE = "system_admin"
BATCH = 30          # 한 번에 보는 대기 요청 수
OPEN_SHOWN = 30     # 프롬프트에 넣는 열린 이슈 수
ACTIONS = ("issue", "link", "answer", "ignore")

SCHEMA = {
    "type": "object",
    "properties": {"actions": {"type": "array", "items": {"type": "object", "properties": {
        "report_ids": {"type": "array", "items": {"type": "integer"}},
        "action": {"type": "string", "enum": list(ACTIONS)},
        "issue_id": {"type": "integer"},
        "title": {"type": "string"},
        "body": {"type": "string"},
        "answer": {"type": "string"},
    }, "required": ["report_ids", "action"]}}},
    "required": ["actions"],
}

SYSTEM = f"""당신은 Mallot Invest 의 시스템 관리자 {issues.PROFILE['name']}({issues.PROFILE['full_name']})입니다. 투자 에이전트들이 일하다가
도구 오류·데이터 불일치·기록 이상을 알려 오면, 그 요청을 개발 이슈로 정리합니다. 투자 판단은 하지 않습니다.
요청마다 하나를 고르세요(report_ids 로 묶을 수 있음):
- issue: 고쳐야 할 결함. 같은 원인으로 보이는 요청은 한 이슈로 묶으세요. title 은 한국어 한 줄(무엇이 어떻게 틀리는지, 도구 이름 포함),
  body 는 '현상 / 예 / 확인할 곳' 순서로 짧게. 확인할 곳에는 도구 이름(get_valuation·compare_candidates 등)이나 기능을 적으세요.
- link: 이미 열린 이슈와 같은 문제 → issue_id 에 그 이슈 번호(목록의 id).
- answer: 결함이 아님 — 정상 동작이나 원래 자료의 한계로 설명되면 answer 에 짧은 설명(에이전트들이 다음에 읽습니다).
  예: '서버가 재시작되어 중단됨'으로 끝난 실행은 재시작 때문이고, 그동안 낸 주문은 그대로 유효합니다.
- ignore: 시스템 문제가 아닌 말(투자 의견·관리자에게 하는 질문)이나 이미 해결된 것.
GitHub 저장소는 공개입니다. title·body 에 관리자의 보유 종목·매매 내역(무엇을 샀다·팔았다)·금액·수량·계좌·손익을 쓰지 마세요.
문제를 보여 줄 종목은 코드와 이름만 쓰면 됩니다(예: 018260 삼성에스디에스). 요청에 없는 사실은 지어내지 마세요.
응답은 JSON 스키마({{"actions": [...]}})를 따르세요."""

Llm = Callable[[str, str, str, dict, str | None], Awaitable[dict]]


async def _default_llm(feature: str, system: str, prompt: str, schema: dict, model: str | None) -> dict:
    return await structured(feature, prompt, schema, model=model, system_prompt=system)


LLM: Llm = _default_llm  # 테스트에서 가짜로 바꾼다
_lock = threading.Lock()  # 서비스와 화면이 동시에 정리하지 않게


def prompt(reports: list[dict], opens: list[dict]) -> str:
    lines = ["[대기 요청]"]
    for r in reports:
        times = f" · {r['seen']}번" if r["seen"] > 1 else ""
        lines.append(f"- 요청 {r['id']} · {r['by']} · {r['at'][5:16]}{times}: {r['text']}")
    lines.append("\n[열린 이슈]" if opens else "\n[열린 이슈] 없음")
    for x in opens:
        num = f" (#{x['number']})" if x["number"] else ""
        lines.append(f"- 이슈 {x['id']}{num}: {x['title']} — {x['body'][:200]}")
    return "\n".join(lines)


def apply(conn: sqlite3.Connection, raw: dict, reports: list[dict]) -> dict:
    """LLM 결정을 검사해 반영한다 → 한 일 수. 잘못된 항목은 건너뛰고, 다루지 않은 요청은 대기로 남는다."""
    waiting = {r["id"]: r for r in reports}
    opens = {x["id"] for x in issues.open_issues(conn)}
    done = {"issue": 0, "link": 0, "answer": 0, "ignore": 0, "skipped": 0}
    for a in (raw or {}).get("actions") or []:
        if not isinstance(a, dict):
            continue
        ids = [i for i in dict.fromkeys(a.get("report_ids") or []) if isinstance(i, int) and i in waiting]
        act = a.get("action")
        if not ids or act not in ACTIONS:
            done["skipped"] += 1
            continue
        if act == "issue":
            title = issues.strip_headline(str(a.get("title") or "")).strip()[:120] or waiting[ids[0]]["text"][:80]
            body = str(a.get("body") or "").strip() or "\n".join(f"- {waiting[i]['text']}" for i in ids)
            target = issues.create(conn, title, body, waiting[ids[0]]["by"])
            opens.add(target)
        elif act == "link":
            target = a.get("issue_id")
            if target not in opens:
                done["skipped"] += 1
                continue
        else:
            for i in ids:
                issues.close_report(conn, i, "answered" if act == "answer" else "ignored", str(a.get("answer") or "")[:500])
                waiting.pop(i)
            done[act] += len(ids)
            continue
        for i in ids:
            issues.link(conn, i, target)
            waiting.pop(i)
        done[act] += 1 if act == "issue" else len(ids)
    return done


def triage(conn: sqlite3.Connection, model: str | None = None) -> dict:
    reports = issues.pending(conn, BATCH)
    if not reports:
        return {"pending": 0}
    opens = issues.open_issues(conn)[-OPEN_SHOWN:]
    try:
        with as_agent(KEY):
            raw = asyncio.run(LLM(FEATURE, SYSTEM, prompt(reports, opens), SCHEMA, model))
    except (LLMError, TypeError, ValueError) as e:
        return {"pending": len(reports), "error": f"정리 실패: {e}"[:300]}
    return {"pending": len(reports), **apply(conn, raw, reports)}


def footer(issue: dict, conn: sqlite3.Connection) -> str:
    who = sorted({r["by"] for r in conn.execute("SELECT by FROM system_reports WHERE issue_id = ?", (issue["id"],))})
    return (f"\n\n---\n에이전트 요청 {issue['reports'] or 1}건 ({', '.join(who) or issue['by']}) · "
            f"시스템 관리자 {issues.PROFILE['name']}가 정리 · 앱 이슈 {issue['id']}")


def publish(conn: sqlite3.Connection) -> dict:
    """아직 안 올린 열린 이슈를 GitHub 에 올린다. 토큰이 없으면 아무것도 하지 않는다."""
    if not github.configured():
        return {"published": 0, "github": False}
    n = 0
    for x in issues.unpublished(conn):
        try:
            number, url = github.create_issue(issues.scrub(x["title"]), issues.scrub(x["body"] + footer(x, conn)))
        except github.GitHubError as e:
            return {"published": n, "github": True, "error": str(e)}
        issues.set_number(conn, x["id"], number, url)
        n += 1
    return {"published": n, "github": True}


def sync(conn: sqlite3.Connection) -> dict:
    if not github.configured():
        return {"synced": 0, "github": False}
    try:
        rows = github.list_issues()
    except github.GitHubError as e:
        return {"synced": 0, "github": True, "error": str(e)}
    changed = [c for g in rows if (c := issues.sync_github(conn, g))]
    return {"synced": len(rows), "changed": len(changed), "github": True}


def run(conn: sqlite3.Connection, *, sync_github: bool = True, model: str | None = None) -> dict:
    """정리 → 올리기 → (sync_github 면) 맞추기. 다른 정리가 돌고 있으면 기다리지 않고 busy."""
    if not _lock.acquire(blocking=False):
        return {"busy": True}
    try:
        out = {"triage": triage(conn, model), "publish": publish(conn)}
        if sync_github:
            out["sync"] = sync(conn)
        return out
    finally:
        _lock.release()
