/** 새 토론 팝업 — 범위(한 종목·섹터·전체)와 후보 미리 보기, 참여 에이전트, 첫 메시지를 정하고 시작한다 (주문 PIN 은 서버가 요구할 때 묻는다) */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import Modal from '../../../shared/ui/Modal'
import type { Run } from '../../runs/api'
import { ApiError, investApi, type AgentsResponse, type DebateScope } from '../api'
import { MODELS } from '../format'
import SymbolSearch from '../SymbolSearch'
import { AgentAvatar } from './Face'
import { useLoad } from '../../../shared/useLoad'

const SCOPES: { key: DebateScope; label: string }[] = [
  { key: 'symbol', label: '한 종목' },
  { key: 'sector', label: '섹터' },
  { key: 'portfolio', label: '전체' },
  { key: 'scan', label: '시장 스캔' },
]

export default function NewDebate({ agents, blockers, autoApprove, active, initialSymbol, onClose, onStarted, onShowActive }: {
  agents: AgentsResponse | null
  blockers: string[]
  autoApprove: boolean
  active: Run | null
  initialSymbol: string
  onClose: () => void
  onStarted: (id: number) => void
  onShowActive: () => void
}) {
  const [scope, setScope] = useState<DebateScope>('symbol')
  const [symbol, setSymbol] = useState(initialSymbol)
  const [sectorId, setSectorId] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const { data: sectors } = useLoad(() => (scope === 'sector' ? investApi.sectors() : Promise.resolve(null)), [scope])
  const pickedSector = sectorId || sectors?.[0]?.id || ''
  const { data: preview, error: previewError } = useLoad(
    () => (scope === 'sector' && !pickedSector ? Promise.resolve(null) : investApi.scopePreview(scope, { symbol, sector_id: pickedSector })),
    [scope, symbol, pickedSector],
  )
  const canStart = !active && !blockers.length && !!preview && !busy

  const start = async () => {
    setError(null)
    setBusy(true)
    try {
      const run = await investApi.createSession({ scope, symbol, sector_id: pickedSector, note: note.trim() })
      onStarted(run.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      if (e instanceof ApiError && e.code === 'session_running') onShowActive()
    } finally {
      setBusy(false)
    }
  }

  const roster = agents?.roster.filter((a) => a.enabled) ?? []
  return (
    <Modal title="새 토론" onClose={onClose}>
      <div className="new-debate">
        {active && (
          <p className="badge warn">
            #{active.id} 토론이 진행 중이에요. 토론은 한 번에 하나만 — 끝나거나 중지한 뒤 시작할 수 있어요.{' '}
            <button className="link" onClick={onShowActive}>보러 가기</button>
          </p>
        )}

        <section>
          <h3>범위</h3>
          <div className="segmented" role="tablist" aria-label="토론 범위">
            {SCOPES.map((x) => (
              <button key={x.key} role="tab" aria-selected={scope === x.key} className={scope === x.key ? 'on' : ''} onClick={() => setScope(x.key)}>
                {x.label}
              </button>
            ))}
          </div>
          {scope === 'symbol' && (
            <SymbolSearch placeholder={`종목 바꾸기 (지금 ${preview?.candidates[0]?.name ?? symbol})`} onPick={(h) => setSymbol(h.symbol)} />
          )}
          {scope === 'sector' && (
            <select value={pickedSector} onChange={(e) => setSectorId(e.target.value)} aria-label="섹터">
              {(sectors ?? []).map((x) => (
                <option key={x.id} value={x.id}>{x.depth ? `　└ ${x.name}` : x.name}</option>
              ))}
            </select>
          )}
          {scope === 'portfolio' && <p className="muted small">토스 보유 국내 종목 → 관심 종목 순서로 후보를 정해요.</p>}
          {scope === 'scan' && (
            <p className="muted small">
              보유·관심·운용 지시의 종목·섹터와 시장 랭킹(거래대금·상승·거래량 상위)을 수십 종목 훑어 점수를 매기고, 회의를 시작하면 포트폴리오 매니저가
              운용 지시를 보고 후보를 골라요. 아래 미리 보기는 점수 순이에요.
            </p>
          )}
          {previewError && <p className="error small">{previewError}</p>}
          {preview && (
            <div className="candidate-chips small">
              <span className="muted">후보 {preview.candidates.length}</span>
              {preview.candidates.map((c) => (
                <span key={c.symbol} className="chip">
                  {c.name}{c.security_type === 'ETF' ? ' · ETF' : ''}{c.held ? ` · 보유 ${c.held}` : ''}
                </span>
              ))}
              {preview.notes.map((n) => <span key={n} className="muted">{n}</span>)}
            </div>
          )}
        </section>

        {agents && (
          <section>
            <h3>참여 에이전트 <span className="muted small">{roster.length}</span></h3>
            <ul className="member-chips">
              {roster.map((a) => (
                <li key={a.key} title={a.persona}>
                  <AgentAvatar p={a} />
                  <span>{a.name ? <><b>{a.name}</b> <span className="muted">{a.title}</span></> : a.title}</span>
                </li>
              ))}
            </ul>
            <p className="muted small">
              {agents.options.rounds}라운드 · {MODELS.find((m) => m.key === agents.options.model)?.label ?? agents.options.model} · 후보 최대{' '}
              {agents.options.max_candidates} · <Link to="/invest/team">실험실에서 바꾸기 →</Link>
            </p>
          </section>
        )}

        <section>
          <h3>첫 메시지 <span className="muted small">선택</span></h3>
          <textarea rows={2} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} aria-label="첫 메시지"
            placeholder="예: 단기 반등만 노려요, 현금은 30% 남겨 둬요" />
        </section>

        {blockers.length > 0 && <p className="badge warn">토론을 시작할 수 없어요: {blockers.join(' · ')}</p>}
        {error && <p className="error small">{error}</p>}
        <div className="form-actions">
          <span className="muted small new-debate-note">
            토스증권 실계좌 · {autoApprove ? <b className="warn-text">승인 생략이 켜져 있어 바로 주문돼요</b> : '내가 승인해야 주문돼요'}
          </span>
          <button onClick={onClose}>취소</button>
          <button className="primary" disabled={!canStart} onClick={start}>{busy ? '시작하는 중…' : '토론 시작'}</button>
        </div>
      </div>
    </Modal>
  )
}
