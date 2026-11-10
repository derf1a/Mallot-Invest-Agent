/**
 * 한 페이지 보고서 — 서버가 계산한 Briefing 을 화면 카드(Report.tsx)와 이미지(reportImage.ts)가 같은 내용으로 그리도록 정리한다.
 * 줄글 대신 그림 재료로: 지표 타일 · 수익률 추이 · 목표 대비 눈금 · 자산 배분 · 종목별 막대 · 회의 카드 · 시장 지표 막대 · 국면 · 섹터 등락표 · 뉴스.
 * 예전 보고서(history·holdings·sectors.rows 가 없는 것)도 있는 재료만으로 그린다.
 */
import type { Briefing, BriefingMeeting } from '../api'
import { ORDER_STATUS } from '../format'
import { CASH, OTHER_COLOR, SLOTS, type Slice } from '../portfolio/model'

export type Tone = 'up' | 'down' | 'muted' | 'warn' | undefined
export interface Kpi { label: string; value: string; sub?: string; tone?: Tone; subMuted?: boolean }
/** 수익률 추이 — ticks = 축에 날짜를 쓸 점 */
export interface Curve { index: string | null; points: NonNullable<Briefing['history']>['points']; ticks: number[] }
/** 목표 대비 눈금 — actual 까지 채우고 mark 에 눈금. signed = 0 기준으로 오르내리는 값 */
export interface Goal { label: string; value: string; note: string; actual: number; mark: number; min: number; max: number; signed: boolean; warn?: boolean; tone?: Tone }
export interface Holding {
  key: string; name: string; sub: string; value: number; weight: number | null; pnl: number | null; rate: number | null; day: number | null; color: string
}
export interface MeetingView {
  key: number; at: string; name: string; status: string | null; live: boolean; headline: string | null; summary: string | null
  talks: string | null; board: { name: string; buy: number; sell: number }[]; trader: string | null
  orders: { side: string; buy: boolean; name: string; quantity: string; status: string; change: number | null }[]
  follow: string[]
}
export interface SectorRow { name: string; side: 'over' | 'under'; reason: string; rates: (number | null)[] }
export interface MarketView {
  alerts: string[]
  note: string | null
  indicators: { name: string; price: string; rate: number | null }[]
  /** key = 국면 눈금에서 켤 칸 */
  view: { key: string; regime: string; headline: string; summary: string | null; by: string | null; cash: string | null; favor: string[]; avoid: string[] } | null
  sectors: { rows: SectorRow[]; over: string[]; under: string[] } | null
  news: { tag: string | null; title: string; source: string }[]
}
export interface ReportView {
  kpis: Kpi[]; warnings: string[]; curve: Curve | null; goals: Goal[]; slices: Slice[]; holdings: Holding[]; notes: string[]
  meetings: MeetingView[]; market: MarketView
}

/** 국면 눈금 — 왼쪽부터 (화면·이미지가 같은 칸) */
export const REGIMES = [{ key: 'risk_off', label: '위험 회피' }, { key: 'neutral', label: '중립' }, { key: 'risk_on', label: '위험 선호' }]
/** 섹터 등락표의 열 — 서버 rates 키 순서 */
export const RATE_KEYS = [{ key: '1d', label: '1일' }, { key: '1w', label: '1주' }, { key: '1mo', label: '1개월' }, { key: '3mo', label: '3개월' }]
/** 섹터 등락표에서 색이 가장 진해지는 등락 */
export const HEAT_MAX = 0.2

export const won = (n: number | null | undefined) => (n === null || n === undefined ? '-' : `${Math.round(n).toLocaleString('ko-KR')}원`)
export const pct = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? '-' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(digits)}%`)
/** 축 글자 — 0 은 부호 없이 */
export const axisPct = (v: number) => (Math.abs(v) < 1e-9 ? '0%' : pct(v, 2))
export const toneOf = (v: number | null | undefined): Tone => (v === null || v === undefined || v === 0 ? undefined : v > 0 ? 'up' : 'down')
/** '2026-10-30 09:08' → '10/30 09:08', 날짜만이면 시작점 */
export const pointLabel = (at: string) => (at.length <= 10 ? `${at.slice(5).replace('-', '/')} 시작` : `${at.slice(5, 10).replace('-', '/')} ${at.slice(11, 16)}`)
const SIDE: Record<string, string> = { buy: '매수', sell: '매도' }
const RUN: Record<string, string> = { succeeded: '끝남', cancelled: '중지', failed: '실패', running: '진행 중', queued: '대기', interrupted: '중단(서버 재시작)' }

/** 추이 — 날짜가 바뀌는 점에만 축 글자, 많으면 고른 간격으로 MAX_TICKS 개 */
const MAX_TICKS = 5
function curveOf(r: Briefing): Curve | null {
  const h = r.history
  if (!h || h.points.length < 2) return null
  const days = h.points.flatMap((p, i) => (i && p.at.slice(0, 10) === h.points[i - 1].at.slice(0, 10) ? [] : [i]))
  const step = Math.ceil(days.length / MAX_TICKS)
  return { index: h.index, points: h.points, ticks: days.filter((_, i) => i % step === 0) }
}

/** 목표 대비 — 정한 목표가 있을 때만. 수익률·지수 눈금은 같은 잣대라 서로 비교된다 */
function goalsOf(g: Briefing['policy']): Goal[] {
  if (!g) return []
  const out: Goal[] = []
  if (g.equity) {
    const off = Math.abs(g.equity.actual - g.equity.target) > 10
    out.push({ label: '주식 비중', value: `${g.equity.actual}%`, note: `목표 ${g.equity.target}%`, actual: g.equity.actual / 100, mark: g.equity.target / 100,
      min: 0, max: 1, signed: false, warn: off })
  }
  const rt = g.return
  const paced = rt && rt.target_year !== null && rt.target_so_far !== null ? rt : null
  const bm = rt ? g.benchmark : null
  const seen = [rt?.actual, paced?.target_so_far, bm?.change].filter((v): v is number => v !== undefined && v !== null)
  const top = Math.max(...seen.map(Math.abs), 0.001) * 1.25
  const min = seen.some((v) => v < 0) ? -top : 0
  if (paced) {
    out.push({ label: `수익률 · ${paced.days}일`, value: pct(paced.actual, 2), note: `목표 진도 ${pct(paced.target_so_far, 2)} (연 ${paced.target_year}%)`,
      actual: paced.actual, mark: paced.target_so_far!, min, max: top, signed: true, tone: toneOf(paced.actual) })
  }
  if (rt && bm) {
    out.push({ label: `${bm.name} 대비`, value: `${bm.excess >= 0 ? '+' : ''}${(bm.excess * 100).toFixed(2)}%p`, note: `${bm.name} ${pct(bm.change, 2)}`,
      actual: rt.actual, mark: bm.change, min, max: top, signed: true, tone: toneOf(bm.excess) })
  }
  return out
}

function holdingsOf(r: Briefing): Holding[] {
  const pf = r.performance.portfolio
  if (!pf) return []
  // 비중은 자산 배분 막대와 같은 값으로 — 서버가 반올림한 weight 를 쓰면 같은 종목이 23.6%·23.5% 로 갈린다
  const share = (value: number, weight: number | null) => (pf.total_assets_krw ? value / pf.total_assets_krw : weight)
  const rows: Holding[] = r.holdings
    ? r.holdings.map((x) => ({ key: `${x.where}-${x.symbol}`, name: `${x.name}${x.where === '토스' ? '' : ` [${x.where}]`}`,
        sub: `${x.quantity.toLocaleString('ko-KR')}주 · ${won(x.value_krw)}`, value: x.value_krw, weight: share(x.value_krw, x.weight), pnl: x.pnl_krw, rate: x.pnl_rate,
        day: x.day_change, color: OTHER_COLOR }))
    : pf.top.map((x) => ({ key: `${x.where}-${x.symbol}`, name: x.name, sub: won(x.value_krw), value: x.value_krw, weight: share(x.value_krw, x.weight), pnl: null, rate: x.pnl_rate,
        day: null, color: OTHER_COLOR }))
  return rows.map((h, i) => (i < SLOTS.length ? { ...h, color: SLOTS[i] } : h))
}

/** 자산 배분 — 앞 5종목은 범주 색(보유 목록의 점과 같은 색), 나머지 '기타', 현금은 맨 끝 (포트폴리오 화면과 같은 규칙) */
function slicesOf(r: Briefing, rows: Holding[]): Slice[] {
  const pf = r.performance.portfolio
  if (!pf) return []
  const head = rows.slice(0, SLOTS.length)
  const rest = pf.stock_value_krw - head.reduce((a, h) => a + h.value, 0)
  const slices: Slice[] = head.map((h) => ({ key: h.key, label: h.name, value: h.value, color: h.color }))
  if (rest >= 1) slices.push({ key: '__other__', label: `기타 ${Math.max(pf.positions - head.length, 1)}`, value: rest, color: OTHER_COLOR })
  if (pf.cash_krw > 0) slices.push({ key: CASH, label: '현금', value: pf.cash_krw, color: 'var(--surface)' })
  return slices.filter((s) => s.value > 0)
}

function notesOf(r: Briefing): string[] {
  const pf = r.performance.portfolio
  if (!pf) return [r.performance.portfolio_note ?? '투자현황 없음']
  const out: string[] = []
  if (r.holdings && !r.holdings.length) out.push('보유 종목 없음 — 모두 현금')
  if (r.holdings_more) out.push(`외 ${r.holdings_more}종목`)
  if (pf.unpriced) out.push(`원금·현재가를 몰라 수익률에서 뺀 보유 ${pf.unpriced}개`)
  return out
}

function meetingOf(m: BriefingMeeting): MeetingView {
  const live = m.live ?? null
  return {
    key: m.id, at: m.at.slice(5), name: m.name, live: !!live, headline: m.headline, summary: m.summary ?? null,
    status: live ? '진행 중' : m.headline ? null : RUN[m.status] ?? m.status,
    talks: live ? `대화 ${live.talks}번 · 주문 ${m.orders?.length ?? 0}건` : null,
    board: live?.board ?? [], trader: live?.trader ?? null,
    orders: (m.orders ?? []).map((o) => ({ side: SIDE[o.side], buy: o.side === 'buy', name: o.name, quantity: `${o.quantity.toLocaleString('ko-KR')}주`,
      status: ORDER_STATUS[o.status] ?? o.status, change: o.change })),
    follow: m.follow_up ?? [],
  }
}

function marketOf(r: Briefing): MarketView {
  const mk = r.market
  const old = r.research
  const v = mk.view
  const label = v?.regime ?? old?.regime ?? null
  const view = v || old?.regime
    ? { key: REGIMES.find((x) => x.label === label)?.key ?? 'neutral', regime: label ?? '중립', headline: (v ? v.headline : old?.headline) ?? '', summary: v?.summary ?? null,
        by: v ? `${v.at} ${v.by}` : null, cash: (v ? v.cash : old?.cash) ?? null, favor: v?.favor ?? [], avoid: v?.avoid ?? [] }
    : null
  const sc = mk.sectors
  const rows = (sc?.rows ?? []).map((x): SectorRow => ({ name: x.name, side: x.side, reason: x.reason, rates: RATE_KEYS.map((k) => x.rates[k.key] ?? null) }))
  const over = sc ? sc.overweight.map((x) => x.name) : old?.overweight ?? []
  const under = sc?.underweight ?? old?.underweight ?? []
  return {
    alerts: (mk.alerts ?? old?.alerts ?? []).slice(-3), note: mk.note,
    indicators: mk.indicators.filter((i) => i.price !== null).map((i) => ({ name: i.name, rate: i.change_rate,
      price: `${i.price!.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}${i.unit === 'pt' ? '' : i.unit ?? ''}` })),
    view, sectors: over.length || under.length ? { rows, over, under } : null,
    news: [...(mk.news ?? []).map((n) => ({ tag: null, title: n.title, source: n.source })),
      ...(mk.holding_news ?? []).map((n) => ({ tag: n.name ?? null, title: n.title, source: n.source }))],
  }
}

/** 보고서 → 그릴 재료. 관리자가 계속 볼 것만 — 설정·일정·운용 지시는 넣지 않는다. */
export function reportView(r: Briefing): ReportView {
  const pf = r.performance.portfolio
  const bl = r.performance.baseline
  const kpis: Kpi[] = bl
    ? [{ label: '운용 수익률', value: pct(bl.return_rate, 2), sub: `기준 ${won(bl.amount)} · ${won(bl.pnl_krw)}`, tone: toneOf(bl.return_rate) }]
    : []
  if (pf) {
    kpis.push(
      { label: '총 자산', value: won(pf.total_assets_krw), sub: `주식 ${won(pf.stock_value_krw)}`, subMuted: true },
      { label: '평가 손익', value: won(pf.pnl_krw), sub: `수익률 ${pct(pf.return_rate, 2)}`, tone: toneOf(pf.pnl_krw) },
      { label: '현금', value: won(pf.cash_krw), sub: pf.cash_ratio === null ? undefined : `비중 ${(pf.cash_ratio * 100).toFixed(0)}%`, subMuted: true },
    )
  }
  const holdings = holdingsOf(r)
  return {
    kpis, holdings, curve: curveOf(r), goals: goalsOf(r.policy), slices: slicesOf(r, holdings), notes: notesOf(r),
    warnings: r.warnings ?? [r.plan?.blocked, r.plan?.halted ? '전체 주문 중지 중' : null].filter((w): w is string => !!w),
    meetings: r.meetings.map(meetingOf), market: marketOf(r),
  }
}
