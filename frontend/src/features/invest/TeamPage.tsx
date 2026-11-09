/**
 * Team — 토론에 참여할 에이전트 고르기 · 편집, 토론 설정(라운드 · 모델 · 후보 수), 주문 승인 정책, 최근 세션 (AgentTeam)
 * 과 판단 기록(시장 스캔 · 발굴 · 가치 스크린 · 섹터 평가의 점수표, Scores), 에이전트별 성과 · 표의 무게(AgentWeights).
 * Mallot 에서는 실험실 탭이었다.
 */
import { useSearchParams } from 'react-router-dom'
import AgentTeam from './AgentTeam'
import AgentWeights from './AgentWeights'
import Scores from './Scores'

const TABS = [
  { key: 'agents', label: '에이전트 · 토론 설정' },
  { key: 'weights', label: '성과 · 가중치' },
  { key: 'scores', label: '판단 기록' },
] as const

export default function TeamPage() {
  const [params, setParams] = useSearchParams()
  const tab = TABS.find((t) => t.key === params.get('tab'))?.key ?? 'agents'
  return (
    <div className="page">
      <h1>Team</h1>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key} className={tab === t.key ? 'on' : ''}
            onClick={() => setParams(t.key === 'agents' ? {} : { tab: t.key })}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'agents' ? <AgentTeam /> : tab === 'weights' ? <AgentWeights /> : <Scores />}
    </div>
  )
}
