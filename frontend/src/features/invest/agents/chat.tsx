/**
 * AI 트레이딩 채팅방 — 카카오톡 단체방처럼: 에이전트마다 프로필·이름·말풍선(반박·동의는 답장 인용), 관리자(나)는 오른쪽 노란 말풍선.
 * 관리자는 말할 일이 적어서 오른쪽을 행동 의견에 쓴다: 매수·매도 의견(트레이더 주문 제안 포함)은 오른쪽, 의견 없음·관망·응답 실패는 왼쪽.
 * 진행(방 열림 → 라운드 → 라운드 정리 → 게이트 → 트레이더 → 승인)은 대화 사이의 가운데 안내 줄·카드로 넣고,
 * 주문 승인 카드는 트레이더 말풍선 아래에, 승인 대기는 위쪽 공지로 고정한다.
 * 채팅 상태(session.ts 의 useChat)는 화면(AI Trading)이 들고 채팅방과 토론 현황 서랍이 같이 쓴다.
 */
import { Fragment, useEffect, useRef, useState } from 'react'
import { isActive, STATUS_LABEL } from '../../runs/live'
import type { Run } from '../../runs/api'
import { investApi, type AdviceStatus, type Agent, type AssistantMessage, type Candidate, type ChatMessage, type Order, type SessionOrder,
  type View } from '../api'
import { DeskCard, ScreeningCard } from './Desk'
import { AgentAvatar, NameLine } from './Face'
import { nameOf, withNames, type Person } from './people'
import ReportCard from './Report'
import { ORDER_STATUS, price, STANCE_LABEL, won } from '../format'
import { candidatesOf, chatDate, chatTime, findLast, GATE_SHORT, GATE_TEXT, stanceLabel, tallyRound, type Chat, type Tally } from './session'

type Msg<T extends ChatMessage['type']> = Extract<ChatMessage, { type: T }>
type Statement = Msg<'statement'>
type AgentInfo = Msg<'start'>['agents'][number]
interface Quote { who: string; text: string }

type Item =
  | { kind: 'date'; key: string; text: string }
  | { kind: 'start'; key: string; m: Msg<'start'> }
  | { kind: 'round'; key: string; m: Msg<'round'>; total: number }
  | { kind: 'summary'; key: string; round: number; tally: Tally[]; spoke: number; changed: number; failed: number }
  | { kind: 'statement'; key: string; m: Statement; tools: string[]; quotes: (Quote | null)[] }
  | { kind: 'user'; key: string; m: Msg<'user'> }
  | { kind: 'gate'; key: string; m: Msg<'gate'> }
  | { kind: 'decision'; key: string; m: Msg<'decision'> }
  | { kind: 'orders'; key: string }
  | { kind: 'minutes'; key: string; m: Msg<'minutes'> }
  | { kind: 'screening'; key: string; m: Msg<'screening'> }
  | { kind: 'pill'; key: string; text: string; tone?: 'warn' }

/** 받침이 있으면 '이', 없으면 '가' */
const iGa = (word: string) => {
  const code = word.charCodeAt(word.length - 1) - 0xac00
  return code >= 0 && code < 11172 && code % 28 ? '이' : '가'
}

/** 메시지 → 채팅방 줄. 도구 조회는 그 에이전트의 다음 발언 아래 한 줄로 접고, 아직 말하지 않은 에이전트의 조회는 working 으로 돌려준다. */
function build(messages: ChatMessage[], run: Run | null, candidates: Candidate[]) {
  const items: Item[] = []
  if (run) items.push({ kind: 'date', key: 'date', text: chatDate(run.created_at) })
  const start = findLast(messages, 'start')
  const total = start?.rounds ?? 0
  let round: Msg<'round'> | null = null
  let said: Statement[] = []
  const earlier = new Map<string, string>() // 지난 라운드까지 에이전트별 마지막 주장 — 답장 인용
  let lastUser: string | null = null
  const working = new Map<string, string[]>()
  let decided = false

  for (const m of messages) {
    switch (m.type) {
      case 'start':
        items.push({ kind: 'start', key: `s${m.seq}`, m })
        break
      case 'round':
        // 앞 라운드 정리 (마지막 라운드는 게이트 카드가 정리한다)
        if (round && said.length) {
          items.push({ kind: 'summary', key: `sum${round.round}`, round: round.round, tally: tallyRound(said, round.candidates ?? candidates),
            spoke: said.filter((s) => !s.error).length, changed: said.filter((s) => s.changed).length, failed: said.filter((s) => s.error).length })
        }
        for (const s of said) if (!s.error) earlier.set(s.title, s.thesis)
        said = []
        working.clear()
        round = m
        items.push({ kind: 'round', key: `r${m.seq}`, m, total })
        break
      case 'tool': {
        const list = working.get(m.agent) ?? []
        if (!list.includes(m.tool_title)) list.push(m.tool_title)
        working.set(m.agent, list)
        break
      }
      case 'statement': {
        const quotes = m.responses.map((r): Quote | null => {
          if (r.to === '관리자') return lastUser ? { who: '나', text: lastUser } : null
          const text = earlier.get(r.to)
          return text ? { who: r.to, text } : null
        })
        items.push({ kind: 'statement', key: `m${m.seq}`, m, tools: working.get(m.agent) ?? [], quotes })
        working.delete(m.agent)
        if (!m.executor) said.push(m)  // 상시 운용 트레이더 점검 발언은 입장 집계에서 뺀다
        break
      }
      case 'user':
        lastUser = m.text
        items.push({ kind: 'user', key: `u${m.seq}`, m })
        break
      case 'gate':
        items.push({ kind: 'gate', key: `g${m.seq}`, m })
        break
      case 'trader_start':
        working.clear()
        items.push({ kind: 'pill', key: `t${m.seq}`, text: `${m.title}${iGa(m.title)} 계좌·한도·호가를 확인하고 있어요` })
        break
      case 'decision':
        decided = true
        if (m.decision.by === 'gate') items.push({ kind: 'pill', key: `d${m.seq}`, text: '열린 종목이 없어 트레이더는 주문하지 않아요' })
        else items.push({ kind: 'decision', key: `d${m.seq}`, m })
        break
      case 'minutes':
        items.push({ kind: 'minutes', key: `mn${m.seq}`, m })
        break
      case 'screening':
        items.push({ kind: 'screening', key: `sc-${m.seq}`, m })
        break
      case 'schedule':
        items.push({ kind: 'pill', key: `sc${m.seq}`, text: `⏱ 다음 판단 · ${m.note}${m.reason ? ` — ${m.reason}` : ''}`,
          tone: m.accepted || m.action === 'none' ? undefined : 'warn' })
        break
      case 'end':
        items.push({ kind: 'pill', key: `e${m.seq}`, text: m.message, tone: m.status === 'succeeded' ? undefined : 'warn' })
        break
      default:
        break // 주문(order)은 최신 상태로 승인 카드에서 보여준다
    }
  }
  if (!decided) items.push({ kind: 'orders', key: 'orders' })
  return { items, working }
}

/** 채팅방 — 위로 올려 읽는 중이면 따라 내려가지 않고 아래에 마지막 메시지 미리 보기를 띄운다.
 * after: 이 토론이 시작된 뒤의 비서 대화 — 채팅방 끝에 이어서 (thinking = 비서가 답을 만드는 중) */
export function ChatRoom({ chat, onFinished, onOrdersChanged, after = [], thinking = false, onOpenSession, roster, assistant, startAtTop = false,
  advice, onChoose }: {
  chat: Chat
  onFinished: () => void
  onOrdersChanged: () => void
  after?: AssistantMessage[]
  thinking?: boolean
  onOpenSession?: (id: number) => void
  /** 지금 명단 — 예전 세션(시작 메시지에 이름이 없음)에 이름을 채운다 */
  roster?: Agent[]
  assistant?: Person
  /** 처음(방 열림)부터 보여 준다 — 아니면 맨 아래(마지막 말)를 따라간다 */
  startAtTop?: boolean
  advice?: AdviceStatus | null
  onChoose?: (text: string) => void
}) {
  const { messages, run, orders, reload } = chat
  const boxRef = useRef<HTMLDivElement>(null)
  const [atBottom, setAtBottom] = useState(!startAtTop)
  const [seenCount, setSeenCount] = useState(0) // 위로 올라가던 순간까지 본 메시지 수
  const finished = !!run && !isActive(run.status)
  const count = messages.length + orders.length + after.length + (thinking ? 1 : 0)
  const unseen = atBottom ? 0 : Math.max(0, count - seenCount)

  useEffect(() => {
    if (finished) onFinished()
  }, [finished, onFinished])

  // 맨 아래에 있을 때만 새 메시지를 따라 내려간다 — '입력 중'이 답으로 바뀌면 수는 같아서 마지막 말·카드까지 본다
  const tail = `${count}|${after.length ? after[after.length - 1].id : ''}|${messages.length ? messages[messages.length - 1].seq : ''}|${thinking}`
  useEffect(() => {
    const el = boxRef.current
    if (el && atBottom) el.scrollTop = el.scrollHeight
  }, [tail, atBottom])

  const toBottom = () => boxRef.current?.scrollTo({ top: boxRef.current.scrollHeight, behavior: 'smooth' })

  const start = findLast(messages, 'start')
  const agents = withNames(start?.agents ?? [], roster)
  const agentOf = (key?: string) => agents.find((a) => a.key === key)
  const personOf = (key?: string, title?: string): Person => agentOf(key) ?? { key: key ?? title ?? 'agent', title: title ?? '에이전트' }
  const nameByTitle = (title: string) => nameOf(agents.find((a) => a.title === title) ?? { key: title, title })
  const executor = agents.find((a) => a.executor)
  const candidates = candidatesOf(messages, run)
  const names: Record<string, string> = Object.fromEntries(candidates.map((c) => [c.symbol, c.name]))
  const { items, working } = build(messages, run, candidates)
  const pending = orders.filter((o) => o.status === 'pending_approval')
  const ordersChanged = () => {
    reload()
    onOrdersChanged()
  }

  // 지금 누가 말하는 중인지 (입력 중 말풍선)
  let typing: AgentInfo[] = []
  if (run && !finished) {
    const lastRound = findLast(messages, 'round')
    const traderTurn = messages.some((m) => m.type === 'trader_start') && !messages.some((m) => m.type === 'decision')
    if (traderTurn) typing = executor ? [executor] : []
    else if (lastRound && !messages.some((m) => m.type === 'gate')) {
      const spoke = new Set(messages.filter((m) => m.type === 'statement' && m.round === lastRound.round).map((m) => (m as Statement).agent))
      typing = lastRound.agents.filter((k) => !spoke.has(k)).map((k) => agentOf(k) ?? { key: k, title: k, executor: false })
    }
  }
  const lookup = [...working.entries()].filter(([k]) => typing.some((a) => a.key === k)).slice(-2)

  const showOrder = (id: number) => document.getElementById(`order-${id}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })

  // 상시 운용: 비서 대화를 시각에 맞춰 채널 사이사이에 (보통 토론은 끝에 이어서)
  // 채널의 ts 는 그날 시각뿐이라 다음 날부터의 말은 시각을 견주지 않고 끝에 — 아침 말이 채널 맨 위로 가지 않게
  const stream = !!start?.stream
  const lastAfter = after.length ? after[after.length - 1].id : null
  const threadAt = new Map<number, AssistantMessage[]>()
  if (stream) {
    const day = (run?.created_at ?? '').slice(0, 10)
    const times = items.map((it) => ('m' in it && it.m && 'ts' in it.m ? String(it.m.ts) : ''))
    for (const am of after) {
      const t = am.created_at.slice(11)
      const later = am.created_at.slice(0, 10) > day
      let at = later ? items.length - 1 : 0
      if (!later) times.forEach((x, k) => { if (x && x <= t) at = k })
      threadAt.set(at, [...(threadAt.get(at) ?? []), am])
    }
  }
  const renderItem = (it: Item) => {
    switch (it.kind) {
      case 'date':
        return <p key={it.key} className="chat-pill date"><CalendarIcon /> {it.text}</p>
      case 'start':
        return <StartNotice key={it.key} m={it.m} people={agents} />
      case 'round':
        return <RoundDivider key={it.key} m={it.m} total={it.total} />
      case 'summary':
        return <SummaryCard key={it.key} it={it} />
      case 'statement':
        return <StatementGroup key={it.key} it={it} who={personOf(it.m.agent, it.m.title)} nameByTitle={nameByTitle} nameOf={(s) => names[s] ?? s} />
      case 'user':
        return <MineGroup key={it.key} m={it.m} />
      case 'gate':
        return <GateCard key={it.key} m={it.m} candidates={candidates} />
      case 'decision':
        return <DecisionGroup key={it.key} m={it.m} who={personOf(it.m.agent ?? executor?.key, it.m.title ?? executor?.title)} orders={orders} onOrdersChanged={ordersChanged} />
      case 'screening':
        return <ScreeningCard key={it.key} m={it.m} />
      case 'minutes':
        return (
          <div key={it.key} className="msg-group">
            <AgentAvatar p={assistant ?? { key: 'assistant', title: '비서' }} />
            <div className="msg-stack">
              <NameLine p={assistant ?? { key: 'assistant', title: '비서' }} />
              <div className="bubble minutes-bubble"><Minutes m={it.m} /></div>
            </div>
          </div>
        )
      case 'orders':
        return orders.length ? (
          <div key={it.key} className="msg-group right">
            <AgentAvatar p={executor ?? { key: 'trader', title: '트레이더', executor: true }} />
            <div className="msg-stack">
              <NameLine p={executor ?? { key: 'trader', title: '트레이더' }} />
              <OrderCards orders={orders} onChanged={ordersChanged} />
            </div>
          </div>
        ) : null
      case 'pill':
        return <p key={it.key} className={`chat-pill${it.tone ? ` ${it.tone}` : ''}`}>{it.text}</p>
    }
  }


  return (
    <div className="room-body">
      {pending.length > 0 && (
        <button className="room-notice" onClick={() => showOrder(pending[0].id)}>
          <NoticeIcon />
          <span className="room-notice-text">
            <b>승인 대기 {pending.length}건</b> {pending[0].name} {pending[0].side === 'buy' ? '매수' : '매도'} {pending[0].quantity}주
            {pending.length > 1 ? ` 외 ${pending.length - 1}건` : ''}
          </span>
          <span className="room-notice-go">보기</span>
        </button>
      )}
      <div className="chat-scroll scroll-y" ref={boxRef} aria-live="polite"
        onScroll={(e) => {
          const el = e.currentTarget
          const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
          if (bottom === atBottom) return
          setAtBottom(bottom)
          if (!bottom) setSeenCount(count)
        }}>
        <div className="chat-col">
          {!run && <p className="chat-pill">불러오는 중…</p>}
          {items.map((it, i) => (
            <Fragment key={it.key}>
              {renderItem(it)}
              {threadAt.has(i) && (
                <AssistantThread messages={threadAt.get(i)!} since={run?.created_at} onOpenSession={onOpenSession}
                  assistant={assistant} advice={advice} onChoose={onChoose} lastId={lastAfter} />
              )}
            </Fragment>
          ))}
          <AssistantThread messages={stream ? [] : after} since={run?.created_at} thinking={thinking} onOpenSession={onOpenSession}
            assistant={assistant} advice={advice} onChoose={onChoose} lastId={lastAfter} />
          {typing.length > 0 && (
            <div className="msg-group typing">
              <AgentAvatar p={typing[0]}>
                {typing.length > 1 && <span className="typing-more">+{typing.length - 1}</span>}
              </AgentAvatar>
              <div className="msg-stack">
                <span className="msg-name">
                  {typing.length > 3 ? `${typing.slice(0, 2).map(nameOf).join(', ')} 외 ${typing.length - 2}명` : typing.map(nameOf).join(', ')}
                </span>
                <div className="bubble typing-bubble" role="status" aria-label="입력 중"><i /><i /><i /></div>
                {lookup.map(([k, tools]) => (
                  <span key={k} className="msg-meta">{nameOf(personOf(k))} · {tools.slice(-2).join(', ')} 보는 중</span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
      {unseen > 0 && <JumpBar messages={messages} onClick={toBottom} />}
    </div>
  )
}

/** 회의록 — 회의가 끝나면 비서가 정리 (채팅방 카드·현황 서랍이 같이 쓴다) */
export function Minutes({ m }: { m: Msg<'minutes'> }) {
  return (
    <div className="minutes">
      <div className="minutes-head"><span className="chip">회의록 · {m.kind}</span>{m.by === 'code' && <span className="muted small">코드 정리</span>}</div>
      <b className="minutes-headline">{m.headline}</b>
      <p>{m.summary}</p>
      {m.points.length > 0 && (
        <ul className="minutes-list">{m.points.map((x) => <li key={x}>{x}</li>)}</ul>
      )}
      {m.follow_up.length > 0 && (
        <div className="minutes-follow small"><span className="muted">다음에 볼 것</span>
          <ul className="minutes-list">{m.follow_up.map((x) => <li key={x}>{x}</li>)}</ul>
        </div>
      )}
    </div>
  )
}

/** 투자 비서 대화 — 내 말은 오른쪽 내 말풍선, 비서·다른 에이전트 답은 왼쪽, 한 일(운용 지시 정리·판단 시작)은 가운데 줄.
 * 날짜가 바뀌면 날짜 줄, 비서가 답을 만드는 중이면 '입력 중' 말풍선. */
export function AssistantThread({ messages, since, thinking, onOpenSession, assistant, advice, onChoose, lastId: lastOverride }: {
  messages: AssistantMessage[]
  since?: string
  thinking?: boolean
  onOpenSession?: (id: number) => void
  assistant?: Person
  /** 자문 상담 중이면 마지막 자문 에이전트 말 아래에 누르는 답, '입력 중'도 자문 에이전트 */
  advice?: AdviceStatus | null
  onChoose?: (text: string) => void
  /** 대화 전체의 마지막 말 (나눠 그릴 때 — 누르는 답은 마지막 말에만) */
  lastId?: number | null
}) {
  const me: Person = assistant ?? { key: 'assistant', title: '비서' }
  const typingAs: Person = advice?.open ? advice.profile : me
  const lastId = lastOverride !== undefined ? lastOverride : messages.length ? messages[messages.length - 1].id : null
  // 리서치 결과(그 실행의 첫 데스크 말) — 리서치 시작 안내의 '결과 보기'가 여기로 내려간다
  const firstDesk = new Map<number, number>()
  for (const m of messages) if (m.meta.desk && m.meta.run_id && !firstDesk.has(m.meta.run_id)) firstDesk.set(m.meta.run_id, m.id)
  const deskRuns = new Set(firstDesk.keys())
  const showDesk = (runId: number) => document.getElementById(`desk-run-${runId}`)?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  return (
    <>
      {messages.map((m, k) => {
        const pill = m.created_at.slice(0, 10) !== (k ? messages[k - 1].created_at : since ?? '').slice(0, 10)
        const ts = m.created_at.slice(11)
        return (
          <Fragment key={m.id}>
            {pill && <p className="chat-pill date"><CalendarIcon /> {chatDate(m.created_at)}</p>}
            {m.role === 'user' ? (
              <MineGroup m={{ seq: -m.id, ts, type: 'user', text: m.text }} />
            ) : m.role === 'event' && m.meta.kind === 'report' && m.meta.report ? (
              <div className="msg-group">
                <AgentAvatar p={me} />
                <div className="msg-stack">
                  <NameLine p={me} />
                  <ReportCard report={m.meta.report} text={m.meta.body} by={`${nameOf(me)} · ${me.title}`} />
                </div>
              </div>
            ) : m.role === 'event' ? (
              <p className={`chat-pill assistant-event${m.meta.kind === 'session_started' ? ' strong' : ''}`}>
                {m.text}
                {m.meta.run_id && m.meta.kind === 'research_started' ? (
                  deskRuns.has(m.meta.run_id)
                    ? <button className="link-button" onClick={() => showDesk(m.meta.run_id!)}>결과 보기</button>
                    : <span className="muted"> · 진행 중</span>
                ) : m.meta.run_id && onOpenSession && (
                  <button className="link-button" onClick={() => onOpenSession(m.meta.run_id!)}>보기 #{m.meta.run_id}</button>
                )}
              </p>
            ) : (
              <div className="msg-group" id={m.meta.run_id && firstDesk.get(m.meta.run_id) === m.id ? `desk-run-${m.meta.run_id}` : undefined}>
                <AgentAvatar p={m.role === 'assistant' ? me : speaker(m)} />
                <div className="msg-stack">
                  <NameLine p={m.role === 'assistant' ? me : speaker(m)} />
                  <div className="bubble-line">
                    <div className={`bubble assistant-bubble${m.meta.error ? ' failed' : ''}`}><p><Rich text={m.text} /></p></div>
                    <time className="msg-time">{chatTime(ts)}</time>
                  </div>
                  {m.meta.desk && <DeskCard m={m} />}
                  {advice?.open && m.id === lastId && m.meta.choices && m.meta.choices.length > 0 && (
                    <div className="quick-replies" role="group" aria-label="답 고르기">
                      {m.meta.choices.map((c) => (
                        <button key={c} className="chip-toggle" disabled={thinking} onClick={() => onChoose?.(c)}>{c}</button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
          </Fragment>
        )
      })}
      {thinking && (
        <div className="msg-group typing">
          <AgentAvatar p={typingAs} />
          <div className="msg-stack">
            <NameLine p={typingAs} />
            <div className="bubble typing-bubble" role="status" aria-label={`${nameOf(typingAs)} 입력 중`}><i /><i /><i /></div>
          </div>
        </div>
      )}
    </>
  )
}

/** 비서 대화 속 다른 에이전트(분석 에이전트·운용 데스크) */
const speaker = (m: AssistantMessage): Person => ({ key: m.meta.agent ?? 'agent', title: m.meta.title ?? '에이전트', name: m.meta.name })

/** LLM 문장의 **굵게** 만 살리고 이스케이프된 따옴표를 정리한다 (HTML 은 그대로 글자로) */
function Rich({ text }: { text: string }) {
  const clean = text.replace(/\\"/g, '"').replace(/\\n/g, ' ')
  const parts = clean.split(/\*\*(.+?)\*\*/g)
  return <>{parts.map((p, i) => (i % 2 ? <strong key={i}>{p}</strong> : p))}</>
}

function StartNotice({ m, people }: { m: Msg<'start'>; people: Person[] }) {
  const analysts = m.agents.filter((a) => !a.executor).length
  const label = m.label ?? m.name
  if (m.stream) {
    return (
      <>
        <p className="chat-pill strong">상시 운용 채널이 열렸어요 — 장이 끝날 때까지 정기 회의 없이 계속 이야기해요</p>
        <p className="chat-pill">{people.map((a) => (a.name ? `${a.name}(${a.title})님` : `${a.title}님`)).join(', ')}이 들어왔어요</p>
        <div className="chat-card">
          <div className="chat-card-head">상시 운용 안내</div>
          <ul className="guide-steps small">
            <li>관리자 말·리스크 경보·리서치 갱신·종목 급변·트레이더 제안이 생기면 맞는 에이전트들이 바로 이야기해요. 조용하면 20분마다 점검.</li>
            <li>트레이더는 언제든 주문해요 — 종목마다 최근 2시간 입장이 3명 이상이고 과반이 같은 방향·반대 20% 이하면(입장판). 같은 종목은 1시간에 한 번, 하루 6건까지.</li>
            <li>트레이더도 계좌를 보고 먼저 사거나 팔 종목을 내놓을 수 있어요 — 그 종목으로 바로 이야기해요.</li>
          </ul>
          <p className="small muted">
            분석 {analysts}명 + 트레이더 · 토스증권 실계좌 · <b className="warn-text">승인 없이 주문돼요</b>{m.provider === 'sample' && ' · 샘플 시세'}
            {' '}· 아래 입력창으로 언제든 끼어들 수 있어요(다음 대화에 바로 반영).
          </p>
        </div>
      </>
    )
  }
  return (
    <>
      <p className="chat-pill">
        {m.autopilot ? `자동 운용 · ${(m.autopilot.slot ?? '').replace(/^\w+:/, '').slice(0, 5)} 판단을 시작해요` : `${label} 토론방이 열렸어요`}
        {m.autopilot?.agenda && <b> · 안건: {m.autopilot.agenda}</b>}
      </p>
      <p className="chat-pill">{people.map((a) => (a.name ? `${a.name}(${a.title})님` : `${a.title}님`)).join(', ')}이 들어왔어요</p>
      <div className="chat-card">
        <div className="chat-card-head">토론 안내</div>
        {m.candidates && m.candidates.length > 0 && (
          <div className="candidate-chips small">
            <span className="muted">후보 {m.candidates.length}</span>
            {m.candidates.map((c) => (
              <span key={c.symbol} className="chip" title={c.why}>{c.name}{c.security_type === 'ETF' ? ' · ETF' : ''}{c.held ? ` · 보유 ${c.held}` : ''}{c.why && c.why !== '보유' ? ` · ${c.why.length > 28 ? `${c.why.slice(0, 28)}…` : c.why}` : ''}</span>
            ))}
          </div>
        )}
        <p className="small muted">
          {m.rounds}라운드 · 분석 {analysts}명 + 트레이더 · 토스증권 실계좌 ·{' '}
          {m.gate === 'supermajority' ? '과반 게이트 · ' : ''}
          {m.auto_approve ? <b className="warn-text">{m.autopilot ? '자동 운용 — 승인 없이 주문돼요' : '승인 생략 — 바로 주문돼요'}</b> : '관리자가 승인해야 주문돼요'}
          {m.provider === 'sample' && ' · 샘플 시세'}
        </p>
        {m.notes?.map((n) => <p key={n} className="small muted">{n}</p>)}
        <p className="small muted">매수·매도 의견은 오른쪽, 의견 없음·관망은 왼쪽에 떠요.</p>
      </div>
    </>
  )
}

function RoundDivider({ m, total }: { m: Msg<'round'>; total: number }) {
  if (m.stream) return <p className="chat-pill round stream"><b>{m.label}</b></p>
  return (
    <p className="chat-pill round">
      <b>라운드 {m.round}{total ? ` / ${total}` : ''}</b> · {m.label}
      {total > 1 && (
        <span className="round-dots" aria-hidden>
          {Array.from({ length: total }, (_, i) => <i key={i} className={i < m.round ? 'on' : ''} />)}
        </span>
      )}
    </p>
  )
}

/** 종목별 입장 수 막대 (매수 · 매도 · 의견 없음) */
function TallyBar({ t }: { t: Pick<Tally, 'buy' | 'sell' | 'none'> }) {
  const sum = t.buy + t.sell + t.none || 1
  return (
    <span className="tally-bar" aria-hidden>
      {t.buy > 0 && <i className="buy" style={{ flexGrow: t.buy / sum }} />}
      {t.sell > 0 && <i className="sell" style={{ flexGrow: t.sell / sum }} />}
      {t.none > 0 && <i className="none" style={{ flexGrow: t.none / sum }} />}
    </span>
  )
}

const counts = (t: Pick<Tally, 'buy' | 'sell' | 'none'>) =>
  [t.buy && `매수 ${t.buy}`, t.sell && `매도 ${t.sell}`, t.none && `없음 ${t.none}`].filter(Boolean).join(' · ')

function SummaryCard({ it }: { it: Extract<Item, { kind: 'summary' }> }) {
  return (
    <div className="chat-card">
      <div className="chat-card-head">
        라운드 {it.round} 정리
        <span className="muted small">응답 {it.spoke}{it.changed ? ` · 입장 바꿈 ${it.changed}` : ''}{it.failed ? ` · 실패 ${it.failed}` : ''}</span>
      </div>
      <ul className="tally-list">
        {it.tally.map((t) => (
          <li key={t.symbol}>
            <span className="tally-name">{t.name}</span>
            <TallyBar t={t} />
            <span className="tally-count small">{counts(t)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function GateCard({ m, candidates }: { m: Msg<'gate'>; candidates: Candidate[] }) {
  const g = m.gate
  const rows = g.symbols
    ? Object.entries(g.symbols).map(([sym, s]) => ({ sym, name: m.names?.[sym] ?? candidates.find((c) => c.symbol === sym)?.name ?? sym, s }))
    : candidates.slice(0, 1).map((c) => ({ sym: c.symbol, name: c.name, s: g }))
  const open = rows.filter((r) => r.s.status === 'aligned').length
  return (
    <div className={`chat-card gate gate-${g.status}`}>
      <div className="chat-card-head">
        게이트 · {rows.length > 1 ? (open ? `${open}종목 열림` : '열린 종목 없음') : GATE_TEXT[g.status]}
        <span className="muted small">{g.mode === 'rolling' ? '입장판 — 최근 2시간 입장 3명 이상, 과반이 같은 방향·반대 20% 이하면 열려요'
          : g.mode === 'supermajority' ? '종목마다 과반이 같은 방향이고 반대가 20% 이하일 때 열려요' : '종목마다 모두 한 방향일 때만 열려요'}</span>
      </div>
      <ul className="tally-list">
        {rows.map(({ sym, name, s }) => (
          <li key={sym}>
            <span className="tally-name">{name}</span>
            <TallyBar t={s.counts} />
            <span className={`chip gate-${s.status}`}>{GATE_SHORT[s.status]}{s.direction ? ` · ${STANCE_LABEL[s.direction]}` : ''}</span>
          </li>
        ))}
      </ul>
      {g.reason && <p className="small muted">{g.reason}</p>}
    </div>
  )
}

/** 행동 의견(어느 후보든 매수·매도)인지 — 행동 의견은 오른쪽, 의견 없음·응답 실패는 왼쪽에 띄운다 */
function isAction(m: Statement): boolean {
  if (m.error) return false
  if (m.views?.length) return m.views.some((v) => v.stance !== 'none')
  return m.stance === 'buy' || m.stance === 'sell'
}

/** 에이전트 발언 하나 = 말풍선 묶음: 답장(반박·동의) → 입장과 주장 → 다른 에이전트에게 하는 말. 시각은 마지막 말풍선 옆. */
function StatementGroup({ it, who, nameByTitle, nameOf }: {
  it: Extract<Item, { kind: 'statement' }>
  who: Person
  nameByTitle: (title: string) => string
  nameOf: (s: string) => string
}) {
  const { m } = it
  const [more, setMore] = useState(false)
  // 상시 운용 트레이더 점검은 사거나 팔자는 종목만
  const views = (m.views ?? []).filter((v) => !m.executor || v.stance !== 'none')
  const multi = views.length > 1
  const shown = views.filter((v) => !v.implicit)
  const time = <time className="msg-time">{chatTime(m.ts)}</time>
  const hasMore = m.evidence.length > 0 || (m.changed && !!m.reason_for_change) || (multi && shown.some((v) => v.stance === 'none' && v.reason))
  return (
    <div className={`msg-group${isAction(m) ? ' right' : ''}`}>
      <AgentAvatar p={who} />
      <div className="msg-stack">
        <NameLine p={who} />
        {m.error ? (
          <div className="bubble-line">
            <div className="bubble failed"><p className="error small">응답 실패: {m.error}</p></div>
            {time}
          </div>
        ) : (
          <>
            {m.responses.map((r, k) => {
              const q = it.quotes[k]
              const toMe = r.to === '관리자'
              return (
                <div key={k} className="bubble-line">
                  <div className={`bubble${toMe ? ' to-me' : ''}`}>
                    <div className={`reply-quote ${toMe ? 'answer' : r.agree ? 'agree' : 'disagree'}`}>
                      <span className="reply-to">{toMe ? '나' : nameByTitle(r.to)}에게 {toMe ? '답장' : r.agree ? '동의' : '반박'}</span>
                      {q && <span className="reply-text"><Rich text={q.text} /></span>}
                    </div>
                    <p><Rich text={r.point} /></p>
                  </div>
                </div>
              )
            })}
            <div className="bubble-line">
              <div className="bubble">
                {!multi && (
                  <div className="bubble-tags">
                    <span className={`stance ${m.stance}`}>{stanceLabel(m.stance)}</span>
                    <span className="conf" title={`확신 ${m.confidence.toFixed(2)}`}><i style={{ width: `${m.confidence * 100}%` }} /></span>
                    {m.changed && m.previous_stance && <span className="badge warn">{STANCE_LABEL[m.previous_stance]} → {stanceLabel(m.stance)}</span>}
                  </div>
                )}
                {multi && <ViewList views={shown} hidden={views.length - shown.length} nameOf={nameOf} previous={m.previous ?? {}} expanded={more} />}
                <p><Rich text={m.thesis} /></p>
                {more && m.changed && m.reason_for_change && <p className="small muted">입장을 바꾼 이유: <Rich text={m.reason_for_change} /></p>}
                {more && m.evidence.length > 0 && (
                  <ul className="evidence small">
                    {m.evidence.map((e, k) => <li key={k}><Rich text={e.fact} /></li>)}
                  </ul>
                )}
                {hasMore && (
                  <button className="link small bubble-more" onClick={() => setMore((v) => !v)}>
                    {more ? '접기' : `더 보기${m.evidence.length ? ` · 근거 ${m.evidence.length}개` : ''}`}
                  </button>
                )}
              </div>
              {!m.message_to_others && time}
            </div>
            {m.message_to_others && (
              <div className="bubble-line">
                <div className="bubble"><p><Rich text={m.message_to_others} /></p></div>
                {time}
              </div>
            )}
          </>
        )}
        {it.tools.length > 0 && <span className="msg-meta">조회 · {it.tools.join(', ')}</span>}
      </div>
    </div>
  )
}

/** 여러 종목 토론의 후보별 입장 — 말하지 않은 후보는 개수만, 의견 없음의 이유는 '더 보기'에서 */
function ViewList({ views, hidden, nameOf, previous, expanded }: {
  views: View[]
  hidden: number
  nameOf: (s: string) => string
  previous: Record<string, string>
  expanded: boolean
}) {
  return (
    <ul className="view-list small">
      {views.map((v) => (
        <li key={v.symbol}>
          <span>{nameOf(v.symbol)}</span>
          <span className={`stance ${v.stance}`}>{STANCE_LABEL[v.stance]}</span>
          <span className="conf" title={`확신 ${v.confidence.toFixed(2)}`}><i style={{ width: `${v.confidence * 100}%` }} /></span>
          {previous[v.symbol] && <span className="badge warn">{stanceLabel(previous[v.symbol])} → {STANCE_LABEL[v.stance]}</span>}
          {v.reason && (v.stance !== 'none' || expanded) && <span className="muted view-reason"><Rich text={v.reason} /></span>}
        </li>
      ))}
      {hidden > 0 && <li className="muted">그 외 {hidden}종목 의견 없음</li>}
    </ul>
  )
}

function MineGroup({ m }: { m: Msg<'user'> }) {
  return (
    <div className="msg-group mine">
      <div className="msg-stack">
        <div className="bubble-line">
          <time className="msg-time">{chatTime(m.ts)}</time>
          <div className={`bubble${m.standing ? ' standing' : ''}`}>
            {m.standing ? <p><span className="standing-tag">운용 지시</span>{m.text.replace(/^\[운용 지시\]\s*/, '')}</p> : <p>{m.text}</p>}
          </div>
        </div>
      </div>
    </div>
  )
}

/** 트레이더 결정 말풍선 + 주문 승인 카드 */
function DecisionGroup({ m, who, orders, onOrdersChanged }: { m: Msg<'decision'>; who: Person; orders: Order[]; onOrdersChanged: () => void }) {
  const [more, setMore] = useState(false)
  const d = m.decision
  const list = (d.decisions ?? []).filter((x) => x.decision !== 'hold')  // 이번에 사고파는 것만 (관망한 후보는 빼고)
  const title = m.title ?? '트레이더'
  const action = list.length > 0 || (!(d.decisions ?? []).length && d.decision !== 'hold')
  return (
    <div className={`msg-group${action ? ' right' : ''}`}>
      <AgentAvatar p={{ ...who, executor: true }} />
      <div className="msg-stack">
        <span className="msg-name"><b>{nameOf(who) || title}</b> <span className="msg-role">{who.title || title} · 실행 담당</span></span>
        <div className="bubble-line">
          <div className="bubble">
            {list.length > 0 ? (
              <ul className="view-list small">
                {list.map((x) => (
                  <li key={x.symbol}>
                    <span>{x.name}</span>
                    <span className={`stance ${x.decision}`}>{STANCE_LABEL[x.decision as 'buy' | 'sell']} {x.quantity}주 제안</span>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="bubble-tags">
                {action ? <span className={`stance ${d.decision}`}>{stanceLabel(d.decision)} {d.quantity}주 제안</span>  // 예전 단일 종목 형식
                  : <span className="stance none">이번엔 주문 없음 · 관망</span>}
              </div>
            )}
            <p className={more ? '' : 'clamp'}><Rich text={d.rationale} /></p>
            {d.rationale.length > 160 && <button className="link small bubble-more" onClick={() => setMore((v) => !v)}>{more ? '접기' : '더 보기'}</button>}
            {d.note && <p className="small warn-text">{d.note}</p>}
          </div>
          {!orders.length && <time className="msg-time">{chatTime(m.ts)}</time>}
        </div>
        {orders.length > 0 && <OrderCards orders={orders} onChanged={onOrdersChanged} />}
      </div>
    </div>
  )
}

/** 트레이더 주문 제안 카드 — 관리자가 승인하면 토스증권 실계좌로 제출 */
function OrderCards({ orders, onChanged }: { orders: Order[]; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const act = async (o: Order, approve: boolean) => {
    setError(null)
    if (approve &&
      !window.confirm(`실계좌 주문입니다. ${o.name} ${o.side === 'buy' ? '매수' : '매도'} ${o.quantity}주를 토스증권으로 보낼까요?`)) return
    setBusy(true)
    try {
      if (approve) await investApi.approve(o.id, true)
      else await investApi.reject(o.id)
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      {orders.map((o) => {
        const pending = o.status === 'pending_approval'
        return (
          <div key={o.id} id={`order-${o.id}`} className={`order-card status-${o.status}`}>
            <div className="order-card-head">
              <span>{pending ? '주문 승인 요청' : '주문'}</span>
              <span className={`chip order-${o.status}`}>{ORDER_STATUS[o.status]}</span>
            </div>
            <div className="order-card-body">
              <div className="order-card-title">
                <span className={`side-chip ${o.side}`}>{o.side === 'buy' ? '매수' : '매도'}</span>
                <b>{o.name} {o.quantity}주</b>
              </div>
              <dl className="order-card-facts small">
                <dt>주문</dt><dd>{o.order_type === 'limit' ? `지정가 ${price(o.price)}` : `시장가 (현재 ${price(o.ref_price)})`}</dd>
                <dt>예상</dt><dd>{won(o.amount)}</dd>
                <dt>계좌</dt><dd><b className="up">토스증권 실계좌</b></dd>
                {o.avg_fill_price ? <><dt>체결</dt><dd>{price(o.avg_fill_price)}</dd></> : null}
              </dl>
              {o.error && !pending && <p className="small neg">{o.error}</p>}
            </div>
            {pending && (
              <div className="order-card-actions">
                <button disabled={busy} onClick={() => act(o, false)}>거절</button>
                <button className="primary" disabled={busy} onClick={() => act(o, true)}>승인</button>
              </div>
            )}
          </div>
        )
      })}
      {orders.some((o) => o.status === 'pending_approval') && <span className="msg-meta">승인하면 한도를 다시 검사한 뒤 토스증권으로 주문돼요</span>}
      {error && <p className="error small">{error}</p>}
    </>
  )
}

/** 위로 올려 읽는 중일 때 아래에 뜨는 마지막 메시지 미리 보기 */
function JumpBar({ messages, onClick }: { messages: ChatMessage[]; onClick: () => void }) {
  const last = [...messages].reverse().find((m) => m.type === 'statement' || m.type === 'user' || m.type === 'decision' || m.type === 'gate')
  let who = ''
  let text = '새 메시지'
  if (last?.type === 'statement') [who, text] = [last.title, last.message_to_others || last.thesis]
  else if (last?.type === 'user') [who, text] = ['나', last.text]
  else if (last?.type === 'decision') [who, text] = [last.title ?? '트레이더', last.decision.rationale]
  else if (last?.type === 'gate') [who, text] = ['게이트', GATE_TEXT[last.gate.status] ?? '']
  return (
    <button className="chat-jump" onClick={onClick}>
      {who && <b>{who}</b>}
      <span className="chat-jump-text">{text.replace(/\*\*/g, '')}</span>
      <span aria-hidden>⌄</span>
    </button>
  )
}

/** 입력창 — Enter 로 보내고 Shift+Enter 로 줄바꿈 (한글 조합 중 Enter 는 무시) */
export function Composer({ onSend, placeholder, label = '에이전트들에게 의견 보내기' }: {
  onSend: (text: string) => Promise<boolean>
  placeholder: string
  label?: string
}) {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`
  }, [text])
  const submit = async () => {
    if (!text.trim() || sending) return
    setSending(true)
    const ok = await onSend(text.trim())
    setSending(false)
    if (ok) setText('')
  }
  return (
    <div className="room-composer">
      <textarea ref={ref} rows={1} value={text} maxLength={500} aria-label={label} placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            submit()
          }
        }} />
      <button className="send-button" disabled={sending || !text.trim()} onClick={submit}>{sending ? '…' : '전송'}</button>
    </div>
  )
}

export function SessionChip({ run }: { run: Run }) {
  if (isActive(run.status)) return <span className="chip run-running">{STATUS_LABEL[run.status]}</span>
  if (run.status !== 'succeeded') return <span className={`chip run-${run.status}`}>{STATUS_LABEL[run.status]}</span>
  const s = run.summary ?? {}
  const orders = (run as Run & { orders_now?: SessionOrder[] }).orders_now ?? []
  const pending = orders.filter((o) => o.status === 'pending_approval').length
  if (pending) return <span className="chip order-pending_approval">승인 대기 {pending}</span>
  if (orders.length) {
    const last = orders[orders.length - 1]
    return <span className={`chip order-${last.status}`}>{orders.length > 1 ? `주문 ${orders.length} · ` : ''}{ORDER_STATUS[last.status]}</span>
  }
  return <span className={`chip gate-${s.gate as string}`}>{s.decision === 'hold' ? '행동 안 함' : stanceLabel(String(s.decision))}</span>
}

function NoticeIcon() {
  return (
    <svg className="room-notice-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M3 11v2a1 1 0 0 0 1 1h2l5 4V6L6 10H4a1 1 0 0 0-1 1z" />
      <path d="M15.5 8.5a5 5 0 0 1 0 7" />
      <path d="M18.5 5.5a9 9 0 0 1 0 13" />
    </svg>
  )
}

function CalendarIcon() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
      <rect x="4" y="5" width="16" height="15" rx="2" />
      <path d="M4 10h16M9 3v4M15 3v4" />
    </svg>
  )
}
