/** 투자 탭 숫자 표시. 등락 색은 국내 관례: 상승 빨강(--up), 하락 파랑(--down). */
import { useEffect, useState } from 'react'
import type { Run } from '../runs/api'
import type { OrderStatus, Stance } from './api'

export function price(v: number | null | undefined, currency = 'KRW'): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '–'
  return currency === 'KRW'
    ? Math.round(v).toLocaleString('ko-KR')
    : v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export const won = (v: number | null | undefined) => (v === null || v === undefined ? '–' : `${price(v)}원`)

export function signed(v: number | null | undefined, currency = 'KRW'): string {
  if (v === null || v === undefined) return '–'
  return `${v > 0 ? '+' : v < 0 ? '−' : ''}${price(Math.abs(v), currency)}`
}

export function pct(r: number | null | undefined, digits = 2): string {
  if (r === null || r === undefined) return '–'
  return `${r > 0 ? '+' : r < 0 ? '−' : ''}${Math.abs(r * 100).toFixed(digits)}%`
}

/** 비율 (부호 없이): 현금 비중·종목 비중 */
export function ratio(r: number | null | undefined, digits = 1): string {
  return r === null || r === undefined ? '–' : `${(r * 100).toFixed(digits)}%`
}

/** 거래량·금액 줄임: 1.2만, 3.4억, 1.2조 */
export function compact(v: number | null | undefined): string {
  if (v === null || v === undefined) return '–'
  const a = Math.abs(v)
  const sign = v < 0 ? '−' : ''
  if (a >= 1e12) return `${sign}${(a / 1e12).toFixed(1)}조`
  if (a >= 1e8) return `${sign}${(a / 1e8).toFixed(1)}억`
  if (a >= 1e4) return `${sign}${(a / 1e4).toFixed(1)}만`
  return `${sign}${Math.round(a).toLocaleString('ko-KR')}`
}

/** 정수 입력칸 표시: 숫자만 남기고 쉼표 */
export const digits = (s: string) => s.replace(/[^\d]/g, '')
export const grouped = (s: string) => (s ? Number(s).toLocaleString('ko-KR') : '')

export const qty = (v: number) => (Number.isInteger(v) ? v.toLocaleString('ko-KR') : v.toFixed(4))

/** 등락 클래스: up / down / flat */
export const dir = (v: number | null | undefined) => (!v ? 'flat' : v > 0 ? 'up' : 'down')

export const STANCE_LABEL: Record<Stance, string> = { buy: '매수', sell: '매도', none: '의견 없음' }

export const hhmm = (iso: string | null | undefined) => (iso ? iso.slice(11, 16) : '')

/** 화면이 보일 때만 주기적으로 tick 을 올린다 (폴링용) */
export function useTicker(ms: number, enabled = true): number {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!enabled) return
    const id = window.setInterval(() => {
      if (document.visibilityState === 'visible') setTick((t) => t + 1)
    }, ms)
    return () => window.clearInterval(id)
  }, [ms, enabled])
  return tick
}

/** 기능별 모델 선택. '' = 설정 > LLM 에서 고른 기본 LLM, 'local' = 로컬 LLM (mlx-lm) */
export const MODELS = [
  { key: '', label: '기본 모델' },
  { key: 'claude-sonnet-5-5', label: 'Sonnet 5.5 (빠름)' },
  { key: 'claude-haiku-4-5-20251001', label: 'Haiku 4.5 (가벼움)' },
  { key: 'local', label: '로컬 LLM (이 Mac)' },
]

export const ORDER_STATUS: Record<OrderStatus, string> = {
  pending_approval: '승인 대기', open: '미체결', partial: '부분 체결', filled: '체결', cancelled: '취소', rejected: '거절', failed: '확인 필요',
}

/** KRX 호가 단위 (서버 market.tick_size 와 같은 표) */
/** 호가 단위 — ETF·ETN 은 주식과 달리 2,000원 미만 1원·이상 5원 (서버 market.tick_size 와 같은 규칙) */
export function tickSize(p: number, currency = 'KRW', securityType?: string): number {
  if (currency !== 'KRW') return 0.01
  if (securityType && ['ETF', 'ETN', 'FOREIGN_ETF'].includes(securityType)) return p < 2_000 ? 1 : 5
  for (const [limit, tick] of [[2_000, 1], [5_000, 5], [20_000, 10], [50_000, 50], [200_000, 100], [500_000, 500]]) {
    if (p < limit) return tick
  }
  return 1_000
}

/** 에이전트 세션 이름 — 범위 라벨(섹터 반도체·전체·종목명) */
export function sessionLabel(r: Run): string {
  if (r.params?.autopilot) return r.name  // '자동 운용 · 09:10'
  return (r.summary?.label as string) ?? (r.summary?.name as string) ?? r.name.replace('에이전틱 매매 · ', '')
}
