/** AI 트레이딩 세션 — 메시지 폴링 훅, 게이트·입장 라벨, 라운드 집계 (채팅방·토론 현황 서랍이 같이 쓴다) */
import { useCallback, useEffect, useRef, useState } from 'react'
import { isActive } from '../../runs/live'
import type { Run } from '../../runs/api'
import { investApi, type AdviceStatus, type AssistantMessage, type Candidate, type ChatMessage, type Order, type Stance } from '../api'
import { STANCE_LABEL } from '../format'

export const GATE_TEXT: Record<string, string> = {
  aligned: '모두 한 방향', conflict: '의견 충돌 — 행동 안 함', no_view: '모두 의견 없음 — 행동 안 함', not_held: '보유하지 않은 종목의 매도 — 행동 안 함', incomplete: '응답 누락 — 행동 안 함', pending: '진행 중',
}
export const GATE_SHORT: Record<string, string> = { aligned: '열림', conflict: '충돌', no_view: '의견 없음', not_held: '보유 없음', incomplete: '응답 누락', pending: '진행 중' }
export const stanceLabel = (s: string) => (s === 'mixed' ? '종목별' : STANCE_LABEL[s as keyof typeof STANCE_LABEL] ?? s)

export type Chat = ReturnType<typeof useChat>
type Msg<T extends ChatMessage['type']> = Extract<ChatMessage, { type: T }>

/** 세션 메시지를 순번 이후만 이어 받는다 (진행 중이면 1.2초마다). 세션이 바뀌면 key 로 다시 마운트해서 쓴다. */
export function useChat(id: number) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [run, setRun] = useState<Run | null>(null)
  const [orders, setOrders] = useState<Order[]>([])
  const [version, setVersion] = useState(0)
  const last = useRef(0)

  useEffect(() => {
    let alive = true
    let timer: number | undefined
    const tick = async () => {
      try {
        const data = await investApi.messages(id, last.current)
        if (!alive) return
        setRun(data.run)
        setOrders(data.orders)
        if (data.messages.length) {
          last.current = data.messages[data.messages.length - 1].seq
          setMessages((prev) => [...prev, ...data.messages])
        }
        if (isActive(data.run.status)) timer = window.setTimeout(tick, 1200)
      } catch {
        if (alive) timer = window.setTimeout(tick, 4000)
      }
    }
    tick()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [id, version])
  // 승인·거절 뒤 주문 상태를 다시 받는다 (끝난 세션은 폴링이 멈춰 있으므로)
  const reload = useCallback(() => setVersion((v) => v + 1), [])
  return { id, messages, run, orders, reload }
}

/** 투자 비서 대화 — 이어 받기 (비서가 답을 만드는 중이면 1.5초, 아니면 10초마다). send 는 내 말을 보내고 바로 목록에 붙인다. */
export function useAssistant() {
  const [messages, setMessages] = useState<AssistantMessage[]>([])
  const [pending, setPending] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [advice, setAdvice] = useState<AdviceStatus | null>(null)
  const [version, setVersion] = useState(0)
  const last = useRef(0)

  useEffect(() => {
    let alive = true
    let timer: number | undefined
    const tick = async () => {
      try {
        const data = await investApi.assistantMessages(last.current)
        if (!alive) return
        if (data.messages.length) {
          last.current = data.messages[data.messages.length - 1].id
          setMessages((prev) => [...prev, ...data.messages.filter((m) => !prev.some((x) => x.id === m.id))])
        }
        setPending(data.pending)
        setAdvice(data.advice)
        setLoaded(true)
        timer = window.setTimeout(tick, data.pending ? 1500 : 10000)
      } catch {
        if (alive) timer = window.setTimeout(tick, 5000)
      }
    }
    tick()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [version])

  const send = useCallback(async (text: string) => {
    const r = await investApi.sayToAssistant(text)
    setMessages((prev) => (prev.some((x) => x.id === r.message.id) ? prev : [...prev, r.message]))
    setPending(true)
    setVersion((v) => v + 1) // 바로 다시 이어 받기 (빠른 간격으로)
  }, [])
  const refresh = useCallback(() => setVersion((v) => v + 1), [])
  return { messages, pending, loaded, advice, send, refresh }
}

export const findLast = <T extends ChatMessage['type']>(messages: ChatMessage[], type: T) =>
  [...messages].reverse().find((m) => m.type === type) as Msg<T> | undefined

/** 토론 후보 — 시작 메시지에 없으면(예전 단일 종목 세션) 실행 파라미터의 종목 하나 */
export function candidatesOf(messages: ChatMessage[], run: Run | null): Candidate[] {
  const start = findLast(messages, 'start')
  if (start?.candidates?.length) return start.candidates
  if (start?.stream) {  // 상시 운용: 대화마다 본 종목을 모은다
    const seen = new Map<string, Candidate>()
    for (const m of messages) if (m.type === 'round') for (const c of m.candidates ?? []) if (!seen.has(c.symbol)) seen.set(c.symbol, c)
    return [...seen.values()]
  }
  const symbol = run?.params.symbol
  return symbol ? [{ symbol: String(symbol), name: String(run?.summary?.name ?? symbol), security_type: '', held: 0 }] : []
}

/** 발언 하나의 후보별 입장 — 예전 단일 종목 세션은 views 없이 stance 하나 */
export function viewsOf(m: Msg<'statement'>, candidates: Candidate[]): { symbol: string; stance: Stance; confidence: number; reason: string }[] {
  if (m.views?.length) return m.views
  if (candidates.length === 1 && m.stance !== 'mixed') return [{ symbol: candidates[0].symbol, stance: m.stance, confidence: m.confidence, reason: m.thesis }]
  return []
}

export interface Tally {
  symbol: string
  name: string
  buy: number
  sell: number
  none: number
}

/** 한 라운드의 종목별 입장 수 (응답 실패는 빼고 센다) */
export function tallyRound(statements: Msg<'statement'>[], candidates: Candidate[]): Tally[] {
  return candidates.map((c) => {
    const t: Tally = { symbol: c.symbol, name: c.name, buy: 0, sell: 0, none: 0 }
    for (const s of statements) {
      if (s.error) continue
      const v = viewsOf(s, candidates).find((x) => x.symbol === c.symbol)
      t[v?.stance ?? 'none'] += 1
    }
    return t
  })
}

/** "14:05:09" → "오후 2:05" (카카오톡처럼) */
export function chatTime(ts: string): string {
  const [h, m] = ts.split(':').map(Number)
  if (Number.isNaN(h) || Number.isNaN(m)) return ts.slice(0, 5)
  return `${h < 12 ? '오전' : '오후'} ${h % 12 || 12}:${String(m).padStart(2, '0')}`
}

/** "2026-10-20 14:05:09" → "2026년 10월 20일 화요일" */
export function chatDate(createdAt: string): string {
  const [y, mo, d] = createdAt.slice(0, 10).split('-').map(Number)
  if (!y || !mo || !d) return createdAt.slice(0, 10)
  const day = '일월화수목금토'[new Date(y, mo - 1, d).getDay()]
  return `${y}년 ${mo}월 ${d}일 ${day}요일`
}
