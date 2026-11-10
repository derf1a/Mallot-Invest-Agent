/**
 * 자동 운용 서랍 (AI Trading 머리글 '자동 운용') — 켜기/끄기, 운용 프로세스(리서치 → 투자위원회 → 집행 → 리스크 감시, 지금 리서치),
 * 오늘 판단 일정(고정·AI 추가·AI 가 옮김·수시 회의, 상태·안건), 지금 판단,
 * 운용 지시(채팅으로 남긴 의도: 섹터 주목·공격적으로·특정 종목), 고정 시각·AI 추가 횟수·필요할 때만 회의, 후보 미리 보기.
 * 켜기·끄기는 PIN 없이 .env 의 MIA_AUTOPILOT 을 고친다. 시각·방식·운용 지시·지금 판단은 주문 PIN(서버가 요구하면 한 번 묻는다).
 */
import { useState, type ReactNode } from 'react'
import { investApi, type AdviceStatus, type Agent, type AutopilotEntry, type AutopilotStatus, type MorningBriefing, type ScopePreview } from '../api'
import { useReadOnly } from '../../../shared/readOnly'
import { useLoad } from '../../../shared/useLoad'
import { PolicySection } from './Advice'
import { DeskProcess } from './Desk'

const KIND: Record<AutopilotEntry['kind'], string> = { fixed: '고정', extra: 'AI 추가', manual: '지금 판단', risk: '수시 회의', stream: '상시 운용' }
const MODES = [
  { key: 'continuous', label: '상시 운용', text: '정기 회의 없이 장 내내 에이전트들이 계속 이야기하고, 트레이더는 입장판이 받쳐 주면 언제든 주문해요' },
  { key: 'meetings', label: '정기 회의', text: '정해 둔 시각(+ AI 가 더한 시각)에 토론하고, 열린 종목만 주문해요' },
] as const
const kindOf = (e: AutopilotEntry) =>
  e.kind === 'fixed' && e.origin !== e.time ? `${e.by === 'assistant' ? '비서' : 'AI'} 가 옮김 (${e.origin})`
    : e.kind === 'extra' && e.by === 'assistant' ? '비서 추가' : KIND[e.kind]
const STREAM: Record<NonNullable<AutopilotStatus['stream']>['state'], string> = { running: '채널 돌고 있음', stopped: '채널 멈춤', closed: '오늘 운용 끝', waiting: '채널 기다리는 중' }
const STATUS: Record<AutopilotEntry['status'], string> = { pending: '예정', started: '판단함', skipped: '건너뜀', missed: '놓침' }

export default function Autopilot({ status, roster, advice, onAdviceStarted, onChanged, onOpenSession, onBriefingSent, onClose }: {
  status: AutopilotStatus | null
  /** 운용 프로세스에서 담당 에이전트 이름·그림 */
  roster: Agent[]
  advice: AdviceStatus | null
  /** 자문 상담을 열었을 때 — 서랍을 닫고 채팅방에서 첫 질문을 받는다 */
  onAdviceStarted: () => void
  onChanged: (s: AutopilotStatus) => void
  onOpenSession: (id: number) => void
  /** 아침 브리핑을 지금 보냈을 때 — 채팅방 대화를 바로 다시 받는다 */
  onBriefingSent: () => void
  onClose: () => void
}) {
  const readOnly = useReadOnly()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [slots, setSlots] = useState<string | null>(null)
  const [extra, setExtra] = useState<number | null>(null)
  const [preview, setPreview] = useState<ScopePreview | null>(null)

  const act = async (fn: () => Promise<AutopilotStatus | void>) => {
    setBusy(true)
    setError(null)
    try {
      const s = await fn()
      if (s) onChanged(s)
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return false
    } finally {
      setBusy(false)
    }
  }
  if (!status) return <Drawer onClose={onClose}><p className="muted small">불러오는 중…</p></Drawer>
  const c = status.config
  const slotText = slots ?? c.slots.join(', ')
  const dirty = slots !== null || extra !== null

  return (
    <Drawer onClose={onClose}>
      <section className="pilot-head">
        <div className="pilot-state">
          <span className={`pilot-dot${c.enabled ? ' on' : ''}`} aria-hidden />
          <b>{c.enabled ? '켜짐' : '꺼짐'}</b>
          {c.enabled && status.next && <span className="muted small">다음 판단 {status.next.time}</span>}
        </div>
        {readOnly ? <span className="chip">읽기 전용</span> : c.enabled
          ? <button disabled={busy} onClick={() => act(() => investApi.saveAutopilot({ enabled: false }))}>끄기</button>
          : <button className="primary" disabled={busy} onClick={() => act(() => investApi.saveAutopilot({ enabled: true }))}>켜기</button>}
      </section>
      <p className="small muted">
        {c.mode === 'continuous'
          ? <>켜면 리서치 담당이 장 전에 시장·섹터·종목을 훑고, 장이 열려 있는 동안 상시 운용 채널에서 에이전트들이 계속 이야기해요. 종목마다 최근 입장의 과반이 같은 방향이고 반대가 20% 이하면 트레이더가 언제든 </>
          : <>켜면 리서치 담당이 장 전에 시장·섹터·종목을 훑고, 정해진 시각에 {c.flexible ? '새 안건이 있으면' : '언제나'} 투자위원회(에이전트 토론)가 열려요. 종목마다 과반이 같은 방향이고 반대가 20% 이하면 트레이더가 </>}
        <b className="warn-text">승인 없이</b> 토스증권 실계좌로 주문해요. 건당·하루 한도, 종목 비중, 전체 주문 중지는 그대로 지켜요.
        {' '}켜짐 · 꺼짐은 <code>.env</code> 의 <code>MIA_AUTOPILOT</code> 에 저장돼요 (파일을 고쳐도 20초 안에 따라요).
      </p>
      <div className="mode-pick" role="radiogroup" aria-label="운용 방식">
        {MODES.map((m) => (
          <button key={m.key} role="radio" aria-checked={c.mode === m.key} className={`mode-option${c.mode === m.key ? ' on' : ''}`}
            disabled={busy} onClick={() => c.mode !== m.key && act(() => investApi.saveAutopilot({ mode: m.key }))}>
            <b>{m.label}</b><span className="small muted">{m.text}</span>
          </button>
        ))}
      </div>
      {status.stream && (
        <div className={`pilot-stream ${status.stream.state}`}>
          <span className="small">
            <b>{STREAM[status.stream.state]}</b> · {status.stream.note}
            {status.stream.run_id && <> · <button className="link-button small" onClick={() => onOpenSession(status.stream!.run_id!)}>#{status.stream.run_id}</button></>}
          </span>
          {!readOnly && status.stream.state === 'stopped' && (
            <button className="primary" disabled={busy || !status.stream.can_resume} title={status.stream.can_resume ? undefined : '자동 판단 시간 안이고 주문할 수 있을 때 재개할 수 있어요'}
              onClick={() => act(async () => {
                const run = await investApi.resumeAutopilot()
                onOpenSession(run.id)
                return investApi.autopilot()
              })}>재개</button>
          )}
        </div>
      )}
      {status.blocked && <p className="alert warn small">{status.blocked}</p>}
      {status.window_note && <p className="muted small">{status.window_note}</p>}
      {error && <p className="error small">{error}</p>}

      <PolicySection advice={advice} onStarted={onAdviceStarted} />

      <DeskProcess roster={roster} />

      <section>
        <div className="pilot-sec-head">
          <h3>오늘 판단 일정</h3>
          {status.window && <span className="muted small">자동 판단 시간 {status.window[0]}~{status.window[1]}</span>}
        </div>
        <ul className="pilot-entries">
          {status.today.entries.map((e) => (
            <li key={e.key} className={`pilot-entry ${e.status}${status.next?.key === e.key ? ' next' : ''}`}>
              <span className="pilot-time num">{e.time}</span>
              <span className="chip">{kindOf(e)}</span>
              {e.run_id
                ? <button className="link-button small" onClick={() => onOpenSession(e.run_id!)}>{STATUS[e.status]} #{e.run_id}</button>
                : <span className={`small ${e.status === 'pending' ? 'muted' : 'warn-text'}`}>{STATUS[e.status]}</span>}
              {(e.note || e.agenda || e.reason) && <span className="pilot-note muted small">{e.note ?? (e.agenda ? `안건: ${e.agenda}` : e.reason)}</span>}
            </li>
          ))}
        </ul>
        <p className="muted small">
          AI 추가 {status.today.extra_used}/{c.max_extra}번 · 판단이 끝날 때마다 AI 가 시각을 더하거나 다음 고정 시각을 옮길 수 있어요.
          {c.flexible && ' 새 안건(발굴 목록·운용 지시·리스크 경보)이 없으면 그 시각은 건너뛰고, 첫 회의와 마지막 회의는 열어요.'}
          {` 급변 종목은 수시 회의로 (하루 ${status.limits.max_adhoc}번).`}
        </p>
        {c.enabled && (
          <button disabled={busy} onClick={() => act(async () => {
            const run = await investApi.runAutopilot()
            onOpenSession(run.id)
            return investApi.autopilot()
          })}>{c.mode === 'continuous' ? '채널에 지금 판단 요청 (없으면 열기)' : '지금 판단'}</button>
        )}
      </section>

      <section>
        <div className="pilot-sec-head">
          <h3>운용 지시</h3>
          <span className="muted small">{status.intents.length}/{status.limits.max_intents} · 다음 판단부터 반영</span>
        </div>
        <ul className="pilot-intents">
          {status.intents.map((i) => (
            <li key={i.id}>
              <div className="bubble standing"><p>{i.once && <span className="standing-tag">이번만</span>}{i.text}</p></div>
              <span className="muted small">{i.at.slice(5)}</span>
              <button className="icon-button" aria-label="운용 지시 지우기" disabled={busy}
                onClick={() => act(() => investApi.deleteIntent(i.id))}>✕</button>
            </li>
          ))}
          {!status.intents.length && <li className="muted small">예: "반도체 섹터 좀 주목해서" · "공격적으로 해 줘" · "하이닉스 잘 봐 줘"</li>}
        </ul>
        {!readOnly && (
          <form className="pilot-intent-form" onSubmit={async (e) => {
            e.preventDefault()
            if (text.trim() && await act(() => investApi.addIntent(text.trim()))) setText('')
          }}>
            <input value={text} maxLength={500} placeholder="운용 지시 남기기" aria-label="운용 지시" onChange={(e) => setText(e.target.value)} />
            <button className="primary" disabled={busy || !text.trim()}>남기기</button>
          </form>
        )}
        <p className="muted small">채팅방에서 비서에게 말하면 비서가 이 목록을 정리해요(합치기·고치기·이번만). 글에 나온 종목·섹터 이름은 후보에 넣고(종목 → 보유 → 섹터 상위 종목), 나머지 뜻(공격적으로 등)은 에이전트들에게 관리자 메시지로 전해요.</p>
        <button className="link-button small" onClick={() => investApi.autopilotPreview().then(setPreview, (e) => setError(e.message))}>후보 미리 보기</button>
        {preview && (
          <div className="candidate-chips small">
            {preview.candidates.map((cd) => <span key={cd.symbol} className="chip" title={cd.why}>{cd.name} · {cd.why}</span>)}
            {preview.notes.map((n) => <p key={n} className="muted small">{n}</p>)}
          </div>
        )}
      </section>

      <Morning onSent={onBriefingSent} />

      <section>
        <h3>시각 {c.mode === 'continuous' && <span className="muted small">— 정기 회의 방식일 때만 써요</span>}</h3>
        <div className="pilot-settings">
          <label>
            고정 판단 시각 <span className="muted small">쉼표로 · 09:00~15:30</span>
            <input value={slotText} onChange={(e) => setSlots(e.target.value)} />
          </label>
          <label>
            AI 추가 하루 최대
            <input type="number" min={0} max={6} value={extra ?? c.max_extra} onChange={(e) => setExtra(Number(e.target.value))} />
          </label>
        </div>
        <label className="check small">
          <input type="checkbox" checked={c.flexible} disabled={busy}
            onChange={(e) => act(() => investApi.saveAutopilot({ flexible: e.target.checked }))} />
          새 안건이 있을 때만 회의 (끄면 정해진 시각마다 꼭)
        </label>
        {dirty && (
          <button className="primary" disabled={busy} onClick={() => act(async () => {
            const s = await investApi.saveAutopilot({
              slots: slots !== null ? slots.split(/[,\s]+/).filter(Boolean) : undefined,
              max_extra: extra ?? undefined,
            })
            setSlots(null)
            setExtra(null)
            return s
          })}>시각 저장</button>
        )}
      </section>
    </Drawer>
  )
}

/** 아침 브리핑 — 켜기·시각·지금 받아 보기 (주문과 상관없어 PIN 없이) */
function Morning({ onSent }: { onSent: () => void }) {
  const { data, reload } = useLoad(() => investApi.morning(), [])
  const [saved, setSaved] = useState<MorningBriefing | null>(null)
  const [time, setTime] = useState<string | null>(null)
  const [days, setDays] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const m = saved ?? data
  if (!m) return null
  const run = async (fn: () => Promise<unknown>, done?: string) => {
    setBusy(true)
    setNote(null)
    try {
      await fn()
      if (done) setNote(done)
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <section>
      <div className="pilot-sec-head">
        <h3>아침 브리핑</h3>
        <span className="muted small">{m.last ? `마지막 ${m.last.slice(5)}` : '아직 보낸 적 없음'}</span>
      </div>
      <p className="muted small">
        <b>{m.enabled ? m.summary : '꺼짐'}</b> — 서윤(비서)이 한 페이지 보고서(운영 플랜·운용 지시·투자현황·시장)와 오늘 볼 것을 채팅방에 남겨요.
        {m.skip_holidays ? ' 휴장일은 건너뛰어요.' : ''} 채팅으로 "보고는 매주 월요일 8시에"처럼 말해도 바뀌어요.
      </p>
      <div className="weekday-chips" role="group" aria-label="보고 요일">
        {'월화수목금토일'.split('').map((d, i) => {
          const on = m.weekdays.includes(i)
          return (
            <button key={d} className={`chip-toggle${on ? ' on' : ''}`} aria-pressed={on} disabled={busy || (on && m.weekdays.length === 1)}
              onClick={() => run(async () => setSaved(await investApi.saveMorning({ weekdays: on ? m.weekdays.filter((x) => x !== i) : [...m.weekdays, i] })))}>
              {d}
            </button>
          )
        })}
      </div>
      <div className="morning-row">
        <label className="small">매월
          <input className="month-days" value={days ?? m.month_days.join(', ')} placeholder="비우면 날짜 제한 없음 (예: 1, 15)"
            onChange={(e) => setDays(e.target.value)} />일
        </label>
        {days !== null && days !== m.month_days.join(', ') && (
          <button className="primary" disabled={busy} onClick={() => run(async () => {
            const list = days.split(/[,\s]+/).filter(Boolean).map(Number)
            setSaved(await investApi.saveMorning({ month_days: list }))
            setDays(null)
          }, '날짜를 바꿨어요')}>저장</button>
        )}
        <label className="check small">
          <input type="checkbox" checked={m.skip_holidays} disabled={busy}
            onChange={(e) => run(async () => setSaved(await investApi.saveMorning({ skip_holidays: e.target.checked })))} />
          휴장일 건너뛰기
        </label>
      </div>
      <div className="morning-row">
        <label className="check">
          <input type="checkbox" checked={m.enabled} disabled={busy}
            onChange={(e) => run(async () => setSaved(await investApi.saveMorning({ enabled: e.target.checked })))} />
          매일 받기
        </label>
        <input type="time" value={time ?? m.time} min="05:00" max="12:00" aria-label="아침 브리핑 시각" onChange={(e) => setTime(e.target.value)} />
        {time !== null && time !== m.time && (
          <button className="primary" disabled={busy} onClick={() => run(async () => {
            setSaved(await investApi.saveMorning({ time }))
            setTime(null)
          }, '시각을 바꿨어요')}>저장</button>
        )}
        <button disabled={busy} onClick={() => run(async () => {
          await investApi.sendMorning()
          reload()
          onSent()
        }, '채팅방에 보냈어요')}>{busy ? '만드는 중…' : '지금 받아 보기'}</button>
      </div>
      {note && <p className="muted small">{note}</p>}
    </section>
  )
}

function Drawer({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <>
      <div className="room-drawer-backdrop" onClick={onClose} />
      <aside className="room-drawer pilot-drawer" aria-label="자동 운용">
        <div className="room-drawer-head">
          <h2>자동 운용</h2>
          <button className="icon-button" aria-label="닫기" onClick={onClose}>✕</button>
        </div>
        <div className="room-drawer-body scroll-y">{children}</div>
      </aside>
    </>
  )
}
