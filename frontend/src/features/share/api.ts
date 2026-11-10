/** 손님 화면 API (/api/share) — 읽기만 (백엔드 app/share) */
import { request, qs } from '../../shared/http'
import type { AutopilotEntry, Briefing } from '../invest/api'

export interface DeskStage { day: string; at: string; data: Record<string, unknown> }

export interface ShareSession {
  id: number
  name: string
  /** interrupted = 서버 재시작으로 끊김 (runs 의 failed 중 그때까지의 요약이 남은 것) */
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted'
  created_at: string
  finished_at: string | null
  scope: string | null
  autopilot: boolean
  minutes: { headline: string; summary: string; kind: string } | null
  gate: string | null
  decision: string | null
  orders: { symbol: string; name: string; side: 'buy' | 'sell'; quantity: number; price: number | null; status: string; created_at: string }[]
}

export interface ShareStatus {
  autopilot: {
    enabled: boolean
    mode: 'continuous' | 'meetings'
    slots: string[]
    flexible: boolean
    today: { date: string; entries: AutopilotEntry[]; extra_used: number }
    window: [string, string] | null
    window_note: string | null
    next: AutopilotEntry | null
    blocked: string | null
    intents: number
  }
  desk: {
    market: DeskStage | null
    sector: DeskStage | null
    ideas: DeskStage | null
    alerts: { at: string | null; text: string }[]
    people: Record<string, { key: string; title: string; name: string; full_name: string; country: string }>
  }
  latest: ShareSession | null
}

export interface SavedReport { id: number; created_at: string; title: string; morning: boolean; report: Briefing | null; body: string }

/** 회의방 메시지 (messages.jsonl 한 줄) — 손님 화면은 아는 종류만 그린다 */
export interface ShareMessage {
  seq: number
  ts: string
  type: string
  [k: string]: unknown
}

export const shareApi = {
  status: () => request<ShareStatus>('GET', '/api/share/status'),
  report: () => request<Briefing>('GET', '/api/share/report'),
  reports: (limit = 10) => request<SavedReport[]>('GET', `/api/share/reports${qs({ limit })}`),
  sessions: (limit = 20) => request<ShareSession[]>('GET', `/api/share/sessions${qs({ limit })}`),
  messages: (id: number, after = 0) => request<{ messages: ShareMessage[] }>('GET', `/api/share/sessions/${id}/messages${qs({ after })}`),
}
