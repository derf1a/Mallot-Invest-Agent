/** 에이전트 명단 카드·편집기 — 실험실 '투자 에이전트' 탭에서 고르고 편집한다 */
import { useState } from 'react'
import Modal from '../../../shared/ui/Modal'
import { investApi, type Agent, type AgentsResponse } from '../api'
import { AgentAvatar } from './Face'

export function RosterCards({ data, onToggle }: { data: AgentsResponse; onToggle?: (key: string) => void }) {
  const toolTitle = (name: string) => data.tools.find((t) => t.name === name)?.title ?? name
  return (
    <div className="agent-cards">
      {data.roster.map((a) => (
        <div key={a.key} className={`agent-card${a.enabled ? '' : ' off'}${a.executor ? ' executor' : ''}`}>
          <div className="agent-head">
            <AgentAvatar p={a} />
            <span className="agent-who">
              <strong>{a.name ?? a.title}</strong>
              {a.name && <span className="small">{a.title}</span>}
              {a.name && <span className="muted small">{a.full_name}{a.country ? ` · ${a.country}` : ''}</span>}
            </span>
            {a.executor && <span className="badge info">실행 담당</span>}
            {onToggle && (
              <label className="switch" title={a.enabled ? '토론에서 빼기' : '토론에 넣기'}>
                <input type="checkbox" checked={a.enabled} disabled={a.executor} onChange={() => onToggle(a.key)} aria-label={`${a.title} 사용`} />
              </label>
            )}
          </div>
          <p className="small agent-persona">{a.persona}</p>
          <div className="badges">
            {a.tools.map((t) => (
              <span key={t} className={`chip tool${t === 'place_order' ? ' order' : ''}`}>{toolTitle(t)}</span>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

export function AgentEditor({ data, onClose, onSaved }: { data: AgentsResponse; onClose: () => void; onSaved: () => void }) {
  const [roster, setRoster] = useState<Agent[]>(data.roster.map((a) => ({ ...a, tools: [...a.tools] })))
  const [open, setOpen] = useState<string | null>(roster[0]?.key ?? null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const update = (key: string, patch: Partial<Agent>) => setRoster((r) => r.map((a) => (a.key === key ? { ...a, ...patch } : a)))

  const add = () => {
    let n = 1
    while (roster.some((a) => a.key === `custom${n}`)) n += 1
    const agent: Agent = { key: `custom${n}`, title: '새 에이전트', persona: '', tools: ['get_quote'], executor: false, enabled: true }
    const execIdx = roster.findIndex((a) => a.executor)
    setRoster((r) => (execIdx < 0 ? [...r, agent] : [...r.slice(0, execIdx), agent, ...r.slice(execIdx)]))
    setOpen(agent.key)
  }

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      await investApi.saveAgents(roster)
      onSaved()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const reset = async () => {
    if (!window.confirm('에이전트 명단을 기본값으로 되돌릴까요?')) return
    await investApi.resetAgents()
    onSaved()
    onClose()
  }

  return (
    <Modal title="에이전트 편집" onClose={onClose} wide>
      <p className="muted small">
        성향(프롬프트)과 도구(행동)를 에이전트마다 다르게 줄 수 있어요. 주문 도구는 실행 담당 한 명만 가질 수 있고, 분석 에이전트는 두 명 이상이어야 해요.
      </p>
      <div className="agent-editor">
        {roster.map((a) => (
          <div key={a.key} className={`agent-edit${open === a.key ? ' open' : ''}`}>
            <button className="agent-edit-head" onClick={() => setOpen(open === a.key ? null : a.key)} aria-expanded={open === a.key}>
              <AgentAvatar p={a} className="mini" label={false} />
              <strong>{a.name ? `${a.name} · ${a.title}` : a.title}</strong>
              {a.executor && <span className="badge info">실행 담당</span>}
              {!a.enabled && <span className="badge">꺼짐</span>}
              <span className="muted small">{a.tools.length}개 도구</span>
            </button>
            {open === a.key && (
              <div className="agent-edit-body">
                <div className="agent-name-fields">
                  <label className="field">
                    <span>직함 <span className="muted small">토론에서 서로 부르는 이름</span></span>
                    <input value={a.title} maxLength={30} onChange={(e) => update(a.key, { title: e.target.value })} />
                  </label>
                  <label className="field">
                    <span>이름</span>
                    <input value={a.name ?? ''} maxLength={20} placeholder="비우면 자동으로" onChange={(e) => update(a.key, { name: e.target.value })} />
                  </label>
                  <label className="field">
                    <span>전체 이름</span>
                    <input value={a.full_name ?? ''} maxLength={40} onChange={(e) => update(a.key, { full_name: e.target.value })} />
                  </label>
                  <label className="field">
                    <span>국적</span>
                    <input value={a.country ?? ''} maxLength={20} onChange={(e) => update(a.key, { country: e.target.value })} />
                  </label>
                </div>
                <label className="field">
                  <span>성향 (프롬프트)</span>
                  <textarea rows={4} maxLength={1500} value={a.persona} onChange={(e) => update(a.key, { persona: e.target.value })}
                    placeholder="예: 배당과 현금흐름을 중시하는 보수적인 투자자. 고평가 종목은 반대한다." />
                </label>
                <fieldset className="tool-checks">
                  <legend className="small">도구 (행동)</legend>
                  {data.tools.filter((t) => !t.executor_only || a.executor).map((t) => (
                    <label key={t.name} className="check" title={t.description}>
                      <input type="checkbox" checked={a.tools.includes(t.name)} disabled={t.executor_only}
                        onChange={(e) => update(a.key, { tools: e.target.checked ? [...a.tools, t.name] : a.tools.filter((x) => x !== t.name) })} />
                      {t.title}
                      <span className="muted small"> — {t.description}</span>
                    </label>
                  ))}
                </fieldset>
                <div className="form-actions">
                  <label className="check">
                    <input type="checkbox" checked={a.enabled} onChange={(e) => update(a.key, { enabled: e.target.checked })} />
                    토론에 참여
                  </label>
                  {!a.executor && (
                    <button onClick={() => setRoster((r) => r.filter((x) => x.key !== a.key))}>삭제</button>
                  )}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
      {error && <p className="error small">{error}</p>}
      <div className="form-actions">
        <button onClick={add}>＋ 분석 에이전트 추가</button>
        <button onClick={reset}>기본값으로</button>
        <button className="primary" disabled={busy} onClick={save}>저장</button>
      </div>
    </Modal>
  )
}
