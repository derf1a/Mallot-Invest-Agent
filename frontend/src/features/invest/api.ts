/** 투자 탭 API 타입·클라이언트 */
import type { Run } from '../runs/api'

export type Side = 'buy' | 'sell'
export type Stance = 'buy' | 'sell' | 'none'

export interface Limits {
  max_order_krw: number
  daily_max_krw: number
  max_position_pct: number
}

export interface InvestStatus {
  provider: 'toss' | 'sample'
  provider_title: string
  halted: boolean
  toss_configured: boolean
  pin_configured: boolean
  trading_blockers: string[]  // 비어 있으면 주문 가능 (토스 키·주문 PIN)
  agent_auto_approve: boolean
  limits: Limits
}

export interface KeyStatus {
  key: 'TOSS_CLIENT_ID' | 'TOSS_CLIENT_SECRET' | 'TOSS_ACCOUNT_SEQ'
  label: string
  secret: boolean
  set: boolean
  masked: string | null
  source: 'file' | 'env' | null
}

export interface InvestConfig {
  /** 키 · .env 위치 — 손님(읽기 전용)에게는 서버가 빼고 보낸다 */
  keys?: KeyStatus[]
  env_file?: string
  toss_configured: boolean
  pin_env_name: string
  pin_configured: boolean
  trading_blockers: string[]
  limits: Limits
  agent_auto_approve: boolean
  halted: boolean
}

export interface SearchHit {
  symbol: string
  name: string
  market: string
  security_type?: string  // STOCK·ETF 등
}

export interface Technicals {
  last_close: number | null
  bars: number
  ma5: number | null
  ma20: number | null
  ma60: number | null
  ma120: number | null
  rsi14: number | null
  macd: { macd: number; signal: number; histogram: number } | null
  volatility_20d_annualized: number | null
  return_5d: number | null
  return_20d: number | null
  return_60d: number | null
  high_in_range: number | null
  low_in_range: number | null
  volume_vs_20d_avg?: number | null
}

export interface StockSnapshot {
  symbol: string
  name: string
  english_name: string | null
  market: string
  currency: string
  security_type: string
  status: string
  is_common: boolean | null
  list_date: string | null
  shares_outstanding: number | null
  suspended: boolean
  leverage: number | null
  provider: 'toss' | 'sample'
  price: number | null
  prev_close: number | null
  change: number | null
  change_rate: number | null
  open: number | null
  high: number | null
  low: number | null
  volume: number | null
  day: string | null
  range_high: number | null
  range_low: number | null
  range_bars: number
  avg_volume_20: number | null
  market_cap: number | null
  upper_limit: number | null
  lower_limit: number | null
  tick: number
  timestamp: string | null
  technicals: Technicals | null
}

export interface Candle {
  t: string
  o: number
  h: number
  l: number
  c: number
  v: number
}

export interface Level {
  price: number
  volume: number
}

export interface Orderbook {
  provider: string
  asks: Level[]
  bids: Level[]
  timestamp: string | null
  currency: string
}

export interface InvestorDay {
  date: string
  individual: number | null
  foreigner: number | null
  institution: number | null
}

export interface Indicator {
  symbol: string
  name: string
  unit: string
  price: number | null
  change: number | null
  change_rate: number | null
}

export interface MarketSession {
  date: string | null
  is_open: boolean
  start: string | null
  end: string | null
  note: string | null
}

export interface Ranking {
  rank: number
  symbol: string
  name: string
  price: number | null
  change_rate: number | null
  volume: number | null
  amount: number | null
  currency: string
}

export interface WatchItem {
  symbol: string
  name: string
  market: string
  security_type: string
  price: number | null
  group_ids?: number[]  // 이 종목이 든 관심 그룹 (flat 목록)
}

export interface WatchGroup {
  id: number
  name: string
  sort_order: number
  items: (WatchItem & { group_id: number })[]
}

export interface Holding {
  symbol: string
  name: string
  quantity: number
  avg_price: number | null
  last_price: number | null
  value: number
  cost: number
  pnl: number
  pnl_rate: number | null
  currency: string
  weight: number | null
}

export interface AccountSummary {
  cash: number
  buying_power: number
  holdings: Holding[]
  holdings_value: number
  total_value: number
  pnl: number
  pnl_rate: number | null
  foreign_holdings: number
}

export interface OrderInfo {
  buying_power: number
  sellable: number
  price: number | null
  tick: number
  currency: string
  upper_limit: number | null
  lower_limit: number | null
  limits: Limits
  halted: boolean
}

export type OrderStatus = 'pending_approval' | 'open' | 'partial' | 'filled' | 'cancelled' | 'rejected' | 'failed'

export interface Order {
  id: number
  created_at: string
  symbol: string
  name: string
  side: Side
  order_type: 'market' | 'limit'
  quantity: number
  price: number | null
  ref_price: number | null
  status: OrderStatus
  filled_quantity: number
  avg_fill_price: number | null
  filled_at: string | null
  broker_order_id: string | null
  source: 'manual' | 'agent'
  run_id: number | null
  reason: string
  error: string | null
  checks: string[]
  amount: number
}

export interface NewsItem {
  id: number
  url: string
  title: string
  source: string
  published_at: string | null
  snippet: string
  symbols: string[]
  market: boolean
}

export interface NewsResponse {
  scope: string
  targets: { symbol: string; name: string }[]
  items: NewsItem[]
  warnings: string[]
}

export type Sentiment = 'positive' | 'neutral' | 'negative' | 'mixed'

export interface Analysis {
  sentiment: Sentiment
  sentiment_score: number
  summary: string
  key_points: { point: string; news_ids: number[] }[]
  risks: string[]
  catalysts: string[]
  watch: string[]
  information_gaps: string[]
}

export interface NewsReport {
  title: string
  headline: string
  market_summary: string
  portfolio_view: string
  highlights: { symbol: string; verdict: string }[]
  action_items: string[]
  risks: string[]
  scope: string
  scope_label: string
  sections: { symbol: string | null; label: string; analysis: Analysis; news_ids: number[] }[]
  news: { id: number; title: string; source: string; published_at: string | null; url: string; symbols: string[] }[]
  provider: string
  citations_dropped: number
}

export interface ReportDetail extends Run {
  report: NewsReport | null
}

export interface Agent {
  key: string
  /** 직함 (토론 안에서 서로를 부르는 이름) */
  title: string
  /** 이름 · 전체 이름 · 국적 (다국적 팀) */
  name?: string
  full_name?: string
  country?: string
  /** 프로필 포켓몬 (전국도감 번호) — 서버가 안 보내면 people.ts 의 기본 번호 */
  pokemon?: number | null
  persona: string
  tools: string[]
  executor: boolean
  enabled: boolean
}

export interface ToolInfo {
  name: string
  title: string
  description: string
  executor_only: boolean
}

export interface AgentOptions {
  rounds: number
  model: string
  max_candidates: number
}

/** 에이전트 성과 · 표의 무게 (/api/invest/agent-scores, 백엔드 debate/track.py) */
export interface AgentScore {
  /** 채점된 판단 수 (판단 뒤 끝난 거래일이 있는 것) · 맞힌 수 · 적중률 · 코스피 대비 평균 초과수익(방향 반영) · 성과 배수 */
  judged: number; wins: number; hit: number; excess: number; skill: number
}
export interface AgentInfluence {
  /** 최근 입장 수 (매수 · 매도) */
  views: number; buy: number; sell: number
  avg_confidence: number
  /** 평균 한 표의 무게 = 확신 ÷ conviction_unit × 지금 성과 배수 */
  avg_weight: number
  /** 전체 무게 가운데 이 에이전트 몫 (0~1) */
  share: number
  /** 같은 날 같은 종목에 실제로 나간 에이전트 주문과 같은 편 · 반대편이었던 입장 수 */
  with_orders: number; against_orders: number
}
export interface AgentScores {
  scores: Record<string, AgentScore>
  influence: Record<string, AgentInfluence>
  horizon: number
  benchmark: string
  rules: {
    mode: 'continuous' | 'meetings'
    conviction_unit: number; min_skill: number; max_skill: number; prior: number; recent: number
    propose_min: number; for_min: number; against_max: number; max_oppose_share: number
  }
}

export interface AgentsResponse {
  roster: Agent[]
  tools: ToolInfo[]
  defaults: Agent[]
  options: AgentOptions  // 토론 설정 (실험실)
  /** 투자 비서 프로필 (이름·직함·국적) */
  assistant: { key: string; title: string; name: string; full_name: string; country: string }
  /** 운용 데스크 리서치 담당과 회의 명단 에이전트가 함께 맡는 일 */
  desk: DeskTeam
}

// ---- 운용 데스크 (시장 분석 → 섹터 탐색 → 종목 발굴 → 투자위원회 → 집행 → 리스크 감시) ----
export interface DeskPerson { key: string; title: string; name: string; full_name: string; country: string; role: string }
export interface DeskTeam { people: DeskPerson[]; duties: Record<string, string>; advisor?: DeskPerson; system?: DeskPerson }

/** 현황 서랍 — 보유 종목마다 손익·비중과 지표 (지표는 국내 종목만) */
export interface HoldingIndicator {
  symbol: string
  name: string
  where: string
  quantity: number
  avg_price: number | null
  last_price: number | null
  value_krw: number
  weight: number | null
  pnl_rate: number | null
  pnl_krw: number | null
  security_type?: string | null
  currency?: string | null
  change_1d: number | null
  change_5d: number | null
  change_20d: number | null
  rsi: number | null
  volume_ratio: number | null
  /** 이동평균 5·20·60 정렬: 1 정배열, -1 역배열, 0 혼조 */
  trend: number | null
}

// ---- 운용 목표 · 자문 상담 ----
export interface PolicyField {
  key: string
  label: string
  type: 'enum' | 'number' | 'list' | 'text'
  unit?: string | null
  required: boolean
  choices?: Record<string, string> | null
  value: unknown
  /** 화면 글 (정하지 않았으면 null) */
  text: string | null
}
export interface PolicyChange { field: string; label: string; before: string; after: string }
export interface PolicyView {
  policy: Record<string, unknown>
  missing: string[]
  history: { at: string; by: string; reason: string; changes: PolicyChange[] }[]
  fields: PolicyField[]
  updated_at: string | null
  updated_by: string | null
}
/** 자문 상담 — 열려 있으면 채팅 입력이 자문 에이전트에게 간다 */
export interface AdviceStatus {
  open: boolean
  started_at: string | null
  turns: number
  topics: { key: string; label: string; done: boolean }[]
  remaining: number
  profile: { key: string; title: string; name: string; full_name: string; country: string }
}
export type Regime = 'risk_on' | 'neutral' | 'risk_off'
export interface MarketView {
  regime: Regime
  headline: string
  summary: string
  favor: string[]
  avoid: string[]
  cash: 'raise' | 'keep' | 'lower'
  by: 'llm' | 'code'
  indicators: { symbol: string; name: string; unit?: string | null; price: number | null; change_rate: number | null }[]
  breadth: Partial<Record<'gainers' | 'losers', { avg: number; top: string[] }>>
  leaders: { symbol: string; name: string; change_rate: number | null }[]
  news: { title: string; source: string | null; at: string }[]
  source: string
}
export interface SectorRow { id: string; name: string; depth: number; rates: Record<string, number>; score: number | null; pick: 'over' | 'under' | null; intent: boolean }
export interface SectorView {
  overweight: { id: string; name: string; reason: string }[]
  underweight: { id: string; name: string; reason: string }[]
  summary: string
  by: 'llm' | 'code'
  count: number
  rows: SectorRow[]
}
export interface Ideas {
  pool: number
  picks: { symbol: string; name: string; security_type: string; sources: string[]; score: number | null; reason: string }[]
  summary: string
  by: 'llm' | 'code'
  rows: ScreeningRow[]
}
export interface DeskAlert { symbol: string; name: string; kind: 'drop' | 'jump' | 'breakout'; change: number; held: boolean; text?: string; at?: string }
export type DeskStepKey = 'market' | 'sector' | 'ideas' | 'committee' | 'execution' | 'risk'
export interface DeskStatus {
  enabled: boolean
  flexible: boolean
  people: DeskPerson[]
  duties: Record<string, string>
  process: { key: DeskStepKey; title: string; who: string; at: string | null; text: string | null; by: 'llm' | 'code' | null }[]
  running: { id: number; name: string } | null
  research: { at: string; run_id: number; trigger: string }[]
  alerts: DeskAlert[]
  market: MarketView | null
  sector: SectorView | null
  ideas: Ideas | null
  limits: { prep_min: number; watch_min: number; refresh_move: number; max_refresh: number; max_adhoc: number }
}

export type DebateScope = 'symbol' | 'sector' | 'portfolio' | 'scan' | 'autopilot'

/** 후보 선정 — 여러 종목을 훑은 점수표와 고른 종목 (시장 스캔·자동 운용) */
export interface ScreeningRow {
  symbol: string
  name: string
  sources: string[]
  score: number | null
  metrics: { price: number; change_1d: number; change_5d: number | null; change_20d: number | null; volume_ratio: number | null; rsi: number | null; trend: number } | null
  picked: boolean
  sector?: string | null
  /** 가치평가 요약 — 재무가 없으면 없다 */
  value?: ValueBrief | null
  /** 보유 종목 (발굴 목록에서 뺀 줄) */
  held?: boolean
}
export interface ValueBrief { score: number; grade: string; per: number | null; pbr: number | null; roe: number | null; upside: number | null; loss: boolean; report: string | null }

/** 판단 기록 — 섹터 평가·가치 스크린·발굴·회의 후보마다 기준과 줄별 근거 */
export type ScoreKind = 'sector' | 'value' | 'ideas' | 'meeting'
export interface ScoreRun {
  id: number
  created_at: string
  day: string
  kind: ScoreKind
  kind_label: string
  run_id: number | null
  regime: string
  rules_hash: string
  pool: number
  picked: number
  picked_by: string
  summary: string
  notes: string[]
  meta: Record<string, unknown>
}
export interface ScoreFactor { key: string; group: string; label: string; input: number | null; points: number }
export interface ValueItem { key: string; label: string; pillar: string; value: number | null; points: number; note: string }
export interface Valuation {
  score: number | null
  grade: string | null
  coverage: number
  reason: string
  pillars: Record<string, { label: string; weight: number; score: number }>
  items: ValueItem[]
  missing: string[]
  missing_labels?: string[]
  indicators: { market_cap: number | null; per: number | null; pbr: number | null; roe: number | null; upside: number | null; fair_value: number | null; loss: boolean }
  basis: { report?: string; period_end?: string; rcept_no?: string; fs_div?: string; ttm?: boolean }
  peer: { per: number | null; pbr: number | null } | null
}
export interface ScoreRow {
  id: number
  rank: number
  subject: string
  name: string
  score: number | null
  value_score: number | null
  price: number | null
  decision: string
  pick_rank: number | null
  reason: string
  sources: string[]
  detail: {
    sector?: string | null
    metrics?: ScreeningRow['metrics']
    factors?: { score: number; regime: string; groups: Record<string, { label: string; raw: number; weight: number; points: number }>; items: ScoreFactor[] } | null
    valuation?: Valuation | null
    rates?: Record<string, number>
  }
}
export interface ValuationStatus {
  configured: boolean
  corps: number
  symbols: number
  reports: number
  fetched_at: string | null
  synced_day: string | null
  last_run: string | null
  last_error: string
}
export interface Screening {
  pool: number
  rows: ScreeningRow[]
  picks: { symbol: string; reason: string }[]
  summary: string
  by: 'llm' | 'code'
}

export interface Candidate {
  symbol: string
  name: string
  security_type: string
  held: number
  /** 자동 운용: 후보가 된 이유 (운용 지시 · 보유 · 섹터 ○○) */
  why?: string
}

export type GateMode = 'unanimous' | 'supermajority' | 'rolling'

export interface AutopilotEntry {
  key: string
  time: string
  kind: 'fixed' | 'extra' | 'manual' | 'risk' | 'stream'
  /** 그 회의 안건과 꼭 볼 종목 (AI·비서가 정함) */
  agenda?: string | null
  focus?: string[]
  by?: 'ai' | 'assistant' | null
  origin: string | null
  reason: string | null
  status: 'pending' | 'started' | 'skipped' | 'missed'
  run_id: number | null
  note: string | null
}

export interface AutopilotIntent {
  id: number
  at: string
  text: string
  /** 다음 자동 판단 한 번에만 쓰고 지운다 */
  once?: boolean
}

export interface PerfPosition {
  symbol: string
  name: string
  where: string
  value_krw: number
  weight: number | null
  pnl_rate: number | null
}

/** 운용 수익률의 기준 원금 — 넣은 돈(추가 입금 +, 출금 −) */
export interface Baseline {
  contributions: { date: string; amount: number; note: string }[]
  amount: number
  since: string | null
}

/** 수익률 — 운용 수익률(기준 원금 대비 토스 계좌) + 내 투자현황(원금을 아는 보유) + AI 주문 뒤 변화 (현황 서랍·보고서) */
export interface Performance {
  baseline: (Baseline & { value_krw: number | null; pnl_krw: number | null; return_rate: number | null; days: number }) | null
  portfolio: {
    /** 항목별 — 종목마다 매입·평가·손익·수익률·기여도(손익 ÷ 기준 원금, 없으면 매입 원금 합) */
    items: { symbol: string; name: string; where: string; quantity: number | null; cost_krw: number | null; value_krw: number
             pnl_krw: number | null; pnl_rate: number | null; weight: number | null; contribution: number | null }[]
    contribution_base: string
    total_assets_krw: number
    stock_value_krw: number
    cash_krw: number
    cash_ratio: number | null
    cost_krw: number
    pnl_krw: number
    return_rate: number | null
    positions: number
    unpriced: number
    top: PerfPosition[]
    best: PerfPosition | null
    worst: PerfPosition | null
  } | null
  portfolio_note: string | null
  ai: {
    count: number
    amount_krw: number
    change: number | null
    pnl_krw: number
    note: string | null
    orders: { id: number; run_id: number | null; at: string; symbol: string; name: string; side: Side; quantity: number; fill: number
              now: number | null; amount_krw: number; change: number | null; pnl_krw: number | null }[]
    /** 주문을 낸 회의별 합 */
    by_meeting: { run_id: number | null; name: string; at: string; orders: number; amount_krw: number; pnl_krw: number; change: number | null }[]
  }
}

export interface BriefingHolding {
  symbol: string; name: string; where: string; quantity: number; value_krw: number
  weight: number | null; pnl_krw: number | null; pnl_rate: number | null; day_change: number | null
}
export interface BriefingMeeting {
  id: number; name: string; status: string; at: string; headline: string | null
  kind?: string | null
  summary?: string | null
  follow_up?: string[]
  orders?: { name: string; symbol: string; side: 'buy' | 'sell'; quantity: number; status: OrderStatus; change: number | null }[]
  /** 진행 중인 회의(상시 운용 채널)의 지금까지 */
  live?: { talks: number; board: { symbol: string; name: string; buy: number; sell: number }[]; trader: string | null } | null
}
export interface BriefingNews { title: string; source: string; at: string; name?: string }

/** 한 페이지 보고서 — 숫자는 코드, comment 만 비서. v 2: 보유 종목·현황 · 회의 요약 · 장내 이슈만
 * (그 전 보고서에는 plan·intents·research 가 있고 holdings·warnings 가 없다 — 화면은 둘 다 새 모양으로 그린다) */
export interface Briefing {
  title: string
  created_at: string
  source: string
  comment?: string
  v?: number
  /** 막힌 것 (전체 중지·주문 조건) */
  warnings?: string[]
  performance: Performance
  holdings?: BriefingHolding[]
  holdings_more?: number
  /** 운용 수익률 추이 — 지나간 보고서의 값 + 지금, index = 같은 시각의 지수 변화 (예전 보고서에는 없음) */
  history?: { index: string | null; points: { at: string; rate: number; index: number | null }[] } | null
  market: {
    indicators: { name: string; price: number | null; unit?: string | null; change_rate: number | null }[]
    note: string | null
    view?: { at: string; by: string; regime: string | null; headline: string | null; summary: string | null; favor: string[]; avoid: string[]; cash: string | null } | null
    /** rows = 비중 의견을 낸 섹터의 기간 등락 (예전 보고서에는 없음) */
    sectors?: { at: string; overweight: { name: string; reason: string }[]; underweight: string[]
      rows?: { name: string; side: 'over' | 'under'; reason: string; rates: Record<string, number> }[] } | null
    news?: BriefingNews[]
    holding_news?: BriefingNews[]
    alerts?: string[]
  }
  /** 예전 보고서만 */
  plan?: { blocked: string | null; halted: boolean }
  /** 운용 목표와 목표 대비 지금 */
  policy?: {
    lines: string[]
    missing: string[]
    updated_at: string | null
    equity: { target: number; actual: number } | null
    return: { actual: number; days: number; target_year: number | null; target_so_far: number | null } | null
    benchmark: { symbol: string; name: string; change: number; excess: number; since: string } | null
  }
  /** 예전 보고서만 — 그날 운용 데스크 리서치 (새 보고서는 market.view·sectors·alerts) */
  research?: { at: string; regime: string | null; headline: string | null; cash: string | null; overweight: string[]; underweight: string[]; ideas: string[]; alerts: string[] } | null
  meetings: BriefingMeeting[]
}

/** 아침 브리핑 설정 — 매일 이 시각에 비서가 보고서를 채팅방에 (휴장일은 건너뜀) */
export interface MorningBriefing {
  enabled: boolean
  time: string
  /** 0=월 … 6=일 */
  weekdays: number[]
  /** 매월 날짜 (비우면 날짜 제한 없음) */
  month_days: number[]
  skip_holidays: boolean
  /** '매일 08:30' · '매주 월·금 08:00' · '매월 1·15일 08:30' */
  summary: string
  /** 마지막으로 보낸(또는 휴장으로 건너뛴) 날 */
  last: string | null
}

/** 투자 비서 대화 — 내 말 · 비서 답 · 다른 에이전트 답 · 한 일(운용 지시 정리·판단 시작) */
export interface AssistantMessage {
  id: number
  created_at: string
  role: 'user' | 'assistant' | 'agent' | 'event'
  text: string
  meta: {
    report?: Briefing; body?: string; title?: string; name?: string; agent?: string; kind?: string; run_id?: number; autopilot?: boolean
    tools?: string[]; error?: string
    /** 자문 에이전트가 준 누르는 답 */
    choices?: string[]; advice?: boolean
    /** 운용 데스크 에이전트의 말 — 단계와 카드 자료 */
    desk?: 'market' | 'sector' | 'ideas' | 'alert'
    data?: MarketView | SectorView | Ideas | { alerts: DeskAlert[]; by: string }
  }
}

export interface AutopilotStatus {
  /** flexible = 새 안건(발굴 목록·운용 지시·경보)이 있을 때만 회의 */
  config: { enabled: boolean; slots: string[]; max_extra: number; flexible: boolean; mode: 'continuous' | 'meetings' }
  intents: AutopilotIntent[]
  today: { date: string; entries: AutopilotEntry[]; extra_used: number }
  window: [string, string] | null
  window_note: string | null
  next: AutopilotEntry | null
  blocked: string | null
  limits: { open_after_min: number; close_before_min: number; grace_min: number; min_gap_min: number; lead_min: number; max_intents: number; max_adhoc: number; max_assistant: number }
}

export interface ScopePreview {
  scope: DebateScope
  label: string
  candidates: Candidate[]
  sector?: { id: string; name: string; summary: string | null; change_rates: Record<string, number | null>; stock_count: number; etf_count: number }
  screening?: Screening
  notes: string[]
}

export interface Sector {
  id: string
  name: string
  depth: number
  parent: string | null
}

export interface View {
  symbol: string
  stance: Stance
  confidence: number
  reason: string
  implicit: boolean  // 에이전트가 말하지 않아 의견 없음으로 채운 후보
}

export interface SessionOrder {
  id: number
  symbol: string
  name: string
  side: Side
  quantity: number
  status: OrderStatus
}

export interface ExternalHolding {
  id: number
  account_name: string
  symbol: string
  name: string
  security_type: string
  currency: string
  quantity: number
  avg_price: number | null
  memo: string
  last_price: number | null
  priced: boolean
  value: number
  value_krw: number
  pnl_rate: number | null
}

/** 내 투자현황의 보유 한 건 (토스 국내·해외 또는 다른 계좌) */
export interface Position {
  /** '토스' 또는 다른 계좌 이름 */
  where: string
  symbol: string
  name: string
  currency: string
  quantity: number
  avg_price: number | null
  last_price: number | null
  value: number
  value_krw: number
  pnl_rate: number | null
  /** 다른 계좌: 현재가를 구했는지 (못 구하면 평단으로 평가) */
  priced?: boolean
  weight: number | null
  security_type: string
  /** 매입 원금·평가 손익 (원화). 평단을 모르거나 현재가가 없으면 null */
  cost_krw: number | null
  pnl_krw: number | null
}

export interface PortfolioOverview {
  positions: Position[]
  total_assets_krw: number
  stock_value_krw: number
  cash: { krw: number; usd: number | null; usd_in_krw: number }
  cash_ratio: number | null
  usd_krw: number | null
  concentration: { top1: number | null; top3: number | null; hhi: number | null; count: number }
  other_accounts: string[]
}

export interface Statement {
  round: number
  agent: string
  title: string
  views?: View[]  // 후보별 입장 (예전 세션에는 없음)
  stance: Stance | 'mixed'
  confidence: number
  thesis: string
  evidence: { fact: string; tool: string }[]
  risks: string[]
  message_to_others: string
  responses: { to: string; agree: boolean; point: string }[]
  changed: boolean
  changed_symbols?: string[]
  previous?: Record<string, Stance>
  previous_stance: Stance | null
  reason_for_change: string
  error: string | null
  /** 상시 운용: 트레이더 점검 발언 */
  executor?: boolean
}

export type GateStatus = 'aligned' | 'conflict' | 'no_view' | 'not_held' | 'incomplete' | 'pending'

export interface SymbolGate {
  status: GateStatus
  direction: Side | null
  counts: Record<Stance, number>
  reason: string
}

export interface Gate extends SymbolGate {
  mode?: GateMode  // 예전 세션에는 없음 = unanimous
  symbols?: Record<string, SymbolGate>  // 종목별 (예전 세션에는 없음)
  aligned?: { symbol: string; direction: Side }[]
}

export interface Decision {
  decisions?: { symbol: string; name: string; decision: 'buy' | 'sell' | 'hold'; quantity: number }[]
  decision: 'buy' | 'sell' | 'hold' | 'mixed'
  quantity: number
  rationale: string
  risk_notes: string[]
  by: string
  note?: string
}

export interface ToolCall {
  agent: string
  tool: string
  args: Record<string, unknown>
  result?: string
  error?: string
}

export interface SessionDetail extends Run {
  session: Record<string, unknown> | null
  orders: Order[]
}

/** 채팅방 메시지 (messages.jsonl 한 줄) */
export type ChatMessage = { seq: number; ts: string } & (
  | {
      type: 'start'; name: string; rounds: number; provider: string; auto_approve: boolean
      agents: { key: string; title: string; executor: boolean; name?: string | null; full_name?: string | null; country?: string | null }[]
      scope?: DebateScope; label?: string; candidates?: Candidate[]; notes?: string[]; model?: string
      gate?: GateMode; autopilot?: { slot: string | null; trigger: string | null; agenda?: string | null } | null
      /** 상시 운용 채널 — 장 내내 이어지는 대화 */
      stream?: boolean
    }
  | { type: 'round'; round: number; label: string; agents: string[]; stream?: boolean; candidates?: Candidate[] }
  | { type: 'tool'; agent: string; title: string; tool: string; tool_title: string; symbol?: string | null }
  | { type: 'user'; text: string; standing?: boolean }
  | ({ type: 'screening'; picker?: string | null } & Screening)
  | { type: 'minutes'; headline: string; summary: string; points: string[]; follow_up: string[]; by: 'assistant' | 'code'; kind: string }
  | { type: 'schedule'; accepted: boolean; action: 'none' | 'add' | 'move'; time: string | null; reason: string; note: string; from?: string | null }
  | ({ type: 'statement' } & Statement)
  | { type: 'gate'; gate: Gate; names?: Record<string, string> }
  | { type: 'trader_start'; agent: string; title: string }
  | { type: 'order'; order: Order }
  | { type: 'decision'; agent?: string; title?: string; decision: Decision; order_ids?: number[] }
  | { type: 'end'; status: 'succeeded' | 'failed' | 'cancelled'; message: string }
)

export interface SessionMessages {
  run: Run
  messages: ChatMessage[]
  orders: Order[]
}

/** 서버 오류: 주문 검사 실패면 checks(통과·실패 항목)가 함께 온다 */
export class ApiError extends Error {
  status: number
  checks: string[]
  code: string | null
  detail: Record<string, unknown>

  constructor(message: string, status: number, checks: string[] = [], code: string | null = null, detail: Record<string, unknown> = {}) {
    super(message)
    this.status = status
    this.checks = checks
    this.code = code
    this.detail = detail
  }
}

// 주문 PIN — 서버가 요구하면 한 번 묻고 이 탭 메모리에만 둔다 (새로 고침하면 다시 묻는다, 저장하지 않음)
let tradingPin: string | null = null

async function request<T>(method: string, url: string, body?: unknown, retried = false): Promise<T> {
  const headers: Record<string, string> = {}
  const init: RequestInit = { method, headers }
  if (body !== undefined) {
    init.body = JSON.stringify(body)
    headers['Content-Type'] = 'application/json'
  }
  if (tradingPin && method !== 'GET') headers['X-Trading-Pin'] = tradingPin
  const res = await fetch(url, init)
  if (!res.ok) {
    let message = `요청 실패 (${res.status})`
    let checks: string[] = []
    let code: string | null = null
    let detail: Record<string, unknown> = {}
    try {
      const data = await res.json()
      if (typeof data.detail === 'string') message = data.detail
      else if (data.detail && typeof data.detail.message === 'string') {
        message = data.detail.message
        checks = data.detail.checks ?? []
        code = data.detail.code ?? null
        detail = data.detail
      } else if (Array.isArray(data.detail)) message = data.detail.map((d: { msg: string }) => d.msg).join(', ')
    } catch {
      /* 본문이 JSON이 아니면 기본 메시지 */
    }
    if (res.status === 403 && (code === 'pin_required' || code === 'pin_invalid') && !retried) {
      tradingPin = null
      const pin = window.prompt(code === 'pin_invalid' ? '주문 PIN 이 틀렸어요. 다시 입력하세요' : '실계좌 주문 PIN 을 입력하세요 (.env 의 WT_TRADING_PIN)')
      if (pin && pin.trim()) {
        tradingPin = pin.trim()
        return request<T>(method, url, body, true)
      }
    }
    if (code === 'pin_invalid') tradingPin = null
    throw new ApiError(message, res.status, checks, code, detail)
  }
  return (res.status === 204 ? undefined : await res.json()) as T
}

const enc = encodeURIComponent

/** 거래 기록 — 체결된 주문 하나 (백엔드 invest/ops/trades.py) */
export interface Trade {
  id: number; at: string; symbol: string; name: string; currency: string; side: Side; order_type: 'market' | 'limit'
  status: OrderStatus; quantity: number; price: number | null; amount: number | null
  now_price: number | null; now_value: number | null; source: 'manual' | 'agent'; run_id: number | null; run_name: string | null
  reason: string
  /** 매수: 지금 가치 − 매수 금액 */
  pnl?: number | null; pnl_rate?: number | null
  /** 매도: 단가(토스 평단 또는 앞선 매수의 이동평균)로 낸 실현 손익 */
  basis_price?: number | null; basis_quantity?: number; basis_from?: 'broker' | 'history' | null
  realized?: number | null; realized_rate?: number | null
  /** 매도: 판 수량의 지금 가치 − 매도 금액 (+ 면 판 뒤에 올랐다) */
  after?: number | null; after_rate?: number | null
}
export interface TradeSymbol {
  symbol: string; name: string; currency: string; buy_count: number; sell_count: number
  bought: number; sold: number; realized: number; known_sells: number; unknown_sold: number
  held_quantity: number; held_cost: number; now_price: number | null; held_value: number | null; unrealized: number | null
}
export interface TradeHistory {
  trades: Trade[]
  symbols: TradeSymbol[]
  totals: { trades: number; buys: number; sells: number; bought: number; sold: number; realized: number; known_sells: number
            held_cost: number; held_value: number | null; unrealized: number | null; priced: boolean; other_currency: number }
  quoted: boolean
}
export interface TradeOpinion {
  agent: string; title: string; at: string | null; round: number | null; stance: Stance; confidence: number | null
  reason: string; changed: boolean
}
export interface TradeDebate {
  order_id: number; symbol: string; side: Side; source: 'manual' | 'agent'; reason: string; checks: string[]
  run_id: number | null; run_name: string | null; run_at: string | null; found: boolean; agenda: string | null
  gate: { at: string | null; mode: string | null; status: string | null; direction: Side | null
          counts: { buy: number; sell: number; none: number } | null; weights: { buy: number; sell: number } | null; reason: string } | null
  decision: { at: string | null; title: string | null; rationale: string; risk_notes: string[]; decision: string | null; quantity: number | null } | null
  opinions: TradeOpinion[]
}

export const investApi = {
  status: () => request<InvestStatus>('GET', '/api/invest/status'),
  config: () => request<InvestConfig>('GET', '/api/invest/config'),
  saveKeys: (keys: Partial<Record<KeyStatus['key'], string>> & { clear?: string[] }) =>
    request<InvestConfig>('PUT', '/api/invest/config/keys', keys),
  testConnection: () =>
    request<{
      ok: boolean
      selected_seq: string | null  // 지금 설정으로 쓰일 계좌 순번
      account_error: string | null
      accounts: { account_seq: number; account_type: string; account_no: string }[]
    }>(
      'POST',
      '/api/invest/config/test',
    ),
  saveTrading: (body: { limits?: Partial<Limits>; agent_auto_approve?: boolean; halted?: boolean }) =>
    request<InvestConfig>('PUT', '/api/invest/config/trading', body),

  search: (q: string) => request<SearchHit[]>('GET', `/api/invest/search?q=${enc(q)}&limit=12`),
  stock: (symbol: string) => request<StockSnapshot>('GET', `/api/invest/stocks/${enc(symbol)}`),
  candles: (symbol: string, interval: '1d' | '1m', count: number) =>
    request<{ candles: Candle[]; provider: string }>(
      'GET',
      `/api/invest/stocks/${enc(symbol)}/candles?interval=${interval}&count=${count}`,
    ),
  orderbook: (symbol: string) => request<Orderbook>('GET', `/api/invest/stocks/${enc(symbol)}/orderbook`),
  investor: (symbol: string) => request<InvestorDay[]>('GET', `/api/invest/stocks/${enc(symbol)}/investor?count=10`),
  market: () => request<{ provider: string; indicators: Indicator[]; session: MarketSession }>('GET', '/api/invest/market'),
  rankings: (kind: 'amount' | 'volume' | 'gainers' | 'losers') =>
    request<Ranking[]>('GET', `/api/invest/rankings?kind=${kind}&count=15`),
  watchlist: () => request<WatchItem[]>('GET', '/api/invest/watchlist'),
  addWatch: (symbol: string, opts: { name?: string; market?: string; security_type?: string; group_id?: number } = {}) =>
    request<WatchItem[]>('POST', '/api/invest/watchlist', { symbol, ...opts }),
  removeWatch: (symbol: string, groupId?: number) =>
    request<WatchItem[]>('DELETE', `/api/invest/watchlist/${enc(symbol)}${groupId ? `?group_id=${groupId}` : ''}`),
  watchGroups: () => request<WatchGroup[]>('GET', '/api/invest/watch-groups'),
  createGroup: (name: string) => request<WatchGroup[]>('POST', '/api/invest/watch-groups', { name }),
  renameGroup: (id: number, name: string) => request<WatchGroup[]>('PATCH', `/api/invest/watch-groups/${id}`, { name }),
  deleteGroup: (id: number) => request<WatchGroup[]>('DELETE', `/api/invest/watch-groups/${id}`),

  account: () => request<AccountSummary>('GET', '/api/invest/account'),
  orderInfo: (symbol: string) => request<OrderInfo>('GET', `/api/invest/order-info/${enc(symbol)}`),
  placeOrder: (body: {
    symbol: string
    side: Side
    order_type: 'market' | 'limit'
    quantity: number
    price?: number | null
    confirm_live?: boolean
  }) => request<Order>('POST', '/api/invest/orders', body),
  orders: (status: 'all' | 'open' | 'closed') => request<Order[]>('GET', `/api/invest/orders?status=${status}&limit=100`),
  approve: (id: number, confirmLive: boolean) =>
    request<Order>('POST', `/api/invest/orders/${id}/approve`, { confirm_live: confirmLive }),
  reject: (id: number) => request<Order>('POST', `/api/invest/orders/${id}/reject`),
  cancel: (id: number) => request<Order>('POST', `/api/invest/orders/${id}/cancel`),
  portfolio: () => request<PortfolioOverview>('GET', '/api/invest/portfolio'),
  externalHoldings: () => request<ExternalHolding[]>('GET', '/api/invest/external-holdings'),
  addExternal: (body: { symbol: string; name?: string; quantity: number; avg_price?: number | null; account_name?: string; memo?: string }) =>
    request<ExternalHolding[]>('POST', '/api/invest/external-holdings', body),
  updateExternal: (id: number, body: { quantity?: number; avg_price?: number | null; account_name?: string; memo?: string }) =>
    request<ExternalHolding[]>('PATCH', `/api/invest/external-holdings/${id}`, body),
  deleteExternal: (id: number) => request<ExternalHolding[]>('DELETE', `/api/invest/external-holdings/${id}`),

  news: (scope: 'holdings' | 'watchlist' | 'market' | 'symbol', opts: { symbol?: string; name?: string; refresh?: boolean } = {}) => {
    const p = new URLSearchParams({ scope, limit: '40' })
    if (opts.symbol) p.set('symbol', opts.symbol)
    if (opts.name) p.set('name', opts.name)
    if (opts.refresh) p.set('refresh', 'true')
    return request<NewsResponse>('GET', `/api/invest/news?${p}`)
  },
  reports: () => request<Run[]>('GET', '/api/invest/reports'),
  createReport: (body: { scope: string; symbols?: string[]; model?: string }) =>
    request<Run>('POST', '/api/invest/reports', body),
  report: (id: number) => request<ReportDetail>('GET', `/api/invest/reports/${id}`),

  agents: () => request<AgentsResponse>('GET', '/api/invest/agents'),
  agentScores: () => request<AgentScores>('GET', '/api/invest/agent-scores'),
  saveAgents: (roster: Agent[]) => request<AgentsResponse>('PUT', '/api/invest/agents', { roster }),
  resetAgents: () => request<AgentsResponse>('POST', '/api/invest/agents/reset'),
  sessions: (symbol?: string, limit = 20) =>
    request<Run[]>('GET', `/api/invest/agent-sessions?limit=${limit}${symbol ? `&symbol=${enc(symbol)}` : ''}`),
  saveAgentOptions: (opts: Partial<AgentOptions>) => request<AgentsResponse>('PUT', '/api/invest/agents/options', opts),
  sectors: () => request<Sector[]>('GET', '/api/invest/sectors'),
  scoreRuns: (kind?: ScoreKind) => request<ScoreRun[]>('GET', `/api/invest/score-runs${kind ? `?kind=${kind}` : ''}`),
  scoreRun: (id: number) => request<ScoreRun & { rows: ScoreRow[] }>('GET', `/api/invest/score-runs/${id}`),
  scoreRules: (hash: string) => request<{ hash: string; created_at: string; rules: Record<string, unknown> }>('GET', `/api/invest/score-rules/${hash}`),
  valuationStatus: () => request<ValuationStatus>('GET', '/api/invest/valuation/status'),
  scopePreview: (scope: DebateScope, opts: { symbol?: string; sector_id?: string }) => {
    const p = new URLSearchParams({ scope, symbol: opts.symbol ?? '', sector_id: opts.sector_id ?? '' })
    return request<ScopePreview>('GET', `/api/invest/agent-scope?${p}`)
  },
  createSession: (body: { scope: DebateScope; symbol?: string; sector_id?: string; note?: string }) =>
    request<Run>('POST', '/api/invest/agent-sessions', body),
  chat: (id: number, text: string) => request<ChatMessage>('POST', `/api/invest/agent-sessions/${id}/chat`, { text }),
  session: (id: number) => request<SessionDetail>('GET', `/api/invest/agent-sessions/${id}`),
  activeSession: () => request<Run | null>('GET', '/api/invest/agent-sessions/active'),
  stopSession: (id: number) => request<Run>('POST', `/api/invest/agent-sessions/${id}/stop`),
  messages: (id: number, after: number) => request<SessionMessages>('GET', `/api/invest/agent-sessions/${id}/messages?after=${after}`),

  autopilot: () => request<AutopilotStatus>('GET', '/api/invest/autopilot'),
  saveAutopilot: (body: { enabled?: boolean; slots?: string[]; max_extra?: number; flexible?: boolean; mode?: 'continuous' | 'meetings' }) =>
    request<AutopilotStatus>('PUT', '/api/invest/autopilot', body),
  addIntent: (text: string) => request<AutopilotStatus>('POST', '/api/invest/autopilot/intents', { text }),
  deleteIntent: (id: number) => request<AutopilotStatus>('DELETE', `/api/invest/autopilot/intents/${id}`),
  autopilotPreview: () => request<ScopePreview>('GET', '/api/invest/autopilot/preview'),
  runAutopilot: () => request<Run>('POST', '/api/invest/autopilot/run'),
  desk: () => request<DeskStatus>('GET', '/api/invest/desk'),
  policy: () => request<PolicyView>('GET', '/api/invest/policy'),
  holdingIndicators: () =>
    request<{ rows: HoldingIndicator[]; total_assets_krw: number; cash_ratio: number | null; source: string }>('GET', '/api/invest/holdings/indicators'),
  startAdvice: (topics?: string[]) => request<AdviceStatus>('POST', '/api/invest/advice/start', { topics: topics ?? null }),
  stopAdvice: () => request<AdviceStatus>('POST', '/api/invest/advice/stop'),
  runResearch: (note = '') => request<{ id: number }>('POST', '/api/invest/desk/research', { note }),

  performance: () => request<Performance>('GET', '/api/invest/performance'),
  trades: () => request<TradeHistory>('GET', '/api/invest/trades'),
  tradeDebate: (id: number) => request<TradeDebate>('GET', `/api/invest/trades/${id}/debate`),
  saveBaseline: (contributions: Baseline['contributions']) => request<Baseline>('PUT', '/api/invest/baseline', { contributions }),
  addContribution: (c: { date: string; amount: number; note?: string }) => request<Baseline>('POST', '/api/invest/baseline/contributions', c),
  briefing: () => request<Briefing & { text: string }>('GET', '/api/invest/briefing'),
  morning: () => request<MorningBriefing>('GET', '/api/invest/morning-briefing'),
  saveMorning: (body: { enabled?: boolean; time?: string; weekdays?: number[]; month_days?: number[]; skip_holidays?: boolean }) =>
    request<MorningBriefing>('PUT', '/api/invest/morning-briefing', body),
  sendMorning: () => request<AssistantMessage>('POST', '/api/invest/morning-briefing/send'),
  assistantMessages: (after: number) =>
    request<{ messages: AssistantMessage[]; pending: boolean; advice: AdviceStatus }>('GET', `/api/invest/assistant/messages?after=${after}`),
  sayToAssistant: (text: string) =>
    request<{ message: AssistantMessage; pending: boolean }>('POST', '/api/invest/assistant/messages', { text }),
}
