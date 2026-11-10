/**
 * 설정 — 투자(토스 · OpenDART 키, 계좌, 주문 조건 · 한도, 전체 중지) · LLM(기본 LLM, 로컬 MLX, 경로, vLLM) · 시스템 이슈 · 공유 · 접근.
 * 손님(읽기 전용)은 값을 보기만 한다 — 입력 · 버튼을 모두 잠그고, 키 칸(서버가 보내지 않음)과 공유 탭은 없다.
 */
import { useSearchParams } from 'react-router-dom'
import { useReadOnly } from '../../shared/readOnly'
import InvestSettings from '../invest/InvestSettings'
import LlmSettings from './LlmSettings'
import RoutesSettings from './RoutesSettings'
import ShareSettings from './ShareSettings'
import SystemIssues from './SystemIssues'

const TABS = [
  { key: 'invest', label: '투자', admin: false },
  { key: 'llm', label: 'LLM', admin: false },
  { key: 'system', label: '시스템 이슈', admin: false },
  { key: 'share', label: '공유 · 접근', admin: true },
] as const

export default function SettingsPage() {
  const readOnly = useReadOnly()
  const [params, setParams] = useSearchParams()
  const tabs = TABS.filter((t) => !readOnly || !t.admin)
  const tab = tabs.find((t) => t.key === params.get('tab'))?.key ?? 'invest'
  return (
    <div className="page">
      <h1>Settings</h1>
      <div className="tabs" role="tablist">
        {tabs.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key} className={tab === t.key ? 'on' : ''}
            onClick={() => setParams({ tab: t.key })}>
            {t.label}
          </button>
        ))}
      </div>
      {readOnly && <p className="readonly-banner small">읽기 전용 — 설정값을 볼 수만 있어요. 키와 공유 설정은 관리자만 봐요.</p>}
      <fieldset className="readonly-fieldset" disabled={readOnly}>
        {tab === 'invest' ? <InvestSettings /> : tab === 'llm' ? <><LlmSettings /><RoutesSettings /></>
          : tab === 'system' ? <SystemIssues /> : <ShareSettings />}
      </fieldset>
    </div>
  )
}
