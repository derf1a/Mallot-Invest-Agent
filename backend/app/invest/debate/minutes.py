"""투자 회의록 — 회의(에이전트 토론 세션)가 끝나면 비서가 정리한다. 매일 정해진 시각의 자동 운용 회의, 비서가 연 회의, 수동 회의 모두.

- 사실(facts): 코드가 세션 결과에서 꺼낸다 — 언제·어떤 회의, 참석자(이름·직함), 후보, 종목별 최종 입장 수·게이트·결정·주문, 에이전트마다
  최종 요지, 입장을 바꾼 수, 관리자 메시지(운용 지시 포함), 다음 회의 시각.
- 요약: 비서(LLM, 기능 invest_assistant)가 그 사실만으로 headline·summary·points·follow_up 을 쓴다. 숫자·주문은 사실에 있는 것만.
  LLM 이 실패하면 코드가 사실로 짧은 회의록을 만든다(fallback) — 회의록이 비지 않게.
- 남는 곳: 산출물 minutes.json, 실행 요약 summary.minutes(회의 내역 목록), 채팅방 끝의 회의록 카드(messages.jsonl type=minutes).
"""

import re
from collections.abc import Awaitable, Callable

from app.llm.client import structured
from app.llm.routes import as_agent

STANCE_KO = {"buy": "매수", "sell": "매도", "none": "의견 없음"}
SIDE_KO = {"buy": "매수", "sell": "매도"}
THESIS_MAX, TRADER_MAX, NOTE_MAX = 500, 600, 400  # 사실에 넣는 글 길이 — 넘으면 clip
GATE_KO = {"aligned": "열림", "conflict": "충돌", "no_view": "모두 의견 없음", "not_held": "보유 없음(사지 말자)", "incomplete": "응답 누락"}

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "points": {"type": "array", "items": {"type": "string"}},
        "follow_up": {"type": "array", "items": {"type": "string"}},
        "system_notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "summary", "points", "follow_up", "system_notes"],
}
SYSTEM = """당신은 투자 회의의 회의록을 쓰는 비서입니다. 주어진 '회의 사실'만 근거로 씁니다.
- headline: 결론 한 줄(예: 'SK하이닉스 매수 2주 체결 · 나머지 관망'). 주문이 없으면 왜 관망했는지.
- summary: 3~5문장 — 누가 어떤 근거로 어느 쪽이었는지, 게이트가 왜 열리거나 닫혔는지, 무엇을 했는지.
- points: 핵심 논점 2~4개(찬성·반대 근거를 이름(직함)과 함께). follow_up: 다음 회의에서 볼 투자 사항 0~3개.
- system_notes: 시스템·도구·데이터·기록 이상은 headline·summary·follow_up 에 쓰지 말고 여기에만(시스템 관리자가 맡습니다, 관리자에게 확인을 요청하지 마세요).
- 사실에 없는 숫자·주문·의견을 지어내지 마세요. 수익을 장담하지 마세요. 한국어, 간결하게."""

def clip(text: object, n: int) -> str:
    """n 자를 넘으면 문장 끝(없으면 띄어쓰기)에서 자르고 '…' — 문장 중간에서 끊긴 글을 회의록에 넘기지 않게."""
    text = str(text or "").strip()
    if len(text) <= n:
        return text
    head = text[:n - 1]
    ends = [m.end() for m in re.finditer(r"[.!?。](?=\s)|\n", head)]
    cut = ends[-1] if ends and ends[-1] >= n // 2 else head.rfind(" ") if head.rfind(" ") >= n // 2 else len(head)
    return head[:cut].rstrip() + "…"


MinutesLlm = Callable[[str, str, str, dict, str | None], Awaitable[dict]]


async def _default_llm(feature: str, system: str, prompt: str, schema: dict, model: str | None) -> dict:
    return await structured(feature, prompt, schema, model=model, system_prompt=system)


LLM: MinutesLlm = _default_llm  # 테스트에서 가짜로 바꾼다


def facts(*, label: str, kind: str, created_at: str, scope: dict, transcript: list[dict], gate: dict, decision: dict,
          orders: list[dict], roster: list[dict], notes: list[dict], schedule: dict | None) -> dict:
    who = {a["key"]: (f"{a['name']}({a['title']})" if a.get("name") else a["title"]) for a in roster}
    final_round = max((t["round"] for t in transcript), default=0)
    finals = [t for t in transcript if t["round"] == final_round]
    names = {c["symbol"]: c["name"] for c in scope["candidates"]}
    per = []
    for sym, name in names.items():
        stances = [next((v["stance"] for v in t.get("views", []) if v["symbol"] == sym), "none") for t in finals if not t.get("error")]
        g = (gate.get("symbols") or {}).get(sym) or {}
        d = next((x for x in decision.get("decisions") or [] if x["symbol"] == sym), None)
        o = next((x for x in orders if x["symbol"] == sym), None)
        per.append({"symbol": sym, "name": name, "counts": {k: stances.count(k) for k in STANCE_KO},
                    "gate": g.get("status"), "gate_reason": g.get("reason"), "decision": d["decision"] if d else "hold",
                    "order": {"side": o["side"], "quantity": o["quantity"], "status": o["status"]} if o else None})
    return {
        "label": label, "kind": kind, "created_at": created_at, "gate_mode": gate.get("mode", "unanimous"),
        "participants": [who[a["key"]] for a in roster],
        "symbols": per,
        "views": [{"who": who.get(t["agent"], t["title"]), "thesis": clip(t.get("thesis"), THESIS_MAX), "error": bool(t.get("error"))}
                  for t in finals],
        "changes": sum(len(t.get("changed_symbols") or []) for t in transcript),
        "trader": clip(decision.get("rationale"), TRADER_MAX),
        "notes": [clip(n["text"], NOTE_MAX) for n in notes][:8],
        "next": schedule.get("note") if schedule else None,
    }


def prompt(f: dict) -> str:
    lines = [f"회의: {f['label']} ({f['kind']}) · {f['created_at']} · 게이트 {'과반' if f['gate_mode'] == 'supermajority' else '만장일치'}",
             "참석: " + ", ".join(f["participants"])]
    for s in f["symbols"]:
        c = s["counts"]
        order = f" · 주문 {SIDE_KO[s['order']['side']]} {s['order']['quantity']:g}주 ({s['order']['status']})" if s["order"] else ""
        lines.append(f"- {s['name']}({s['symbol']}): 매수 {c['buy']} · 매도 {c['sell']} · 의견 없음 {c['none']} → 게이트 "
                     f"{GATE_KO.get(s['gate'] or '', s['gate'] or '-')} ({s['gate_reason'] or ''}) · 결정 {s['decision']}{order}")
    lines.append("에이전트 최종 요지:\n" + "\n".join(f"- {v['who']}: {'(응답 실패)' if v['error'] else v['thesis']}" for v in f["views"]))
    if f["trader"]:
        lines.append(f"트레이더 판단: {f['trader']}")
    if f["notes"]:
        lines.append("관리자 메시지(운용 지시 포함):\n" + "\n".join(f"- {n}" for n in f["notes"]))
    lines.append(f"입장을 바꾼 횟수: {f['changes']}")
    if f["next"]:
        lines.append(f"다음 회의: {f['next']}")
    return "회의 사실:\n" + "\n".join(lines) + "\n\n이 사실만으로 회의록을 쓰세요."


def fallback(f: dict) -> dict:
    """LLM 없이 사실로 만든 짧은 회의록."""
    acts = [f"{s['name']} {SIDE_KO[s['order']['side']]} {s['order']['quantity']:g}주" for s in f["symbols"] if s["order"]]
    opened = [s["name"] for s in f["symbols"] if s["gate"] == "aligned"]
    headline = (" · ".join(acts) if acts else "주문 없음") + (" · 나머지 관망" if acts and len(acts) < len(f["symbols"]) else "")
    summary = (f"{len(f['symbols'])}종목을 논의했어요 (참석: {', '.join(f['participants'])}). "
               + (f"게이트가 열린 종목: {', '.join(opened)}. " if opened else "게이트가 열린 종목이 없어 관망했어요. ")
               + (f"트레이더: {f['trader']}" if f["trader"] else ""))
    return {"headline": headline, "summary": summary.strip(), "points": [], "follow_up": [f["next"]] if f["next"] else [],
            "by": "code"}


def clean(raw: dict, f: dict) -> dict:
    def items(v: object, n: int) -> list[str]:
        return [clip(x, 300) for x in v or [] if clip(x, 300)][:n] if isinstance(v, list) else []

    out = {"headline": clip(raw.get("headline"), 120), "summary": clip(raw.get("summary"), 1200),
           "points": items(raw.get("points"), 4), "follow_up": items(raw.get("follow_up"), 3), "system_notes": items(raw.get("system_notes"), 5), "by": "assistant"}
    return out if out["headline"] and out["summary"] else fallback(f)


async def write(f: dict, model: str | None) -> dict:
    with as_agent("assistant"):
        raw = await LLM("invest_assistant", SYSTEM, prompt(f), SCHEMA, model)
    return clean(raw if isinstance(raw, dict) else {}, f)
