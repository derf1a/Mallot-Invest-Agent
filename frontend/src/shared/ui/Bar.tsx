interface Props {
  value: number
  max: number
  /** budget: 많이 쓸수록 경고 색 / progress: 납부 진행률이라 항상 기본 색 */
  kind?: 'budget' | 'progress'
  /** 기본(정상) 상태 색 */
  tone?: 'ok' | 'saving' | 'fixed' | 'variable'
  /** 지금까지 써도 되는 위치 (0~1, 기간이 지난 비율) — 세로 눈금으로 표시 */
  pace?: number
}

/** 예산 소진 막대. 80% 이상은 주의, 100% 초과는 경고 색 */
export default function Bar({ value, max, kind = 'budget', tone = 'ok', pace }: Props) {
  const ratio = max > 0 ? value / max : value > 0 ? 1.01 : 0
  const level = kind === 'progress' ? tone : ratio > 1 ? 'over' : ratio >= 0.8 ? 'warn' : tone
  return (
    <div className="bar" role="progressbar" aria-valuenow={value} aria-valuemax={max}>
      <div className={`bar-fill ${level}`} style={{ width: `${Math.min(ratio, 1) * 100}%` }} />
      {pace !== undefined && <i className="bar-pace" style={{ left: `${Math.min(Math.max(pace, 0), 1) * 100}%` }} />}
    </div>
  )
}
