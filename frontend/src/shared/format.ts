export const won = (n: number) => `${n.toLocaleString('ko-KR')}원`

export const signedWon = (n: number) => (n > 0 ? `+${won(n)}` : n < 0 ? `-${won(-n)}` : won(0))

export const todayISO = () => {
  const d = new Date()
  return toISO(d)
}

export const toISO = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

export const parseISO = (s: string) => {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}

export const addDays = (s: string, days: number) => {
  const d = parseISO(s)
  d.setDate(d.getDate() + days)
  return toISO(d)
}

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토']

export const shortDate = (s: string) => {
  const d = parseISO(s)
  return `${d.getMonth() + 1}/${d.getDate()}`
}

export const longDate = (s: string) => {
  const d = parseISO(s)
  return `${d.getMonth() + 1}월 ${d.getDate()}일 (${WEEKDAYS[d.getDay()]})`
}

export const periodLabel = (p: { start: string; end: string }) => `${shortDate(p.start)} ~ ${shortDate(p.end)}`

export const percent = (part: number, whole: number) => (whole > 0 ? Math.round((part / whole) * 100) : 0)

/** 실험실 숫자 표기 */
export function fmtNum(v: number): string {
  const a = Math.abs(v)
  if (a >= 1000) return v.toLocaleString('ko-KR', { maximumFractionDigits: 0 })
  if (a >= 10) return v.toFixed(1)
  if (a >= 1) return v.toFixed(2)
  return v.toFixed(3)
}

export const fmtPct = (v: number) => `${(v * 100).toFixed(1)}%`
export const pctOrDash = (v: number | null | undefined) => (v === null || v === undefined ? '–' : fmtPct(v))
