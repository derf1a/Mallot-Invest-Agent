/**
 * 토론 현황 서랍 (카카오톡 채팅방 서랍처럼 오른쪽에서 열린다) — 맨 위 중심은 **보유 종목 지표 표**(Holdings), 그 아래 수익률·회의록·
 * 후보 × 에이전트 최신 입장표와 종목별 게이트·세션 정보.
 * 참여자 목록은 채팅방 왼쪽 위 참여자 수를 누르면 여는 서랍(Members)에.
 * 진행 단계는 채팅방 안의 안내 줄·카드로 보여주고, 여기서는 한눈에 보는 표만. 게이트 규칙: 종목마다 분석 에이전트의 최종 입장이 한 방향(+ 의견 없음)일 때만 열린다.
 */
import { Link } from 'react-router-dom'
import { isActive } from '../../runs/live'
import type { Agent, Stance, SymbolGate } from '../api'
import { STANCE_LABEL } from '../format'
import { Minutes } from './chat'
import HoldingsTable from './Holdings'
import PerformanceView from './Performance'
import { AgentAvatar } from './Face'
import { nameOf, withNames } from './people'
import { candidatesOf, findLast, GATE_SHORT, viewsOf, type Chat } from './session'

interface LatestView {
  stance: Stance
  confidence: number
  reason: string
  round: number
}

export default function RoomInfo({ chat, roster }: { chat: Chat; roster: Agent[] }) {
  const { messages, run } = chat
  if (!run) return <p className="muted small">불러오는 중…</p>

  const start = findLast(messages, 'start')
  const agents = withNames(start?.agents ?? [], roster)
  const analysts = agents.filter((a) => !a.executor)
  const candidates = candidatesOf(messages, run)
  const gate = findLast(messages, 'gate')?.gate
  const active = isActive(run.status)

  // 에이전트 → 종목 → 최신 입장 (나중 라운드가 덮어쓴다)
  const latest = new Map<string, Map<string, LatestView>>()
  for (const m of messages) {
    if (m.type !== 'statement' || m.error) continue
    const per = latest.get(m.agent) ?? new Map<string, LatestView>()
    for (const v of viewsOf(m, candidates)) per.set(v.symbol, { stance: v.stance, confidence: v.confidence, reason: v.reason, round: m.round })
    latest.set(m.agent, per)
  }
  const gateOf = (symbol: string): SymbolGate | undefined => gate?.symbols?.[symbol] ?? (candidates.length === 1 ? gate : undefined)

  const minutes = findLast(messages, 'minutes')
  // 보유 종목 표의 '에이전트' 칸 — 이 회의 분석 에이전트 최신 입장 수
  const counts = new Map<string, Record<Stance, number>>()
  for (const per of latest.values()) {
    for (const [sym, v] of per) {
      const c = counts.get(sym) ?? { buy: 0, sell: 0, none: 0 }
      c[v.stance] += 1
      counts.set(sym, c)
    }
  }
  return (
    <div className="room-info">
      <section className="holdings-section">
        <h3>보유 종목 <span className="muted small">지금 시세·지표</span></h3>
        <HoldingsTable views={counts} />
      </section>
      <section>
        <h3>수익률</h3>
        <PerformanceView />
      </section>
      {minutes && (
        <section>
          <h3>회의록</h3>
          <Minutes m={minutes} />
        </section>
      )}

      {candidates.length > 0 && analysts.length > 0 && (
        <section>
          <h3>입장표 <span className="muted small">최신 라운드 기준</span></h3>
          <div className="stance-table-wrap scroll-x">
            <table className="stance-table">
              <thead>
                <tr>
                  <th scope="col">후보</th>
                  {analysts.map((a) => (
                    <th key={a.key} scope="col" title={`${nameOf(a)} · ${a.title}`}>
                      <AgentAvatar p={a} className="mini" label={false} />
                    </th>
                  ))}
                  <th scope="col">게이트</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((c) => {
                  const g = gateOf(c.symbol)
                  return (
                    <tr key={c.symbol}>
                      <th scope="row" title={c.symbol}>{c.name}</th>
                      {analysts.map((a) => {
                        const v = latest.get(a.key)?.get(c.symbol)
                        return (
                          <td key={a.key} title={v ? `${a.title} · ${STANCE_LABEL[v.stance]} (확신 ${v.confidence.toFixed(2)}, 라운드 ${v.round})${v.reason ? `\n${v.reason}` : ''}` : `${a.title} · 아직`}>
                            {v ? <span className={`stance ${v.stance}`}>{v.stance === 'none' ? '–' : STANCE_LABEL[v.stance]}</span> : <span className="muted">…</span>}
                          </td>
                        )
                      })}
                      <td>
                        {g ? (
                          <span className={`chip gate-${g.status}`}>
                            {GATE_SHORT[g.status]}
                            {g.direction && <span className="gate-dir">{STANCE_LABEL[g.direction]}</span>}
                          </span>
                        ) : <span className="muted small">{active ? '진행 중' : '–'}</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <p className="muted small legend-line">
            {analysts.map((a) => <span key={a.key} className="legend-person"><AgentAvatar p={a} className="mini" label={false} /> {nameOf(a)}</span>)}
          </p>
        </section>
      )}

      {start && (
        <section>
          <h3>세션</h3>
          <p className="muted small">
            #{run.id} · {start.rounds}라운드 · {start.model || '기본 모델'} · 분석 {analysts.length}명 + 트레이더 · 실계좌 ·{' '}
            {start.auto_approve ? <b className="warn-text">승인 생략</b> : '관리자 승인'}
            {start.provider === 'sample' && ' · 샘플 시세'}
          </p>
          {gate?.reason && <p className="small">{gate.reason}</p>}
        </section>
      )}
      <Link className="small" to="/invest/team">에이전트 구성·토론 설정 (실험실) →</Link>
      <p className="muted small">에이전트 토론이 수익을 높인다고 보장하지 않아요. 판단과 책임은 관리자에게 있어요.</p>
    </div>
  )
}
