/** Observe — 에이전트 설정 보기 · 구조 그래프 · 호출 추적 (백엔드 app/graph · app/tracing) */
import { request, qs } from '../../shared/http'
import type { Route } from '../settings/api'

export interface CodeRef { path: string; line: number; label: string }

export interface ToolSpec {
  name: string
  title: string
  description: string
  schema: Record<string, unknown>
  read_only: boolean
  /** toss_market · toss_account · news_rss · dart · trading · app */
  source: string
}

export interface Link { to?: string; from?: string; why: string }

export type AgentGroup = 'debate' | 'desk' | 'helper'

export interface AgentSpec {
  key: string
  title: string
  name: string | null
  full_name: string | null
  country: string | null
  group: AgentGroup
  role: 'analyst' | 'executor' | 'research' | 'assistant' | 'advisor' | 'scheduler'
  enabled: boolean
  persona: string
  feature: string
  route: Route
  system_prompt: string
  extra_prompts: { title: string; text: string; ref: CodeRef }[]
  tools: ToolSpec[]
  rules: { title: string; text: string; ref: CodeRef }[]
  talks_to: Link[]
  hears_from: Link[]
  refs: CodeRef[]
  is_default?: boolean
  warning?: string
}

export type NodeKind = 'human' | 'agent' | 'code' | 'store' | 'data'
export type EdgeKind = 'control' | 'talk' | 'data' | 'order'

export interface FlowNode {
  id: string
  label: string
  sub: string
  kind: NodeKind
  col: number
  row: number
  agent: string | null
  ref: CodeRef | null
}

export interface FlowEdge { source: string; target: string; label: string; kind: EdgeKind }

export interface Flow {
  key: string
  title: string
  summary: string
  nodes: FlowNode[]
  edges: FlowEdge[]
  mermaid: string
  refs: CodeRef[]
}

export type ObservedKind = 'run' | 'agent' | 'tool' | 'model' | 'event' | 'human'

export interface ObservedGraph {
  trace_id: string
  root: { id: string; name: string; kind: string; status: string; run_id: number | null; start_ms: number; end_ms: number | null }
  summary: TraceSummary
  nodes: { id: string; label: string; kind: ObservedKind; count: number; agent?: string; source?: string }[]
  edges: { source: string; target: string; kind: 'control' | 'llm' | 'tool' | 'talk' | 'event'; count: number; agree: number;
    disagree: number; tokens: number; errors: number }[]
}

export interface LangGraphExport {
  key: string
  installed: boolean
  mermaid: string | null
  nodes: string[] | null
  edges?: [string, string][]
  how?: string
}

export type SpanKind = 'run' | 'step' | 'agent' | 'llm' | 'tool' | 'event'
export type SpanStatus = 'running' | 'ok' | 'error' | 'cancelled'

export interface Span {
  id: string
  trace_id: string
  parent_id: string | null
  run_id: number | null
  kind: SpanKind
  name: string
  agent: string | null
  status: SpanStatus
  start_ms: number
  end_ms: number | null
  duration_ms: number | null
  input: unknown
  output: unknown
  attrs: Record<string, unknown>
  error: string | null
}

export interface TraceSummary {
  spans: number
  llm_calls: number
  tool_calls: number
  errors: number
  running: number
  agents: string[]
  input_tokens: number
  output_tokens: number
  cost_usd: number
}

export type TraceRow = Omit<Span, 'input' | 'output'> & { summary: TraceSummary }

export interface TraceDetail { trace_id: string; root: Span; spans: Span[]; summary: TraceSummary }

export interface AgentStat {
  agent: string
  turns: number
  llm_calls: number
  tool_calls: number
  errors: number
  input_tokens: number
  output_tokens: number
  cost_usd: number
  avg_turn_ms: number | null
}

export interface LangSmithStatus { configured: boolean; installed: boolean; active: boolean; project: string; exported: number;
  error: string | null; how: string }

export const observeApi = {
  agents: () => request<AgentSpec[]>('GET', '/api/graph/agents'),
  agent: (key: string) => request<AgentSpec>('GET', `/api/graph/agents/${key}`),
  flows: () => request<{ key: string; title: string; summary: string }[]>('GET', '/api/graph/flows'),
  flow: (key: string) => request<Flow>('GET', `/api/graph/flows/${key}`),
  observed: (traceId: string) => request<ObservedGraph>('GET', `/api/graph/observed/${traceId}`),
  langgraph: (key: string) => request<LangGraphExport>('GET', `/api/graph/langgraph/${key}`),
  traces: (p: { limit?: number; agent?: string; run_id?: number; before_ms?: number } = {}) =>
    request<TraceRow[]>('GET', `/api/traces${qs(p)}`),
  trace: (id: string) => request<TraceDetail>('GET', `/api/traces/${id}`),
  agentStats: (days = 7) => request<AgentStat[]>('GET', `/api/traces/stats/agents${qs({ days })}`),
  langsmith: () => request<LangSmithStatus>('GET', '/api/traces/export/langsmith'),
}

/** 코드 위치 → GitHub 그 줄 (경로는 backend 기준). 내 컴퓨터 경로는 화면에 넣지 않는다 */
export const githubHref = (r: CodeRef) => `https://github.com/derf1a/Mallot-Invest-Agent/blob/main/backend/${r.path}#L${r.line}`
