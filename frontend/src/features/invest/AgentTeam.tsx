/**
 * 실험실 > 투자 에이전트 — 에이전틱 매매에 참여할 에이전트 고르기·편집, 토론 설정(라운드·모델·후보 수),
 * 주문 승인 정책(승인 생략), 최근 세션. 토론 자체는 Invest > AI Trading 화면에서 진행하고 승인한다.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { AgentEditor, RosterCards } from './agents/Roster'
import { investApi, type AgentOptions, type SessionOrder } from './api'
import { MODELS, ORDER_STATUS, sessionLabel, STANCE_LABEL } from './format'
import { useReadOnly } from '../../shared/readOnly'
import { useLoad } from '../../shared/useLoad'
import { STATUS_LABEL } from '../runs/live'

const GATE: Record<string, string> = { aligned: '게이트 열림', conflict: '의견 충돌', no_view: '모두 의견 없음', not_held: '보유 없음', incomplete: '응답 누락' }

export default function AgentTeam() {
  const readOnly = useReadOnly()  // 손님은 승인 방식 · 명단 · 토론 옵션을 보기만
  const [version, setVersion] = useState(0)
  const { data: agents, error: agentsError } = useLoad(() => investApi.agents(), [version])
  const { data: status } = useLoad(() => investApi.status(), [version])
  const { data: sessions } = useLoad(() => investApi.sessions(), [version])
  const [editing, setEditing] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const refresh = () => setVersion((v) => v + 1)

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setMessage(null)
    try {
      await fn()
      setMessage({ ok: true, text: ok })
      refresh()
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : String(e) })
    }
  }

  const toggle = (key: string) =>
    agents && run(() => investApi.saveAgents(agents.roster.map((a) => (a.key === key ? { ...a, enabled: !a.enabled } : a))), '저장했어요')

  const setAuto = (on: boolean) => {
    if (on && !window.confirm('승인 생략을 켜면 트레이더가 결정하는 즉시 토스증권 실계좌로 실제 주문이 나가요. 켤까요?')) return
    run(() => investApi.saveTrading({ agent_auto_approve: on }), on ? '승인 생략을 켰어요' : '이제 주문마다 승인을 받아요')
  }

  const auto = !!status?.agent_auto_approve
  return (
    <fieldset className="readonly-fieldset" disabled={readOnly}>
      <p className="notice small">
        🤝 <b>Invest &gt; AI Trading</b> 채팅방에서 쓰는 에이전트와 토론 설정을 여기서 정해요. 성향(프롬프트)과 도구(행동)가 다른 분석 에이전트들이
        한 종목·섹터·전체(보유+관심) 범위로 토론하고, 종목마다 모두 한 방향일 때만 트레이더가 주문을 제안해요. 이 기능은 수익을 보장하지 않아요 —
        의사결정 근거를 펼쳐 보이는 실험이에요.
      </p>
      {message && <p className={message.ok ? 'ok-text small' : 'error small'} role="status">{message.text}</p>}

      <article className="card">
        <h2>
          주문 승인
          <span className={`health-badge ${auto ? 'warn' : 'ok'}`}>{auto ? '승인 생략' : '사용자 승인'}</span>
        </h2>
        <div className="segmented">
          <button className={!auto ? 'on' : ''} onClick={() => auto && setAuto(false)}>관리자 승인 후 주문</button>
          <button className={auto ? 'on danger' : ''} onClick={() => !auto && setAuto(true)}>승인 생략 (바로 주문)</button>
        </div>
        <p className="muted small">
          기본은 트레이더의 매수·매도 제안을 채팅방에서 <b>내가 승인</b>해야 주문돼요. 승인 생략을 켜면 트레이더가 바로 주문해요
          {' '}— 실계좌라 실제 돈이 움직여요. 이 설정을 바꿀 때는 주문 PIN 을 물어요.{' '}
          어느 쪽이든 주문 한도·잔고·전체 중지는 코드가 다시 검사해요.
        </p>
      </article>

      {agents && <OptionsCard key={JSON.stringify(agents.options)} options={agents.options}
        onSave={(o) => run(() => investApi.saveAgentOptions(o), '토론 설정을 저장했어요')} />}

      <article className="card">
        <div className="section-head">
          <h2>에이전트 구성</h2>
          <button onClick={() => setEditing(true)}>편집</button>
        </div>
        {agentsError && <p className="error small">{agentsError}</p>}
        {agents && <RosterCards data={agents} onToggle={toggle} />}
        <p className="muted small">스위치로 토론 참여를 끄고 켜요. 실행 담당(트레이더)은 한 명이어야 하고, 분석 에이전트는 두 명 이상이어야 해요.</p>
      </article>

      <article className="card">
        <h2>최근 세션</h2>
        {!sessions?.length && <p className="muted small">아직 없어요. Invest &gt; AI Trading에서 토론을 시작하세요.</p>}
        <ul className="plain-list session-list">
          {sessions?.slice(0, 12).map((r) => {
            const s = r.summary ?? {}
            const orders = (r as typeof r & { orders_now?: SessionOrder[] }).orders_now ?? []  // 승인·거절 뒤 현재 상태
            return (
              <li key={r.id}>
                <Link to={`/invest/ai?session=${r.id}`}>
                  #{r.id} {sessionLabel(r)}
                </Link>{' '}
                <span className="muted small">{r.created_at.slice(5, 16)}</span>{' '}
                <span className={`chip run-${r.status}`}>{STATUS_LABEL[r.status]}</span>{' '}
                {r.status === 'succeeded' && (
                  <span className="small">
                    {GATE[s.gate as string] ?? '–'}
                    {orders.length
                      ? ' · ' + orders.map((o) => `${o.name} ${STANCE_LABEL[o.side]} ${o.quantity}주 ${ORDER_STATUS[o.status]}`).join(', ')
                      : ' · 행동 안 함'}
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      </article>

      {editing && agents && <AgentEditor data={agents} onClose={() => setEditing(false)} onSaved={refresh} />}
    </fieldset>
  )
}

/** 토론 설정 — 라운드·모델·후보 종목 수 (AI Trading 채팅방은 이 설정으로 토론한다) */
function OptionsCard({ options, onSave }: { options: AgentOptions; onSave: (o: AgentOptions) => void }) {
  const [o, setO] = useState(options)
  const changed = JSON.stringify(o) !== JSON.stringify(options)
  return (
    <article className="card">
      <h2>토론 설정</h2>
      <div className="grid">
        <label className="field">
          <span>라운드</span>
          <select value={o.rounds} onChange={(e) => setO({ ...o, rounds: Number(e.target.value) })}>
            <option value={1}>1라운드 — 독립 의견만</option>
            <option value={2}>2라운드 — 한 번 설득·반박</option>
            <option value={3}>3라운드 — 두 번 설득·반박</option>
          </select>
        </label>
        <label className="field">
          <span>모델</span>
          <select value={o.model} onChange={(e) => setO({ ...o, model: e.target.value })}>
            {MODELS.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
          </select>
        </label>
        <label className="field">
          <span>후보 종목 수 (섹터·전체 토론)</span>
          <select value={o.max_candidates} onChange={(e) => setO({ ...o, max_candidates: Number(e.target.value) })}>
            {[2, 3, 4, 5, 6, 8, 10].map((n) => <option key={n} value={n}>최대 {n}종목</option>)}
          </select>
        </label>
      </div>
      <p className="muted small">
        라운드·후보가 늘수록 LLM 호출과 비용이 늘어요(분석 에이전트 수 × 라운드 + 트레이더). 섹터 토론은 시가총액 상위 종목과 관련 ETF,
        전체 토론은 보유 종목 먼저 관심 종목 순으로 후보를 채워요.
      </p>
      <div className="form-actions">
        <button className="primary" disabled={!changed} onClick={() => onSave(o)}>저장</button>
      </div>
    </article>
  )
}
