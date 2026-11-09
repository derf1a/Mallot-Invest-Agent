/**
 * AI Trading — 에이전트 토론을 카카오톡 단체방처럼 한 화면 전체로 본다. 관리자(나)도 토론 중 의견을 보내고, 트레이더가 낸 주문은 채팅방 안의 승인 카드로 승인한다.
 * 머리글 오른쪽: 자동 운용(서랍: 켜기·오늘 일정·운용 지시·시각) · 지난 토론(목록) · 현황(서랍: 참여자·입장표·세션 정보) · 새 토론(팝업: 범위·후보·첫 메시지).
 * 토론 중이 아닐 때 입력창은 투자 비서에게 간다 — 비서가 답하고(필요하면 다른 에이전트에게 묻고), 운용 지시를 정리하고, 즉시 지시면 바로 판단을
 * 시작한다. 그 대화는 보고 있는 토론 아래에 이어서 보인다. 진행 단계는 채팅방 안의 안내 줄·카드로.
 * 에이전트 구성·라운드·모델·후보 수·'승인 생략'은 실험실 > 투자 에이전트에서. 토론은 한 번에 하나만.
 */
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { isActive } from '../runs/live'
import type { Run } from '../runs/api'
import { AskComposer, StreamComposer } from './agents/Advice'
import Autopilot from './agents/Autopilot'
import { AssistantThread, ChatRoom, Composer, SessionChip } from './agents/chat'
import { AgentAvatar } from './agents/Face'
import Members from './agents/Members'
import { memberCount, withNames, type Person } from './agents/people'
import NewDebate from './agents/NewDebate'
import RoomInfo from './agents/RoomInfo'
import { findLast, useAssistant, useChat } from './agents/session'
import { investApi, type AdviceStatus, type Agent, type AssistantMessage, type AutopilotStatus, type DeskTeam } from './api'
import { sessionLabel, useTicker } from './format'
import { useLoad } from '../../shared/useLoad'

type Panel = 'new' | 'sessions' | 'info' | 'autopilot' | 'members' | null
const ROOM_NAME = '투자 회의실'
const ASK_HINT = '비서에게 말하기 — 질문 · 운용 지시(반도체 주목) · "지금 판단해 줘" · 설정("회의 하루 4번", "보고는 월요일 8시") (Enter 전송)'

function lastSymbol(): string {
  try {
    return localStorage.getItem('invest.symbol') ?? '005930'
  } catch {
    return '005930'
  }
}

export default function AiTrading() {
  const [params, setParams] = useSearchParams()
  const [panel, setPanel] = useState<Panel>(() => (params.has('s') ? 'new' : null))
  const [version, setVersion] = useState(0)
  const tick = useTicker(4000)
  const { data: agents } = useLoad(() => investApi.agents(), [])
  const { data: status } = useLoad(() => investApi.status(), [version])
  const { data: active } = useLoad(() => investApi.activeSession(), [version, tick])
  const { data: history } = useLoad(() => investApi.sessions(undefined, 60), [version])  // 회의 내역: 하루 세 번 × 약 3주
  const slowTick = useTicker(20000)
  const { data: pilotLoaded } = useLoad(() => investApi.autopilot(), [version, slowTick])
  const [pilotSaved, setPilotSaved] = useState<AutopilotStatus | null>(null)
  const pilot = pilotSaved && pilotLoaded && pilotSaved.today.date === pilotLoaded.today.date ? pilotSaved : pilotLoaded ?? pilotSaved
  const [error, setError] = useState<string | null>(null)

  // 보는 세션: 고른 것 → 진행 중 → 가장 최근
  const picked = params.get('session') ? Number(params.get('session')) : null
  const current = picked ?? active?.id ?? history?.[0]?.id ?? null
  const viewingLive = !!active && current === active.id
  // 회의 내역·대화의 '보기'로 연 회의는 처음(방 열림)부터 보여 준다 — 진행 중 회의로 돌아갈 때만 맨 아래
  const [fromTop, setFromTop] = useState<number | null>(null)
  const pick = (id: number, top = true) => {
    setFromTop(top ? id : null)
    setParams((p) => {
      const next = new URLSearchParams(p)
      next.set('session', String(id))
      next.delete('s')
      return next
    }, { replace: true })
    setPanel(null)
    setError(null)
  }
  const refresh = useCallback(() => setVersion((v) => v + 1), [])
  const closePanel = () => {
    setPanel(null)
    if (params.has('s')) setParams((p) => {
      const next = new URLSearchParams(p)
      next.delete('s')
      return next
    }, { replace: true })
  }

  // Esc 로 열린 목록·서랍을 닫는다 (팝업은 바깥을 눌러 닫기)
  useEffect(() => {
    if (panel !== 'sessions' && panel !== 'info' && panel !== 'autopilot' && panel !== 'members') return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setPanel(null)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [panel])

  const send = async (text: string) => {
    if (!active) return false
    setError(null)
    try {
      await investApi.chat(active.id, text)
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return false
    }
  }
  // 투자 비서 — 답이 끝나면(운용 지시를 고쳤거나 판단을 시작했을 수 있어) 자동 운용·진행 중 토론을 다시 받는다
  const talk = useAssistant()
  const [wasPending, setWasPending] = useState(false)
  if (talk.pending !== wasPending) {
    setWasPending(talk.pending)
    if (!talk.pending) {
      setPilotSaved(null)
      refresh()
    }
  }
  const sendToAssistant = async (text: string) => {
    setError(null)
    try {
      await talk.send(text)
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return false
    }
  }
  const stop = async () => {
    if (!active) return
    await investApi.stopSession(active.id)
    refresh()
  }

  const currentRun = history?.find((r) => r.id === current) ?? (active?.id === current ? active : null)
  const toggle = (p: Exclude<Panel, null>) => setPanel((v) => (v === p ? null : p))
  const actions = (
    <>
      <button className={`room-action${panel === 'autopilot' ? ' on' : ''}`} onClick={() => toggle('autopilot')} aria-expanded={panel === 'autopilot'}
        aria-label={`자동 운용 ${pilot?.config.enabled ? '켜짐' : '꺼짐'}`} title={pilot?.config.enabled ? `자동 운용 켜짐${pilot.next ? ` · 다음 ${pilot.next.time}` : ''}` : '자동 운용 꺼짐'}>
        <ClockIcon /><span className="room-action-label">자동 운용</span>
        <span className={`pilot-dot${pilot?.config.enabled ? ' on' : ''}`} aria-hidden />
      </button>
      <div className="room-menu-wrap">
        <button className={`room-action${panel === 'sessions' ? ' on' : ''}`} onClick={() => toggle('sessions')} aria-expanded={panel === 'sessions'}
          aria-label="회의 내역" title="회의 내역 (날짜별 회의록)">
          <ListIcon /><span className="room-action-label">회의 내역</span>
        </button>
        {panel === 'sessions' && (
          <MeetingMenu history={history ?? []} today={pilot?.today ?? null} current={current} onPick={pick} onClose={() => setPanel(null)} />
        )}
      </div>
      {current !== null && (
        <button className={`room-action${panel === 'info' ? ' on' : ''}`} onClick={() => toggle('info')} aria-expanded={panel === 'info'}
          aria-label="토론 현황" title="토론 현황 (참여자·입장표)">
          <MenuIcon /><span className="room-action-label">현황</span>
        </button>
      )}
      <button className="room-action primary" onClick={() => toggle('new')} aria-label="새 토론" title="새 토론">
        <PlusIcon /><span className="room-action-label">새 토론</span>
      </button>
    </>
  )

  return (
    <section className="ai-page">
      <h1 className="sr-only">AI Trading</h1>
      {current === null ? (
        <EmptyRoom actions={actions} onNew={() => setPanel('new')} loading={!history} onAsk={sendToAssistant} thread={talk.messages}
          thinking={talk.pending} onOpenSession={pick} error={error} assistant={agents?.assistant} desk={agents?.desk} roster={agents?.roster ?? []}
          advice={talk.advice} onAdviceChanged={talk.refresh}
          membersOpen={panel === 'members'} onMembers={() => toggle('members')} onCloseMembers={() => setPanel(null)} />
      ) : (
        <SessionView key={current} id={current} run={currentRun} live={viewingLive} activeRun={active ?? null} actions={actions}
          roster={agents?.roster ?? []} infoOpen={panel === 'info'} onCloseInfo={() => setPanel(null)} error={error}
          membersOpen={panel === 'members'} onMembers={() => toggle('members')}
          onSend={send} onAsk={sendToAssistant} thread={talk.messages} thinking={talk.pending} onOpenSession={pick} assistant={agents?.assistant}
          desk={agents?.desk} advice={talk.advice} onAdviceChanged={talk.refresh}
          onStop={stop} onFinished={refresh} onOrdersChanged={refresh}
          startAtTop={fromTop === current} onShowLive={() => active && pick(active.id, false)} />
      )}
      {panel === 'autopilot' && (
        <Autopilot status={pilot ?? null} roster={agents?.roster ?? []} advice={talk.advice} onAdviceStarted={() => {
          setPanel(null)
          talk.refresh()
        }} onChanged={(s) => {
          setPilotSaved(s)
          refresh()
        }} onOpenSession={(id) => {
          pick(id)
          refresh()
        }} onBriefingSent={talk.refresh} onClose={() => setPanel(null)} />
      )}
      {panel === 'new' && (
        <NewDebate agents={agents ?? null} blockers={status?.trading_blockers ?? []} autoApprove={!!status?.agent_auto_approve}
          active={active ?? null} initialSymbol={params.get('s') ?? lastSymbol()} onClose={closePanel}
          onStarted={(id) => {
            pick(id)
            refresh()
          }}
          onShowActive={() => {
            refresh()
            if (active) pick(active.id, false)
          }} />
      )}
    </section>
  )
}

/** 세션 하나 — 머리글·채팅방·입력창과 현황 서랍이 같은 메시지를 본다 (세션이 바뀌면 key 로 새로 마운트) */
function SessionView({ id, run, live, activeRun, actions, roster, infoOpen, onCloseInfo, membersOpen, onMembers, error, thread, thinking, assistant, desk, advice, onAdviceChanged, startAtTop, onSend, onAsk, onOpenSession, onStop, onFinished, onOrdersChanged, onShowLive }: {
  id: number
  run: Run | null | undefined
  live: boolean
  activeRun: Run | null
  actions: ReactNode
  roster: Agent[]
  infoOpen: boolean
  onCloseInfo: () => void
  membersOpen: boolean
  onMembers: () => void
  error: string | null
  thread: AssistantMessage[]
  thinking: boolean
  assistant?: Person
  /** 리서치 데스크 (참여자 수·참여자 서랍) */
  desk?: DeskTeam
  /** 자문 상담 — 열려 있으면 입력이 자문 에이전트에게 */
  advice?: AdviceStatus | null
  onAdviceChanged: () => void
  onSend: (text: string) => Promise<boolean>
  onAsk: (text: string) => Promise<boolean>
  onOpenSession: (id: number) => void
  onStop: () => void
  onFinished: () => void
  onOrdersChanged: () => void
  onShowLive: () => void
  /** 처음부터 보기 (회의 내역·'보기'로 열었을 때) */
  startAtTop?: boolean
}) {
  const chat = useChat(id)
  const shown = chat.run ?? run
  const running = !!shown && isActive(shown.status)
  // 끝난 세션의 칩은 세션 목록 쪽(승인 뒤 현재 주문 상태 orders_now 포함)으로
  const chipRun = !running && run ? run : shown
  const start = findLast(chat.messages, 'start')
  // 방(투자 회의실)의 참여자는 지금 명단 — 이 회의에 누가 있었는지는 현황·회의록에. 명단을 아직 못 받았으면 이 회의 참석자로
  const current = roster.filter((a) => a.enabled)
  const members = current.length ? current : withNames(start?.agents ?? [], roster)

  return (
    <>
      <header className="room-head">
        <div className="room-id">
          <span className="room-avatars" aria-hidden>
            {members.slice(0, 4).map((a) => <AgentAvatar key={a.key} p={a} className="tiny" label={false} />)}
          </span>
          <div className="room-title">
            <div className="room-name">
              <b>{ROOM_NAME}</b>
              <button className="room-count" onClick={onMembers} aria-expanded={membersOpen} title="참여자 보기 (나·비서 포함)">
                {memberCount(members, desk)}
              </button>
            </div>
            <div className="room-sub small">
              {chipRun && <SessionChip run={chipRun} />}
              <span className="room-meeting">{start?.label ?? (shown ? sessionLabel(shown) : '')} · #{id}{shown ? ` · ${shown.created_at.slice(5, 16)}` : ''}</span>
              {start?.auto_approve && <b className="warn-text">승인 생략</b>}
            </div>
          </div>
        </div>
        <div className="room-actions">
          {running && live && (
            <button className="stop-button" onClick={onStop} disabled={shown?.cancel_requested}>
              {shown?.cancel_requested ? '중지하는 중…' : '■ 중지'}
            </button>
          )}
          {actions}
        </div>
      </header>

      <ChatRoom chat={chat} onFinished={onFinished} onOrdersChanged={onOrdersChanged} onOpenSession={onOpenSession} roster={roster} assistant={assistant}
        startAtTop={startAtTop}
        after={(running && !start?.stream) || !shown ? [] : thread.filter((m) => m.created_at >= shown.created_at)}
        thinking={(!running || !!start?.stream) && thinking}
        advice={advice} onChoose={(t) => void onAsk(t)} />

      {error && <p className="error small room-error">{error}</p>}
      {live && running && start?.stream ? (
        <StreamComposer onSend={onSend} advice={advice ?? null} onAsk={onAsk} onChanged={onAdviceChanged} hint={ASK_HINT} />
      ) : live && running ? (
        <Composer onSend={onSend} placeholder="에이전트들에게 의견 보내기 — 다음 라운드부터 반영돼요 (Enter 전송 · Shift+Enter 줄바꿈)" />
      ) : activeRun && !live ? (
        <div className="room-composer idle">
          <span className="small"><span className="typing-dot" aria-hidden /> #{activeRun.id} {sessionLabel(activeRun)} 토론이 진행 중이에요</span>
          <button onClick={onShowLive}>보러 가기</button>
        </div>
      ) : running ? (
        <div className="room-composer idle"><span className="small muted">토론을 준비하고 있어요…</span></div>
      ) : (
        <AskComposer advice={advice ?? null} onAsk={onAsk} onChanged={onAdviceChanged} hint={ASK_HINT} />
      )}

      {membersOpen && <Members people={members} assistant={assistant} desk={desk} roster={roster} onClose={onMembers} />}
      {infoOpen && (
        <>
          <div className="room-drawer-backdrop" onClick={onCloseInfo} />
          <aside className="room-drawer info-drawer" aria-label="토론 현황">
            <div className="room-drawer-head">
              <h2>토론 현황</h2>
              <button className="icon-button" aria-label="닫기" onClick={onCloseInfo}>✕</button>
            </div>
            <div className="room-drawer-body scroll-y">
              <RoomInfo chat={chat} roster={roster} />
            </div>
          </aside>
        </>
      )}
    </>
  )
}

/** 아직 토론이 없을 때 — 어떻게 진행되는지 채팅방 안내처럼 */
function EmptyRoom({ actions, onNew, loading, onAsk, thread, thinking, onOpenSession, error, assistant, desk, advice, onAdviceChanged, roster, membersOpen, onMembers, onCloseMembers }: {
  actions: ReactNode
  onNew: () => void
  loading: boolean
  onAsk: (text: string) => Promise<boolean>
  thread: AssistantMessage[]
  thinking: boolean
  onOpenSession: (id: number) => void
  error: string | null
  assistant?: Person
  /** 리서치 데스크 (참여자 수·참여자 서랍) */
  desk?: DeskTeam
  /** 자문 상담 — 열려 있으면 입력이 자문 에이전트에게 */
  advice?: AdviceStatus | null
  onAdviceChanged: () => void
  roster: Agent[]
  membersOpen: boolean
  onMembers: () => void
  onCloseMembers: () => void
}) {
  const people = roster.filter((a) => a.enabled)
  return (
    <>
      <header className="room-head">
        <div className="room-id">
          <span className="room-avatars" aria-hidden>
            {people.slice(0, 4).map((a) => <AgentAvatar key={a.key} p={a} className="tiny" label={false} />)}
          </span>
          <div className="room-title">
            <div className="room-name">
              <b>{ROOM_NAME}</b>
              <button className="room-count" onClick={onMembers} aria-expanded={membersOpen} title="참여자 보기 (나·비서 포함)">{memberCount(people, desk)}</button>
            </div>
            <div className="room-sub small muted">에이전트 투자 회의</div>
          </div>
        </div>
        <div className="room-actions">{actions}</div>
      </header>
      <div className="room-body">
        <div className="chat-scroll scroll-y">
          <div className="chat-col">
            {loading ? <p className="chat-pill">불러오는 중…</p> : (
              <>
                <p className="chat-pill">아직 토론이 없어요</p>
                <div className="chat-card">
                  <div className="chat-card-head">이렇게 진행돼요</div>
                  <ol className="guide-steps small">
                    <li>라운드 1 · 에이전트마다 독립 의견</li>
                    <li>라운드 2+ · 서로 반박·동의 (입장을 바꾸면 이유 필수) — 나도 의견을 보낼 수 있어요</li>
                    <li>게이트 · 종목마다 모두 한 방향일 때만 열림</li>
                    <li>트레이더 · 열린 종목만, 그 방향으로 주문 제안</li>
                    <li>승인 · 내가 승인해야 토스증권으로 주문</li>
                  </ol>
                  <p className="small muted">아래 입력창으로 비서에게 말하면 답하고(필요하면 다른 에이전트에게 묻고), 운용 지시를 정리하고, '지금 판단해 줘'면 바로 토론을 시작해요. 자동 운용을 켜면 정해진 시각마다 보유 종목과 운용 지시로 스스로 토론하고, 과반이 같은 방향이면 승인 없이 주문해요.</p>
                  <p className="small muted">에이전트 토론이 수익을 높인다고 보장하지 않아요. 판단과 책임은 관리자에게 있어요.</p>
                  <button className="primary" onClick={onNew}>+ 새 토론 시작</button>
                </div>
                <AssistantThread messages={thread} thinking={thinking} onOpenSession={onOpenSession} assistant={assistant}
                  advice={advice} onChoose={(t) => void onAsk(t)} />
              </>
            )}
          </div>
        </div>
      </div>
      {error && <p className="error small room-error">{error}</p>}
      <AskComposer advice={advice ?? null} onAsk={onAsk} onChanged={onAdviceChanged} hint={ASK_HINT} />
      {membersOpen && <Members people={people} assistant={assistant} desk={desk} roster={roster} onClose={onCloseMembers} />}
    </>
  )
}

const DAY_NAMES = '일월화수목금토'
const dayLabel = (d: string) => {
  const [y, m, dd] = d.split('-').map(Number)
  return `${m}월 ${dd}일 (${DAY_NAMES[new Date(y, m - 1, dd).getDay()]})`
}
const ENTRY_STATUS: Record<string, string> = { pending: '예정', skipped: '건너뜀', missed: '놓침' }

/** 회의 내역 — 날짜별로 묶고 회의마다 시각·이름·상태와 비서가 정리한 회의록 한 줄. 오늘은 아직 열리지 않은 정기 회의(예정·건너뜀·놓침)도 */
function MeetingMenu({ history, today, current, onPick, onClose }: {
  history: Run[]
  today: AutopilotStatus['today'] | null
  current: number | null
  onPick: (id: number) => void
  onClose: () => void
}) {
  const days = new Map<string, Run[]>()
  for (const r of history) days.set(r.created_at.slice(0, 10), [...(days.get(r.created_at.slice(0, 10)) ?? []), r])
  const waiting = (today?.entries ?? []).filter((e) => !e.run_id && e.kind !== 'manual')
  if (today && waiting.length && !days.has(today.date)) days.set(today.date, [])
  const dates = [...days.keys()].sort().reverse()
  return (
    <>
      <div className="room-menu-backdrop" onClick={onClose} />
      <div className="room-menu" role="dialog" aria-label="회의 내역">
        <div className="room-menu-head"><b>회의 내역</b> <span className="muted small">{history.length}</span></div>
        <div className="session-items scroll-y">
          {dates.map((d) => (
            <section key={d} className="meeting-day">
              <h4 className="muted small">{dayLabel(d)}</h4>
              <ul>
                {days.get(d)!.map((r) => {
                  const minutes = r.summary?.minutes as { headline?: string } | undefined
                  return (
                    <li key={r.id}>
                      <button className={`session-item${r.id === current ? ' on' : ''}`} onClick={() => onPick(r.id)}>
                        <span className="session-title"><span className="meeting-time num">{r.created_at.slice(11, 16)}</span> {sessionLabel(r)}</span>
                        <SessionChip run={r} />
                        {minutes?.headline && <span className="meeting-headline small">{minutes.headline}</span>}
                      </button>
                    </li>
                  )
                })}
                {today && d === today.date && waiting.map((e) => (
                  <li key={e.key} className={`meeting-waiting ${e.status}`}>
                    <span className="meeting-time num">{e.time}</span>
                    <span className="small">{e.kind === 'extra' ? 'AI 가 더한 회의' : '정기 회의'} · {ENTRY_STATUS[e.status] ?? e.status}</span>
                    {e.note && <span className="muted small">{e.note}</span>}
                  </li>
                ))}
              </ul>
            </section>
          ))}
          {!dates.length && <p className="muted small">아직 회의가 없어요.</p>}
        </div>
      </div>
    </>
  )
}

const icon = { viewBox: '0 0 24 24', width: 18, height: 18, fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round' as const, 'aria-hidden': true }
const ListIcon = () => <svg {...icon}><path d="M9 6h11M9 12h11M9 18h11" /><circle cx="4.5" cy="6" r="1" /><circle cx="4.5" cy="12" r="1" /><circle cx="4.5" cy="18" r="1" /></svg>
const MenuIcon = () => <svg {...icon}><path d="M4 7h16M4 12h16M4 17h16" /></svg>
const PlusIcon = () => <svg {...icon}><path d="M12 5v14M5 12h14" /></svg>
const ClockIcon = () => <svg {...icon}><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></svg>
