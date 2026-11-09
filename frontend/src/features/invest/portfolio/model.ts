/** 포트폴리오 화면 계산 — /api/invest/portfolio 의 보유(positions)를 묶고 나눈다. 화면 컴포넌트는 그리기만 한다. */
import type { PortfolioOverview, Position } from '../api'

/** 범주 색: dataviz 기본 팔레트 1~5번 순서 고정 (validate_palette.js 라이트·다크 통과). 6번째부터는 '기타'로 접는다. */
export const SLOTS = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)', 'var(--series-5)']
export const OTHER_COLOR = 'var(--series-other)'
export const CASH = '__cash__'

export interface Slice {
  key: string
  label: string
  value: number
  color: string // CSS 색 (현금은 빗금 무늬 클래스로 따로 그린다)
  note?: string
}

export type AllocationMode = 'symbol' | 'account' | 'type'

export const where = (p: Position) => (p.where === '토스' ? (p.currency === 'KRW' ? '토스 국내' : '토스 해외') : p.where)

export function typeOf(p: Position) {
  if (p.currency !== 'KRW') return '해외 주식'
  return p.security_type === 'ETF' ? '국내 ETF' : '국내 주식'
}

export function cashTotal(o: PortfolioOverview) {
  return o.cash.krw + o.cash.usd_in_krw
}

/** 같은 열쇠끼리 평가금액을 더해 큰 순서로 */
function sumBy(positions: Position[], key: (p: Position) => string, label: (p: Position) => string) {
  const out = new Map<string, { key: string; label: string; value: number }>()
  for (const p of positions) {
    const k = key(p)
    const row = out.get(k) ?? { key: k, label: label(p), value: 0 }
    row.value += p.value_krw
    out.set(k, row)
  }
  return [...out.values()].sort((a, b) => b.value - a.value)
}

/** 전체 자산을 종목·계좌·유형으로 나눈 조각: 앞 5개는 범주 색, 나머지는 '기타 N', 현금은 맨 끝. 색은 큰 순서로 고정. */
export function allocation(o: PortfolioOverview, mode: AllocationMode): Slice[] {
  const groups = mode === 'symbol'
    ? sumBy(o.positions, (p) => p.symbol, (p) => p.name || p.symbol)
    : mode === 'account'
      ? sumBy(o.positions, where, where)
      : sumBy(o.positions, typeOf, typeOf)
  const head = groups.slice(0, SLOTS.length).map((g, i) => ({ ...g, color: SLOTS[i] }))
  const tail = groups.slice(SLOTS.length)
  const slices: Slice[] = [...head]
  if (tail.length) {
    slices.push({ key: '__other__', label: `기타 ${tail.length}`, value: tail.reduce((a, g) => a + g.value, 0), color: OTHER_COLOR,
      note: tail.map((g) => g.label).join(', ') })
  }
  const cash = cashTotal(o)
  if (cash > 0) {
    slices.push({ key: CASH, label: '현금', value: cash, color: 'var(--surface)',
      note: o.cash.usd ? `원화 ${Math.round(o.cash.krw).toLocaleString('ko-KR')}원 + 달러 $${o.cash.usd.toLocaleString('en-US')}` : undefined })
  }
  return slices.filter((s) => s.value > 0)
}

/** 종목 → 범주 색 (종목별 배분과 같은 색 — 보유 목록의 점) */
export function symbolColors(o: PortfolioOverview): Map<string, string> {
  return new Map(allocation(o, 'symbol').filter((s) => s.key !== CASH && s.key !== '__other__').map((s) => [s.key, s.color]))
}

export interface Totals {
  pnl: number
  cost: number
  rate: number | null
  unknown: number // 평단·현재가를 몰라 손익에서 빠진 보유 수
}

export function totals(positions: Position[]): Totals {
  const known = positions.filter((p) => p.pnl_krw !== null && p.cost_krw !== null)
  const pnl = known.reduce((a, p) => a + (p.pnl_krw ?? 0), 0)
  const cost = known.reduce((a, p) => a + (p.cost_krw ?? 0), 0)
  return { pnl, cost, rate: cost > 0 ? pnl / cost : null, unknown: positions.length - known.length }
}

export interface PnlRow {
  symbol: string
  name: string
  pnl: number
  cost: number
  rate: number | null
}

/** 종목별 손익 (여러 계좌에 나눠 가진 종목은 합친다), 손익 큰 순 */
export function pnlBySymbol(positions: Position[]): PnlRow[] {
  const out = new Map<string, PnlRow>()
  for (const p of positions) {
    if (p.pnl_krw === null || p.cost_krw === null) continue
    const row = out.get(p.symbol) ?? { symbol: p.symbol, name: p.name || p.symbol, pnl: 0, cost: 0, rate: null }
    row.pnl += p.pnl_krw
    row.cost += p.cost_krw
    out.set(p.symbol, row)
  }
  return [...out.values()].map((r) => ({ ...r, rate: r.cost ? r.pnl / r.cost : null })).sort((a, b) => b.pnl - a.pnl)
}

export type SortKey = 'value' | 'pnl' | 'rate' | 'name'

export function sortPositions(rows: Position[], key: SortKey, desc: boolean): Position[] {
  const val = (p: Position): number | string => {
    if (key === 'name') return p.name || p.symbol
    if (key === 'value') return p.value_krw
    if (key === 'pnl') return p.pnl_krw ?? -Infinity
    return p.pnl_rate ?? -Infinity
  }
  return [...rows].sort((a, b) => {
    const x = val(a)
    const y = val(b)
    const c = typeof x === 'string' ? x.localeCompare(y as string, 'ko') : x - (y as number)
    return desc ? -c : c
  })
}
