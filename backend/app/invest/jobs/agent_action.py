"""작업: 에이전틱 매매 — 멀티 에이전트 토론 → 종목별 만장일치 방향 게이트 → 트레이더가 주문 제안 (Invest > AI Trading 채팅방).

- 토론 범위: 단일 종목 / 섹터 / 전체(보유 + 관심) → 후보 종목 (scopes.resolve). 라운드·모델·후보 수는 실험실 설정.
- 대화는 진행되는 대로 산출물 폴더의 messages.jsonl 에 한 줄씩 쌓는다 → AI Trading 화면이 채팅방·의사결정 패널로 실시간 표시.
- 관리자(사용자)는 토론 중 채팅으로 의견을 남길 수 있다(post_user_message) → 다음 라운드·트레이더 프롬프트에 들어간다.
- 트레이더 주문은 기본으로 사용자 승인 대기(실험실에서 '승인 생략'을 켜면 바로 제출).
- 자동 운용(params.autopilot, autopilot.py 의 일정이 시작): 후보 = 시장 스캔(경보·지시·보유·발굴 목록·관심·랭킹 → 포트폴리오
  매니저가 리서치 요약과 함께 고름), 운용 지시는 관리자 메시지로 먼저 들어가고, 게이트는 supermajority(과반 + 반대 극소수), 주문은
  승인 없이 제출. 끝나면 일정 담당 LLM 이 다음 판단 시각을 더하거나 옮긴다(코드가 검사해 반영, 채팅방에 schedule 메시지).
- 오늘 운용 데스크 리서치(desk.brief)가 있으면 모든 에이전트의 시스템 프롬프트에 참고 자료로 들어간다.
- 후보에 보유 종목이 있으면 매도 점검(exits.for_candidates — 평단·손익·비중·지표·신호)이 후보 줄에 붙어 에이전트가 매도 타이밍을 따진다.
- 상시 운용(params.stream, 자동 운용 mode=continuous): 토론 한 번 대신 장 내내 도는 채널(stream.py) — 같은 채팅방 형식(대화마다 구분선·
  발언·롤링 게이트·트레이더 결정)으로 쌓이고, 장이 끝나면 하루 회의록. 요약 end = closed(장 끝) | stopped(자동 운용이 꺼지거나 방식 바뀜).
- 한 번에 하나만 돈다(라우터가 막음). 중지를 누르면 감시 작업이 1초 안에 진행 중인 LLM 호출을 끊는다.
기록: 발언마다 로그, 라운드별 매수·매도·의견 없음 수(지표, 후보 전체 합), session.json(끝난 뒤 전체), 주문은 invest_orders.run_id.
"""

import asyncio
import json
import threading
from datetime import datetime

from app.invest import agents, autopilot, desk, minutes, policy, scopes, stream
from app.invest.analysis import exits, screening
from app.invest.marketdata import market
from app.invest.broker import config
from app.invest.marketdata.market import MarketError
from app.invest.broker.toss import TossError
from app.runs.runner import Cancelled, JobError, JobSpec, RunContext, register
from app.llm.client import LLMError
from app.system import issues

DEFAULTS = {"scope": "symbol", "symbol": "", "sector_id": "", "note": "", "rounds": None, "model": None, "autopilot": False}
MESSAGES = "messages.jsonl"

_LIVE: dict[int, "Chat"] = {}  # 진행 중인 세션의 채팅 (run_id → Chat) — 사용자가 토론 중에 남기는 의견을 받는다
_LIVE_LOCK = threading.Lock()


class Chat:
    """messages.jsonl 쓰기 (순번·시각을 붙여 한 줄씩) + 관리자 의견 모음."""

    def __init__(self, ctx: RunContext):
        self.path = ctx.artifact_dir / MESSAGES
        self.seq = 0
        self.lock = threading.Lock()
        self.user_notes: list[dict] = []

    def __call__(self, event: dict) -> dict:
        with self.lock:
            self.seq += 1
            line = {"seq": self.seq, "ts": datetime.now().strftime("%H:%M:%S"), **event}
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
            if event.get("type") == "user":
                self.user_notes.append({"ts": line["ts"][:5], "text": event["text"]})
            return line

    def notes(self) -> list[dict]:
        with self.lock:
            return list(self.user_notes)


def post_user_message(run_id: int, text: str) -> dict | None:
    """진행 중인 세션에 관리자 의견을 남긴다. 이 프로세스에서 돌고 있지 않으면 None."""
    with _LIVE_LOCK:
        chat = _LIVE.get(run_id)
    if chat is None:
        return None
    return chat({"type": "user", "text": text})


def read_messages(artifact_dir, after: int = 0) -> list[dict]:
    path = artifact_dir / MESSAGES
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("seq", 0) > after:
            out.append(m)
    return out


async def _with_cancel_watch(coro, ctx: RunContext):
    """중지 요청을 1초마다 확인해 본 작업(LLM 호출 포함)을 끊는다."""
    task = asyncio.ensure_future(coro)
    while not task.done():
        try:
            ctx.check_cancel()
        except Cancelled:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 — 끊긴 호출의 오류는 무시
                pass
            raise
        await asyncio.wait({task}, timeout=1.0)
    return task.result()


def _log_statement(ctx: RunContext, st: dict) -> None:
    if st.get("error"):
        ctx.log(f"[R{st['round']}] {st['title']}: 응답 실패 — {st['error']}", "warn")
        return
    tag = "실행" if st.get("executor") else f"R{st['round']}"
    views = "; ".join(f"{v['symbol']} {agents.STANCE_KO[v['stance']]}({v['confidence']:.2f})"
                      for v in st.get("views", []) if not v.get("implicit"))
    msg = f"[{tag}] {st['title']}: {views or agents.STANCE_KO.get(st['stance'], '')} — {st['thesis'][:300]}"
    if st.get("changed"):
        msg += " ↺ " + ", ".join(f"{sym} {agents.STANCE_KO[old]}→{agents.STANCE_KO[next(v['stance'] for v in st['views'] if v['symbol'] == sym)]}"
                                for sym, old in st["previous"].items()) + f": {st['reason_for_change'][:200]}"
    ctx.log(msg)


def run(ctx: RunContext, params: dict) -> dict:
    opts = config.agent_options(ctx.conn)
    rounds = max(1, min(int(params.get("rounds") or opts["rounds"]), 3))
    model = str(params.get("model") or opts["model"] or "") or None
    p = market.provider()
    standing: list[dict] = []
    if params.get("autopilot"):
        if not autopilot.get_config(ctx.conn)["enabled"]:
            raise JobError("자동 운용이 꺼져 있어요")
        standing = autopilot.intents(ctx.conn)
        note = str(params.get("note") or "").strip()  # 비서가 받은 즉시 지시 — 그 지시에 나온 종목도 후보로
        params = {**params, "scope": "autopilot", "intents": [i["text"] for i in standing] + ([note] if note else [])}
    if params.get("stream"):
        return _run_stream(ctx, params, p, model, opts, standing)
    try:  # 시장 스캔·자동 운용: 여러 종목을 훑은 표에서 LLM(포트폴리오 매니저 관점)이 회의 후보를 고른다
        scope = scopes.resolve(ctx.conn, p, {**params, "run_id": ctx.run_id}, opts["max_candidates"],
                               pick=lambda rows, text, n, ctx_: screening.llm_pick(rows, text, n, model, context=ctx_))
    except scopes.ScopeError as e:
        raise JobError(str(e)) from None
    if scope.get("screening"):
        sc = scope["screening"]
        ctx.log(f"후보 선정: {sc['pool']}종목을 훑어 {len(sc['picks'])}개 ({'LLM' if sc['by'] == 'llm' else '점수 순'}) — "
                + ", ".join(c["name"] for c in scope["candidates"]))
    roster = agents.get_roster(ctx.conn)
    active = [a for a in roster if a["enabled"]]
    analysts = [a for a in active if not a["executor"]]
    auto = config.agent_auto_approve(ctx.conn)
    ctx.set_artifact(ctx.artifact_dir)
    chat = Chat(ctx)
    with _LIVE_LOCK:
        _LIVE[ctx.run_id] = chat
    try:
        return _run(ctx, params, scope, p, roster, active, analysts, auto, rounds, model, chat, standing)
    finally:
        with _LIVE_LOCK:
            _LIVE.pop(ctx.run_id, None)


def _run(ctx, params, scope, p, roster, active, analysts, auto, rounds, model, chat: Chat, standing: list[dict]) -> dict:
    symbols = [c["symbol"] for c in scope["candidates"]]
    pilot = bool(params.get("autopilot"))
    gate_mode = "supermajority" if pilot else "unanimous"
    label = str(params.get("label") or scope["label"])
    chat({"type": "start", "scope": scope["scope"], "label": label, "name": label,
          "symbol": symbols[0] if len(symbols) == 1 else None, "candidates": scope["candidates"],
          "sector": scope.get("sector"), "notes": scope["notes"], "rounds": rounds, "model": model or "",
          "provider": p.key, "auto_approve": auto or pilot, "gate": gate_mode,
          "autopilot": {"slot": params.get("slot"), "trigger": params.get("trigger"), "agenda": params.get("agenda") or None}
          if pilot else None,
          "agents": [{"key": a["key"], "title": a["title"], "name": a.get("name"), "full_name": a.get("full_name"),
                      "country": a.get("country"), "pokemon": a.get("pokemon"), "executor": a["executor"]} for a in active]})
    if scope.get("screening"):  # 여러 종목을 훑고 고른 과정
        sc = scope["screening"]
        pm = next((a for a in active if a["key"] == "portfolio"), None)
        chat({"type": "screening", "pool": sc["pool"], "rows": sc["rows"][:15], "picks": sc["picks"], "summary": sc["summary"],
              "by": sc["by"], "picker": f"{pm['name']}({pm['title']})" if pm and sc["by"] == "llm" else None})
    for i in standing:  # 자동 운용: 운용 지시를 관리자 메시지로 먼저
        chat({"type": "user", "text": f"[운용 지시{' · 이번만' if i.get('once') else ''}] {i['text']}", "standing": True})
    note = str(params.get("note") or "").strip()[:500]
    if note:
        chat({"type": "user", "text": note})
    ctx.log(f"{scope['label']} · 후보 {', '.join(f'{c['name']}({c['symbol']})' for c in scope['candidates'])} · "
            f"에이전트 {', '.join(a['title'] for a in active)} · {rounds}라운드 · 시세 {p.title} · 실계좌 · "
            f"{'자동 운용 · 과반 게이트 · 승인 없이 제출' if pilot else '승인 생략' if auto else '사용자 승인'}")
    for n in scope["notes"]:
        ctx.log(n, "warn")
    session = agents.Session(conn=ctx.conn, scope=scope, run_id=ctx.run_id, provider=p, on_event=chat,
                             user_notes=chat.notes, gate_mode=gate_mode, auto_submit=pilot,
                             brief=desk.brief(ctx.conn, market.now_kst().date()), policy=policy.text(policy.get(ctx.conn)),
                             agenda=str(params.get("agenda") or ""), system=issues.brief(ctx.conn),
                             holdings=exits.for_candidates(ctx.conn, p, scope["candidates"]))
    counts: dict[int, list[str]] = {}
    spoke: dict[int, int] = {}
    total_steps = len(analysts) * rounds + 1

    def on_statement(st: dict) -> None:
        _log_statement(ctx, st)
        if st.get("executor"):
            return
        spoke[st["round"]] = spoke.get(st["round"], 0) + 1
        counts.setdefault(st["round"], []).extend(v["stance"] for v in st.get("views", []) if not st.get("error"))
        ctx.progress(min(0.95, sum(spoke.values()) / total_steps))
        if spoke[st["round"]] == len(analysts):
            c = counts[st["round"]]
            ctx.metrics(st["round"], buy=c.count("buy"), sell=c.count("sell"), none=c.count("none"))

    try:
        result = asyncio.run(_with_cancel_watch(
            agents.run_session(session, roster, rounds=rounds, model=model, on_statement=on_statement,
                               check_cancel=ctx.check_cancel), ctx))
    except Cancelled:
        chat({"type": "end", "status": "cancelled", "message": "사용자가 토론을 중지했습니다"})
        raise
    except Exception as e:
        chat({"type": "end", "status": "failed", "message": f"토론이 실패했습니다: {e}"[:300]})
        raise
    ctx.log(f"게이트: {result.gate['reason']}")
    d = result.decision
    ctx.log(f"결정: {', '.join(f'{x['name']} {x['decision']} {x['quantity'] or ''}'.strip() for x in d['decisions']) or d['decision']}"
            f" — {d['rationale'][:300]}" + (f" · {d['note']}" if d.get("note") else ""))
    for o in result.orders:
        ctx.log(f"주문 #{o['id']} {o['name']} {o['side']} {o['quantity']:g}주 · {o['status']}")
    for sym, err in result.order_errors.items():
        if not any(o["symbol"] == sym for o in result.orders):
            ctx.log(f"주문 거절 ({sym}): {err}", "warn")
    schedule = _plan_next(ctx, p, model, result, chat) if pilot else None
    if pilot:  # '이번만' 지시는 이 판단에 썼으니 지운다
        autopilot.consume_once(ctx.conn, [i["id"] for i in standing if i.get("once")])
    record = _minutes(ctx, model, label, params, scope, result, active, chat, schedule)
    pending = [o for o in result.orders if o["status"] == "pending_approval"]
    chat({"type": "end", "status": "succeeded",
          "message": f"승인을 기다리는 주문이 {len(pending)}건 있어요" if pending else
          f"자동 판단이 끝났어요 — 주문 {len(result.orders)}건" if pilot else "토론이 끝났어요"})
    (ctx.artifact_dir / "session.json").write_text(json.dumps({
        "scope": scope, "rounds": rounds, "model": model or "", "provider": p.key, "auto_approve": auto or pilot,
        "gate_mode": gate_mode, "autopilot": {"slot": params.get("slot"), "intents": [i["text"] for i in standing],
                                              "schedule": schedule} if pilot else None,
        "roster": active, "user_notes": chat.notes(), "transcript": result.transcript, "gate": result.gate,
        "decision": result.decision, "orders": result.orders, "order_errors": result.order_errors,
        "tool_calls": result.tool_log, "stance_changes": result.stance_changes},
        ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    orders = [{"id": o["id"], "symbol": o["symbol"], "name": o["name"], "side": o["side"], "quantity": o["quantity"],
               "status": o["status"]} for o in result.orders]
    return {"scope": scope["scope"], "label": scope["label"], "name": scope["label"], "symbols": symbols,
            "gate": result.gate["status"], "aligned": result.gate["aligned"], "direction": result.gate["direction"],
            "decision": d["decision"], "quantity": d["quantity"], "decisions": d["decisions"], "orders": orders,
            "order_id": orders[0]["id"] if orders else None, "order_status": orders[0]["status"] if orders else None,
            "stance_changes": result.stance_changes, "tool_calls": len(result.tool_log), "provider": p.key,
            "user_notes": len(chat.notes()), "autopilot": pilot, "gate_mode": gate_mode,
            "next_check": schedule["note"] if schedule else None,
            "minutes": {"headline": record["headline"], "summary": record["summary"], "kind": record["facts"]["kind"]}}


MEETING_KIND = {"fixed": "정기 회의", "extra": "AI 가 더한 회의", "manual": "지금 판단", "assistant": "비서가 연 회의",
                "risk": "수시 회의(리스크 경보)", "stream": "상시 운용 (하루)"}


def _run_stream(ctx: RunContext, params: dict, p, model: str | None, opts: dict, standing: list[dict]) -> dict:
    """상시 운용 채널 — 장이 끝날 때까지 stream.Stream 이 돈다."""
    roster = agents.get_roster(ctx.conn)
    active = [a for a in roster if a["enabled"]]
    ctx.set_artifact(ctx.artifact_dir)
    chat = Chat(ctx)
    with _LIVE_LOCK:
        _LIVE[ctx.run_id] = chat
    try:
        label = "상시 운용"
        chat({"type": "start", "scope": "stream", "label": label, "name": label, "symbol": None, "candidates": [], "sector": None,
              "notes": [], "rounds": 0, "model": model or "", "provider": p.key, "auto_approve": True, "gate": "rolling", "stream": True,
              "autopilot": {"slot": params.get("slot"), "trigger": "stream", "agenda": "정기 회의 없이 장 내내 대화 · 트레이더는 언제든 주문"},
              "agents": [{"key": a["key"], "title": a["title"], "name": a.get("name"), "full_name": a.get("full_name"),
                          "country": a.get("country"), "pokemon": a.get("pokemon"), "executor": a["executor"]} for a in active]})
        for i in standing:
            chat({"type": "user", "text": f"[운용 지시{' · 이번만' if i.get('once') else ''}] {i['text']}", "standing": True})
        note = str(params.get("note") or "").strip()[:500]
        if note:
            chat({"type": "user", "text": note})
        ctx.log(f"상시 운용 시작 · 에이전트 {', '.join(a['title'] for a in active)} · 시세 {p.title} · 실계좌 · 롤링 게이트 · 승인 없이 제출")
        st = stream.Stream(ctx, chat, p, model, roster, opts["max_candidates"],
                           run_async=lambda coro: asyncio.run(_with_cancel_watch(coro, ctx)),
                           pick=lambda rows, text, n, c: screening.llm_pick(rows, text, n, model, context=c))
        try:
            how = st.run([note] if note else [])
        except Cancelled:
            chat({"type": "end", "status": "cancelled", "message": "사용자가 상시 운용을 중지했어요"})
            raise
        except Exception as e:
            chat({"type": "end", "status": "failed", "message": f"상시 운용이 멈췄어요: {e}"[:300]})
            raise
        autopilot.consume_once(ctx.conn, [i["id"] for i in standing if i.get("once")])
        scope, finals, gate, decision = st.minutes_facts()
        result = agents.SessionResult(finals, gate, decision, st.orders, {}, [], 0)
        record = _minutes(ctx, model, label, {**params, "trigger": "stream"}, scope, result, active, chat, None)
        chat({"type": "end", "status": "succeeded",
              "message": f"{'장이 끝나' if how == 'closed' else '자동 운용이 꺼지거나 방식이 바뀌어'} 상시 운용을 마쳐요 — 대화 {st.n}번 · 주문 "
                         f"{len(st.orders)}건"})
        (ctx.artifact_dir / "session.json").write_text(json.dumps({
            "scope": scope, "stream": True, "end": how, "talks": st.n, "board": {a: {s_: {**v, "at": v["at"].isoformat()}
                                                                                   for s_, v in vs.items()} for a, vs in st.board.views.items()},
            "roster": active, "user_notes": chat.notes(), "transcript": st.transcript, "decisions": st.decisions, "orders": st.orders},
            ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        orders = [{"id": o["id"], "symbol": o["symbol"], "name": o["name"], "side": o["side"], "quantity": o["quantity"],
                   "status": o["status"]} for o in st.orders]
        return {"scope": "stream", "label": label, "name": label, "symbols": [c["symbol"] for c in scope["candidates"]],
                "gate": "aligned" if orders else "no_view", "aligned": [], "direction": None, "decision": decision.get("decision", "hold"),
                "quantity": 0, "decisions": decision["decisions"], "orders": orders, "order_id": orders[0]["id"] if orders else None,
                "order_status": orders[0]["status"] if orders else None, "stance_changes": 0, "tool_calls": 0, "provider": p.key,
                "user_notes": len(chat.notes()), "autopilot": True, "gate_mode": "rolling", "stream": True, "end": how, "talks": st.n,
                "next_check": None,
                "minutes": {"headline": record["headline"], "summary": record["summary"], "kind": record["facts"]["kind"]}}
    finally:
        with _LIVE_LOCK:
            _LIVE.pop(ctx.run_id, None)


def _minutes(ctx: RunContext, model: str | None, label: str, params: dict, scope: dict, result: agents.SessionResult,
             active: list[dict], chat: Chat, schedule: dict | None) -> dict:
    """회의록 — 코드가 사실을 꺼내고 비서가 요약한다(실패하면 코드가 정리). minutes.json + 채팅방 회의록 카드."""
    f = minutes.facts(label=label, kind=MEETING_KIND.get(str(params.get("trigger") or ""), "수동 회의"),
                      created_at=datetime.now().strftime("%Y-%m-%d %H:%M"), scope=scope, transcript=result.transcript,
                      gate=result.gate, decision=result.decision, orders=result.orders, roster=active, notes=chat.notes(),
                      schedule=schedule)
    try:
        doc = asyncio.run(minutes.write(f, model))
    except (LLMError, TypeError, ValueError) as e:
        ctx.log(f"비서가 회의록을 쓰지 못해 코드로 정리했어요: {e}", "warn")
        doc = minutes.fallback(f)
    doc["facts"] = f
    if params.get("trigger") != "stream":  # 상시 운용은 말할 때마다 받았다
        for by, text in stream.system_asks(result.transcript):
            issues.report(ctx.conn, text, by)
    (ctx.artifact_dir / "minutes.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    chat({"type": "minutes", **{k: doc[k] for k in ("headline", "summary", "points", "follow_up", "by")}, "kind": f["kind"]})
    ctx.log(f"회의록: {doc['headline']}")
    return doc


def _plan_next(ctx: RunContext, p, model: str | None, result: agents.SessionResult, chat: Chat) -> dict:
    """자동 운용: 일정 담당 LLM 이 다음 판단 시각을 정하고(add·move·none) 코드가 검사해 반영한다. 실패해도 세션은 그대로 끝난다."""
    now = market.now_kst()
    try:
        window = autopilot.trading_window(p)
    except (MarketError, TossError):
        window = None
    d = result.decision
    summary = (f"게이트: {result.gate['reason']}\n결정: "
               + (", ".join(f"{x['name']} {x['decision']} {x['quantity'] or ''}".strip() for x in d["decisions"]) or d["decision"])
               + f" — {d['rationale'][:300]}\n주문: "
               + (", ".join(f"{o['name']} {o['side']} {o['quantity']:g}주 {o['status']}" for o in result.orders) or "없음"))
    proposal: dict = {}
    if window:
        try:
            proposal = asyncio.run(autopilot.plan_next(ctx.conn, now, window, summary, model))
        except (LLMError, TypeError, ValueError) as e:
            ctx.log(f"다음 판단 시각을 정하지 못했어요: {e}", "warn")
    res = autopilot.apply_proposal(ctx.conn, proposal, now, window, ctx.run_id)
    ctx.log(f"다음 판단: {res['note']}" + (f" — {res['reason']}" if res.get("reason") else ""),
            "info" if res["accepted"] or res["action"] == "none" else "warn")
    chat({"type": "schedule", **res})
    return res


register(JobSpec(
    kind="agent_action",
    title="에이전틱 매매",
    description="성향·도구가 다른 에이전트들이 토론하고, 모두 한 방향일 때만 트레이더가 주문을 제안합니다 (기본: 사용자 승인). "
                "자동 운용은 운용 지시·보유로 정해진 시각에 돌고, 과반 + 반대 극소수일 때 승인 없이 주문합니다.",
    run=run,
    defaults=DEFAULTS,
    uses_llm=True,
))
