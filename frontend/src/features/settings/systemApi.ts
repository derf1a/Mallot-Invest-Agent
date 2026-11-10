/** 시스템 이슈 API — 시스템 관리자(마테우스)가 정리한 이슈 · 답 · 대기 요청 (백엔드 app/system/api.py) */
import { request } from '../../shared/http'

export interface SystemIssue {
  id: number
  number: number | null
  url: string
  title: string
  body: string
  status: 'open' | 'resolved'
  by: string
  reports: number
  resolution: string
  created_at: string
  updated_at: string
  resolved_at: string | null
}
export interface SystemIssueDetail extends SystemIssue {
  events: { id: number; at: string; kind: string; who: string; text: string }[]
  requests: { at: string; last_at: string; by: string; source: string; text: string; seen: number }[]
}
export interface SystemOverview {
  counts: { open: number; resolved: number; pending: number }
  issues: SystemIssue[]
  answers: { id: number; at: string; by: string; text: string; answer: string; seen: number }[]
  pending: { id: number; at: string; by: string; text: string; seen: number }[]
  github: { configured: boolean; repo: string; token_env: string }
  admin: { name: string; title: string; role: string }
}
export interface TriageResult {
  result: {
    triage: { pending: number; issue?: number; link?: number; answer?: number; ignore?: number; error?: string }
    publish: { published: number; github: boolean; error?: string }
    sync?: { synced: number; changed?: number; github: boolean; error?: string }
  }
}

export const systemIssuesApi = {
  overview: (status?: 'open' | 'resolved') => request<SystemOverview>('GET', `/api/system/issues${status ? `?status=${status}` : ''}`),
  detail: (id: number) => request<SystemIssueDetail>('GET', `/api/system/issues/${id}`),
  triage: () => request<SystemOverview & TriageResult>('POST', '/api/system/triage'),
  resolve: (id: number, text = '') => request<SystemIssueDetail>('POST', `/api/system/issues/${id}/resolve`, { text }),
  reopen: (id: number, text = '') => request<SystemIssueDetail>('POST', `/api/system/issues/${id}/reopen`, { text }),
}
