/**
 * 운용 목표 · 자문 상담 — 채팅 입력(상담 중이면 자문 에이전트에게: 위에 상담 막대 — 항목마다 기록했는지·상담 끝내기)과
 * 자동 운용 서랍의 '운용 목표'(항목별 지금 값·아직 안 정한 것·최근 바뀐 기록, 자문 에이전트와 상담 시작).
 * 운용 목표는 대화로만 바꾼다 — 자문 에이전트가 묻고 비서가 반영(바뀔 때마다 채팅방에 '설정 변경' 줄).
 */
import { useState } from 'react'
import { investApi, type AdviceStatus } from '../api'
import { useLoad } from '../../../shared/useLoad'
import { Composer } from './chat'
import { AgentAvatar } from './Face'

const BY: Record<string, string> = { assistant: '비서', advisor: '자문 상담 그대로', manual: '직접' }

/** 아이들 입력창 — 상담 중이면 자문 에이전트에게 답하기 */
export function AskComposer({ advice, onAsk, onChanged, hint }: {
  advice: AdviceStatus | null
  onAsk: (text: string) => Promise<boolean>
  /** 상담을 끝냈을 때 — 대화를 바로 다시 받는다 */
  onChanged: () => void
  hint: string
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (!advice?.open) return <Composer onSend={onAsk} placeholder={hint} label="비서에게 말하기" />
  const p = advice.profile
  const stop = async () => {
    setBusy(true)
    setError(null)
    try {
      await investApi.stopAdvice()
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <div className="advice-bar" role="status">
        <AgentAvatar p={p} className="tiny" label={false} />
        <span className="advice-bar-text small">
          <b>운용 목표 상담 중</b> · {p.name}({p.title}) · 남은 질문 {advice.remaining}
          <span className="advice-dots" aria-label={`${advice.topics.length - advice.remaining}/${advice.topics.length} 기록`}>
            {advice.topics.map((t) => <i key={t.key} className={t.done ? 'on' : ''} title={`${t.label}${t.done ? ' · 기록함' : ''}`} />)}
          </span>
        </span>
        <button className="link-button small" disabled={busy} onClick={stop}>{busy ? '마치는 중…' : '상담 끝내기'}</button>
        {error && <span className="error small">{error}</span>}
      </div>
      <Composer onSend={onAsk} label="자문 에이전트에게 답하기"
        placeholder={`${p.name}(자문)에게 답하기 — 위 보기를 누르거나 직접 적어 주세요 · '그만'이면 지금까지로 마쳐요 (Enter 전송)`} />
    </>
  )
}

/** 상시 운용 채널을 보고 있을 때 입력창 — 에이전트들에게(채널, 다음 대화에 바로) / 비서에게(자문 상담 중이면 자문 에이전트) */
export function StreamComposer({ onSend, advice, onAsk, onChanged, hint }: {
  onSend: (text: string) => Promise<boolean>
  advice: AdviceStatus | null
  onAsk: (text: string) => Promise<boolean>
  onChanged: () => void
  hint: string
}) {
  const [to, setTo] = useState<'agents' | 'assistant'>(advice?.open ? 'assistant' : 'agents')
  return (
    <>
      <div className="composer-target" role="tablist" aria-label="누구에게">
        <button role="tab" aria-selected={to === 'agents'} className={`chip-toggle${to === 'agents' ? ' on' : ''}`} onClick={() => setTo('agents')}>
          에이전트들에게
        </button>
        <button role="tab" aria-selected={to === 'assistant'} className={`chip-toggle${to === 'assistant' ? ' on' : ''}`}
          onClick={() => setTo('assistant')}>
          비서에게{advice?.open ? ' · 자문 상담 중' : ''}
        </button>
      </div>
      {to === 'agents' ? (
        <Composer onSend={onSend} label="상시 운용 채널에 말하기"
          placeholder="에이전트들에게 — 다음 대화에 바로 반영돼요 (예: 하이닉스 지금 어때? · 현금 30%는 남겨 둬) (Enter 전송)" />
      ) : (
        <AskComposer advice={advice} onAsk={onAsk} onChanged={onChanged} hint={hint} />
      )}
    </>
  )
}

/** 자동 운용 서랍 — 운용 목표 */
export function PolicySection({ advice, onStarted }: { advice: AdviceStatus | null; onStarted: () => void }) {
  const { data, error } = useLoad(() => investApi.policy(), [advice?.open])
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  if (!data) return error ? <p className="error small">{error}</p> : null
  const start = async (topics?: string[]) => {
    setBusy(true)
    setNote(null)
    try {
      await investApi.startAdvice(topics)
      onStarted()
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  const set = data.fields.filter((f) => f.text !== null)
  const missing = data.fields.filter((f) => f.required && f.text === null)
  return (
    <section className="policy-section">
      <div className="pilot-sec-head">
        <h3>운용 목표</h3>
        <span className="muted small">
          {data.updated_at ? `${data.updated_at.slice(5)} · ${BY[data.updated_by ?? ''] ?? data.updated_by}` : '아직 정하지 않음'}
        </span>
      </div>
      {set.length > 0 ? (
        <dl className="policy-list small">
          {set.map((f) => (
            <div key={f.key}><dt className="muted">{f.label}</dt><dd>{f.text}</dd></div>
          ))}
        </dl>
      ) : (
        <p className="small muted">모든 에이전트(리서치·토론·트레이더·일정)가 판단 기준으로 쓰는 투자 정책이에요. 자문 에이전트가 대화로 여쭤봐요.</p>
      )}
      {missing.length > 0 && <p className="small warn-text">아직 안 정함: {missing.map((f) => f.label).join(', ')}</p>}
      {data.history.length > 0 && (
        <ul className="policy-history small muted">
          {data.history.slice(0, 3).map((h) => (
            <li key={h.at + h.changes.map((c) => c.field).join()}>
              {h.at.slice(5)} {BY[h.by] ?? h.by} — {h.changes.map((c) => `${c.label} ${c.before} → ${c.after}`).join(', ')}
            </li>
          ))}
        </ul>
      )}
      <div className="desk-actions">
        {advice?.open ? (
          <span className="small muted">상담 중이에요 — 채팅방에서 답해 주세요</span>
        ) : (
          <>
            <button className="primary" disabled={busy} onClick={() => start()}>자문 에이전트와 상담</button>
            {missing.length > 0 && set.length > 0 && (
              <button disabled={busy} onClick={() => start(missing.map((f) => f.key))}>안 정한 것만</button>
            )}
          </>
        )}
        {note && <span className="error small">{note}</span>}
      </div>
    </section>
  )
}
