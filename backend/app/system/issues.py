"""시스템 이슈 — 에이전트들이 알린 개발·시스템 문제의 기록. 보고서·비서 대화에서 빼고 여기로 모은다.

- 요청(system_reports): 에이전트의 '시스템: …' 문단, 회의록의 system_notes, 비서의 report_system_issue. 같은 말은 한 줄로 세고(key), 시스템 관리자가
  이슈에 잇거나 답하거나 버린다(agent.py).
- 이슈(system_issues): GitHub 이슈와 짝(number). 사건(system_issue_events)이 이력 — 설정 > 시스템 이슈가 보여 준다.
- brief: 다른 에이전트 프롬프트에 넣는 글. 이미 접수된 문제를 다시 묻지 않게.
- is_note·strip_notes: 보고서·회의 요약에서 이런 말을 가려낸다. scrub: GitHub 에 올리기 전에 금액·수량·계좌 형식을 지운다.
"""

import hashlib
import re
import sqlite3
from datetime import datetime

PROFILE = {"key": "system", "title": "시스템 관리자", "name": "마테우스", "full_name": "Mateus Costa", "country": "브라질", "pokemon": 137,
           "role": "에이전트들이 알린 시스템 문제를 GitHub 이슈로 정리하고, 고쳐졌는지 저장소를 확인해요"}
BRIEF_OPEN = 8
BRIEF_DONE = 4
LINE = 110
# 투자가 아니라 시스템·개발에 관한 말 — 보고서에 넣지 않는다
NOTE = re.compile(r"^\W*관리자 확인|시스템 확인|게이트.{0,30}(닫혔는데|닫혔지만|우회|넘을 수)|게이트 충돌에도|경위를 확인|상태 open|도구 (오류|결과가 다)"
                  r"|get_[a-z_]+|버그|회의록.{0,8}(오류|틀)|재시작|답변이 기록되지")
_MONEY = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:만\s*|억\s*)?원")
_QTY = re.compile(r"\d[\d,]*\s*주(?![기차간째일년])")
_ACCOUNT = re.compile(r"\d{2,}-\d{2,}-\d{2,}|\b\d{9,}\b")


def is_note(text: str | None) -> bool:
    return bool(NOTE.search(text or ""))


def strip_notes(text: str | None) -> str:
    """글에서 시스템 이야기를 담은 문장만 뺀다."""
    return " ".join(p for p in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if p and not is_note(p))


def strip_headline(text: str | None) -> str:
    return " — ".join(p for p in (text or "").split(" — ") if not is_note(p))


def scrub(text: str) -> str:
    """GitHub 에 올리는 글에서 개인 운용 정보 형식을 지운다 (비공개 저장소여도)."""
    return _QTY.sub("[수량]", _MONEY.sub("[금액]", _ACCOUNT.sub("[번호]", text)))


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _key(text: str) -> str:
    return hashlib.sha1(re.sub(r"[\d\W_]+", "", text).encode()).hexdigest()


def report(conn: sqlite3.Connection, text: str, by: str, source: str = "channel", now: datetime | None = None, recount: bool = True) -> dict | None:
    """요청을 받는다 — 같은 말이면 세기만 한다. recount=False 면 이미 받은 말을 다시 세지 않는다(회의록을 다시 읽을 때)."""
    text = (text or "").strip()[:1000]
    if not text:
        return None
    at, key = _stamp(now), _key(text)
    row = conn.execute("SELECT * FROM system_reports WHERE key = ?", (key,)).fetchone()
    if row:
        if recount:
            conn.execute("UPDATE system_reports SET seen = seen + 1, last_at = ? WHERE id = ?", (at, row["id"]))
            if row["issue_id"]:
                conn.execute("UPDATE system_issues SET reports = reports + 1, updated_at = ? WHERE id = ?", (at, row["issue_id"]))
            conn.commit()
        return dict(row)
    cur = conn.execute("INSERT INTO system_reports (at, last_at, by, source, text, key) VALUES (?, ?, ?, ?, ?, ?)", (at, at, by, source, text, key))
    conn.commit()
    return {"id": cur.lastrowid}


def pending(conn: sqlite3.Connection, limit: int) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM system_reports WHERE status = 'pending' ORDER BY seen DESC, id LIMIT ?", (limit,))]


def event(conn: sqlite3.Connection, issue_id: int, kind: str, text: str, who: str, now: datetime | None = None) -> None:
    conn.execute("INSERT INTO system_issue_events (issue_id, at, kind, who, text) VALUES (?, ?, ?, ?, ?)", (issue_id, _stamp(now), kind, who, text[:500]))


def create(conn: sqlite3.Connection, title: str, body: str, by: str, now: datetime | None = None) -> int:
    at = _stamp(now)
    cur = conn.execute("INSERT INTO system_issues (title, body, by, created_at, updated_at) VALUES (?, ?, ?, ?, ?)", (title[:120], body[:2000], by, at, at))
    event(conn, cur.lastrowid, "opened", f"{by} 요청으로 등록", PROFILE["name"], now)
    conn.commit()
    return cur.lastrowid


def link(conn: sqlite3.Connection, report_id: int, issue_id: int, now: datetime | None = None) -> None:
    row = conn.execute("SELECT seen, by FROM system_reports WHERE id = ?", (report_id,)).fetchone()
    conn.execute("UPDATE system_reports SET status = 'linked', issue_id = ? WHERE id = ?", (issue_id, report_id))
    conn.execute("UPDATE system_issues SET reports = reports + ?, updated_at = ? WHERE id = ?", (row["seen"], _stamp(now), issue_id))
    event(conn, issue_id, "reported", f"{row['by']} 요청 {row['seen']}건 연결", PROFILE["name"], now)
    conn.commit()


def close_report(conn: sqlite3.Connection, report_id: int, status: str, answer: str = "") -> None:
    """이슈가 아닌 요청 — answered(시스템 관리자가 답함)·ignored."""
    conn.execute("UPDATE system_reports SET status = ?, answer = ? WHERE id = ?", (status, answer[:500], report_id))
    conn.commit()


def set_number(conn: sqlite3.Connection, issue_id: int, number: int, url: str, now: datetime | None = None) -> None:
    conn.execute("UPDATE system_issues SET number = ?, url = ?, updated_at = ? WHERE id = ?", (number, url, _stamp(now), issue_id))
    event(conn, issue_id, "published", f"GitHub 이슈 #{number} 등록", PROFILE["name"], now)
    conn.commit()


def resolve(conn: sqlite3.Connection, issue_id: int, resolution: str, who: str, now: datetime | None = None) -> None:
    at = _stamp(now)
    conn.execute("UPDATE system_issues SET status = 'resolved', resolution = ?, resolved_at = ?, updated_at = ? WHERE id = ?", (resolution[:500], at, at, issue_id))
    event(conn, issue_id, "resolved", resolution, who, now)
    conn.commit()


def reopen(conn: sqlite3.Connection, issue_id: int, why: str, who: str, now: datetime | None = None) -> None:
    conn.execute("UPDATE system_issues SET status = 'open', resolved_at = NULL, updated_at = ? WHERE id = ?", (_stamp(now), issue_id))
    event(conn, issue_id, "reopened", why, who, now)
    conn.commit()


def sync_github(conn: sqlite3.Connection, g: dict, now: datetime | None = None) -> str | None:
    """GitHub 이슈 하나를 맞춘다 → 바뀐 것 ('imported'·'resolved'·'reopened')."""
    closed = g["state"] == "closed"
    row = conn.execute("SELECT id, status, url FROM system_issues WHERE number = ?", (g["number"],)).fetchone()
    if row is None:
        created = g["created_at"][:19].replace("T", " ")
        done = g["closed_at"][:19].replace("T", " ") if closed and g.get("closed_at") else None
        cur = conn.execute("INSERT INTO system_issues (number, url, title, body, status, by, created_at, updated_at, resolved_at, resolution) "
                           "VALUES (?, ?, ?, ?, ?, 'GitHub', ?, ?, ?, ?)",
                           (g["number"], g["url"], g["title"][:120], g["body"][:2000], "resolved" if closed else "open", created, created, done,
                            "GitHub 에서 닫힘" if closed else ""))
        event(conn, cur.lastrowid, "imported", "GitHub 이슈를 가져옴", PROFILE["name"], now)
        conn.commit()
        return "imported"
    if g["url"] and row["url"] != g["url"]:  # 저장소·계정 이름이 바뀌면 GitHub 가 주는 주소도 바뀐다
        conn.execute("UPDATE system_issues SET url = ? WHERE id = ?", (g["url"], row["id"]))
        conn.commit()
    if closed and row["status"] == "open":
        resolve(conn, row["id"], "GitHub 에서 닫힘", "GitHub", now)
        return "resolved"
    if not closed and row["status"] == "resolved":
        reopen(conn, row["id"], "GitHub 에서 다시 열림", "GitHub", now)
        return "reopened"
    return None


def open_issues(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM system_issues WHERE status = 'open' ORDER BY id")]


def unpublished(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM system_issues WHERE number IS NULL AND status = 'open' ORDER BY id")]


def get(conn: sqlite3.Connection, issue_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM system_issues WHERE id = ?", (issue_id,)).fetchone()
    return dict(row) if row else None


def listing(conn: sqlite3.Connection, status: str | None = None, limit: int = 200) -> list[dict]:
    where, args = ("WHERE status = ?", [status]) if status else ("", [])
    return [dict(r) for r in conn.execute(f"SELECT * FROM system_issues {where} ORDER BY status = 'resolved', updated_at DESC LIMIT ?", (*args, limit))]


def detail(conn: sqlite3.Connection, issue_id: int) -> dict | None:
    row = get(conn, issue_id)
    if row is None:
        return None
    return {**row, "events": [dict(r) for r in conn.execute("SELECT * FROM system_issue_events WHERE issue_id = ? ORDER BY id", (issue_id,))],
            "requests": [dict(r) for r in conn.execute("SELECT at, last_at, by, source, text, seen FROM system_reports WHERE issue_id = ? ORDER BY id", (issue_id,))]}


def counts(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT COALESCE(SUM(status = 'open'), 0) AS open, COALESCE(SUM(status = 'resolved'), 0) AS resolved FROM system_issues").fetchone()
    waiting = conn.execute("SELECT COUNT(*) FROM system_reports WHERE status = 'pending'").fetchone()[0]
    return {"open": row["open"], "resolved": row["resolved"], "pending": waiting}


def answers(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """시스템 관리자가 이슈로 만들지 않고 답한 요청."""
    return [dict(r) for r in conn.execute("SELECT id, at, last_at, by, text, answer, seen FROM system_reports WHERE status = 'answered' ORDER BY id DESC LIMIT ?", (limit,))]


def brief(conn: sqlite3.Connection) -> str:
    """에이전트 프롬프트에 넣는 글 — 비어 있으면 ''."""
    opens = conn.execute("SELECT * FROM system_issues WHERE status = 'open' ORDER BY updated_at DESC LIMIT ?", (BRIEF_OPEN,)).fetchall()
    done = conn.execute("SELECT * FROM system_issues WHERE status = 'resolved' AND by != 'GitHub' ORDER BY resolved_at DESC LIMIT ?", (BRIEF_DONE,)).fetchall()
    said = answers(conn, BRIEF_DONE)
    if not opens and not done and not said:
        return ""
    name = lambda x: f"#{x['number']}" if x["number"] else f"접수 {x['id']}"  # noqa: E731
    lines = [f"[시스템 이슈 — 시스템 관리자 {PROFILE['name']}가 맡습니다. 아래 건은 이미 접수됐으니 다시 요청하지 말고, 관리자에게도 묻지 마세요]"]
    lines += [f"- 처리 중 {name(x)} {x['title'][:LINE]}" for x in opens]
    lines += [f"- 해결 {name(x)} {x['title'][:LINE]} — {x['resolution'][:LINE]}" for x in done]
    lines += [f"- 답변: {x['text'][:60]} → {x['answer'][:LINE]}" for x in said]
    return "\n".join(lines) + "\n"
