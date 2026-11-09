/** LLM 설정 API — 기본 LLM · 로컬 MLX · 경로(에이전트 · 기능별) · vLLM (백엔드 app/llm/api.py) */
import { request } from '../../shared/http'

export type LlmProviderKey = 'claude' | 'local'

export type Thinking = 'off' | 'low' | 'medium' | 'xhigh'

export type LocalWorkerState = 'stopped' | 'starting' | 'loading' | 'ready' | 'error'

export interface LocalModel {
  key: string
  repo: string
  title: string
  note: string
  path: string
  downloaded: boolean
  downloading: boolean
  bytes: number
  total_bytes: number
  progress: number | null
  error: string | null
}

/** 작업 프로세스가 모델을 올린 뒤 잡은 메모리 계획: 한도 - 가중치 - 고정 상태 - 여유분 = KV 캐시 */
export interface KvPlan {
  limit_bytes: number
  limit_source: 'iogpu.wired_limit_mb' | 'metal_recommended'
  weights_bytes: number
  fixed_state_bytes: number
  margin_bytes: number
  kv_bytes_per_token: number
  kv_tokens: number
  kv_budget_bytes: number
  max_context: number
}

export interface LocalWorker {
  state: LocalWorkerState
  model?: string
  pid?: number
  uptime_sec?: number
  error?: string | null
  plan?: KvPlan
  load_seconds?: number
  active_bytes?: number
  peak_bytes?: number
  busy?: boolean
  waiting?: number
  served?: number
  cached_tokens?: number
}

export interface LlmStatus {
  provider: LlmProviderKey
  options: { model: string; thinking: Thinking }
  models: LocalModel[]
  worker: LocalWorker
  memory: {
    memory_bytes: number | null
    wired_limit_mb: number | null
    gpu_limit_bytes: number | null
    gpu_limit_source: 'iogpu.wired_limit_mb' | 'metal_recommended'
    target_bytes: number | null
    margin_bytes: number
    apple_silicon: boolean
  }
}

export interface LlmTestResult {
  answer: string
  reasoning: string
  usage: { prompt_tokens: number; cached_tokens: number; output_tokens: number }
  timing: { prefill_ms: number; generate_ms: number; prompt_tps: number | null; generation_tps: number | null }
  total_ms: number
}

export interface Route {
  provider: 'claude' | 'mlx' | 'vllm'
  model: string
  /** 어디서 정해졌나: image · agent · call(토론 설정 모델) · feature · default */
  source: 'image' | 'agent' | 'call' | 'feature' | 'default'
  text: string
  label: string
}

export interface RoutesView {
  default: string
  features: Record<string, string>
  agents: Record<string, string>
  providers: { key: string; title: string }[]
  feature_list: { key: string; title: string; route: Route }[]
  effective_default: Route
}

export interface VllmStatus {
  configured: boolean
  url: string
  has_key: boolean
  model: string
  models: string[]
  reachable: boolean
  error: string | null
}

export const systemApi = {
  llmStatus: () => request<LlmStatus>('GET', '/api/llm'),
  setLlmProvider: (provider: LlmProviderKey) => request<LlmStatus>('PUT', '/api/llm/provider', { provider }),
  setLocalOptions: (body: { model?: string; thinking?: Thinking }) => request<LlmStatus>('PUT', '/api/llm/local', body),
  downloadLocalModel: (key: string) => request<LocalModel>('POST', `/api/llm/local/models/${key}/download`),
  cancelLocalDownload: (key: string) => request<LocalModel>('POST', `/api/llm/local/models/${key}/cancel`),
  deleteLocalModel: (key: string) => request<LocalModel>('DELETE', `/api/llm/local/models/${key}`),
  loadLocal: () => request<LlmStatus>('POST', '/api/llm/local/load'),
  unloadLocal: () => request<LlmStatus>('POST', '/api/llm/local/unload'),
  testLocal: () => request<LlmTestResult>('POST', '/api/llm/local/test'),
  routes: () => request<RoutesView>('GET', '/api/llm/routes'),
  setRoutes: (body: { default?: string; features?: Record<string, string>; agents?: Record<string, string> }) =>
    request<RoutesView>('PUT', '/api/llm/routes', body),
  vllm: () => request<VllmStatus>('GET', '/api/llm/vllm'),
  setVllmModel: (model: string) => request<VllmStatus>('PUT', '/api/llm/vllm', { model }),
  testVllm: () => request<{ answer: string; model: string | null; total_ms: number }>('POST', '/api/llm/vllm/test'),
  health: () => request<{ ok: boolean; db_version: number }>('GET', '/api/health'),
}
