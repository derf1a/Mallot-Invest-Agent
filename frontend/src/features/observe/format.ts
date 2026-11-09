/** Observe 화면 표기 — 에이전트 이름, 시각, 걸린 시간 */

// 기본 명단 키 → 직함 (직접 만든 에이전트는 키 그대로)
const TITLES: Record<string, string> = {
  technical: '차티스트', value: '가치 투자자', news: '뉴스 애널리스트', growth: '모멘텀 투자자', portfolio: '포트폴리오 매니저',
  risk: '리스크 매니저', trader: '트레이더', strategist: '시장 전략가', sector: '섹터 애널리스트', scout: '종목 발굴 애널리스트',
  assistant: '비서', advisor: '자문 에이전트', scheduler: '일정 담당',
}

export const agentLabel = (key: string | null | undefined) => (key ? TITLES[key] ?? key : '')

const two = (n: number) => String(n).padStart(2, '0')

export function fmtTime(ms: number | null | undefined): string {
  if (!ms) return ''
  const d = new Date(ms)
  return `${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`
}

export function fmtDur(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '…'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`
}

export const fmtUsd = (v: number) => (v ? `$${v < 0.01 ? v.toFixed(4) : v.toFixed(2)}` : '–')

export const SPAN_KIND_LABEL: Record<string, string> = {
  run: '실행', step: '단계', agent: '에이전트', llm: 'LLM', tool: '도구', event: '사건',
}
