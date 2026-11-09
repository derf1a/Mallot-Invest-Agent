/** 설정 — 투자(토스 · OpenDART 키, 계좌, 주문 조건 · 한도, 전체 중지) · LLM(기본 LLM, 로컬 MLX, 경로, vLLM) */
import { useSearchParams } from 'react-router-dom'
import InvestSettings from '../invest/InvestSettings'
import LlmSettings from './LlmSettings'
import RoutesSettings from './RoutesSettings'

const TABS = [
  { key: 'invest', label: '투자' },
  { key: 'llm', label: 'LLM' },
] as const

export default function SettingsPage() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'llm' ? 'llm' : 'invest'
  return (
    <div className="page">
      <h1>Settings</h1>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key} className={tab === t.key ? 'on' : ''}
            onClick={() => setParams({ tab: t.key })}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'invest' ? <InvestSettings /> : <><LlmSettings /><RoutesSettings /></>}
    </div>
  )
}
