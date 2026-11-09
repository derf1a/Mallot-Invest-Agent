/** 실행(run) API — 에이전트 토론 · 리서치 · 뉴스 리포트가 모두 실행 하나로 남는다 (백엔드 app/runs) */
import { request, qs } from '../../shared/http'

export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface Run {
  id: number
  kind: string
  kind_title: string
  name: string
  status: RunStatus
  params: Record<string, unknown>
  summary: Record<string, unknown> | null
  progress: number
  parent_id: number | null
  parent_name: string | null
  dataset_id: number | null
  dataset_name: string | null
  artifact_path: string | null
  error: string | null
  cancel_requested: boolean
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export type Metrics = Record<string, [number, number][]>

export interface RunLog {
  id: number
  ts: string
  level: 'debug' | 'info' | 'warn' | 'error'
  message: string
}

export const labApi = {
  runs: (kind?: string, limit = 50) => request<Run[]>('GET', `/api/runs${qs({ kind, limit })}`),
  run: (id: number) => request<Run>('GET', `/api/runs/${id}`),
  metrics: (id: number) => request<Metrics>('GET', `/api/runs/${id}/metrics`),
  logs: (id: number, afterId = 0) => request<RunLog[]>('GET', `/api/runs/${id}/logs${qs({ after_id: afterId })}`),
  cancel: (id: number) => request<Run>('POST', `/api/runs/${id}/cancel`),
  deleteRun: (id: number) => request<void>('DELETE', `/api/runs/${id}`),
}
