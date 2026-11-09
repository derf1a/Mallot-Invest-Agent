/**
 * Agents — 에이전트마다 지금 코드 · 설정 그대로: 시스템 프롬프트(예시 세션으로 그린 모양), 쓸 수 있는 도구 · 규칙,
 * LLM 경로(어디서 정해졌는지 · 여기서 바꾸기), 누구와 이어져 있는지, 최근 7일 활동(트레이스).
 * 성향 · 도구 · 명단 편집은 Team 화면에서 한다 (주문 PIN).
 */
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useLoad } from '../../shared/useLoad'
import { systemApi, type Route } from '../settings/api'
import { githubHref, observeApi, type AgentGroup, type AgentSpec, type AgentStat } from './api'
import CodeBlock from './CodeBlock'
import { agentLabel, fmtDur, fmtUsd } from './format'

const GROUPS: { key: AgentGroup; title: string; note: string }[] = [
  { key: 'debate', title: '토론 명단', note: '회의 · 상시 운용에서 입장을 내는 분석 에이전트와 트레이더' },
  { key: 'desk', title: '리서치 데스크', note: '시장 분석 → 섹터 탐색 → 종목 발굴' },
  { key: 'helper', title: '비서 · 도우미', note: '관리자와 대화 · 일정 · 회의록' },
]
const TABS = ['개요', '프롬프트', '도구', '규칙', '연결'] as const
type Tab = (typeof TABS)[number]

const SOURCE_LABEL: Record<string, string> = {
  toss_market: '토스 시세', toss_account: '토스 계좌', news_rss: '뉴스 RSS', dart: 'OpenDART', trading: '주문', app: '앱',
}
const ROUTE_SOURCE: Record<Route['source'], string> = {
  agent: '이 에이전트 경로', call: '토론 설정의 모델', feature: '기능 경로', default: '기본 경로', image: '이미지 → Claude',
}

export default function AgentsPage() {
  const [params, setParams] = useSearchParams()
  const { data: agents, error, reload } = useLoad(() => observeApi.agents(), [])
  const { data: stats } = useLoad(() => observeApi.agentStats(7), [])
  const key = params.get('key') ?? agents?.[0]?.key ?? null
  const agent = agents?.find((a) => a.key === key) ?? null
  return (
    <section className="page observe-page">
      <div className="page-header">
        <h1>Agents</h1>
        <span className="muted small">프롬프트 · 도구 · 규칙은 지금 코드 값, 경로는 지금 설정으로 계산</span>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="agents-split">
        <nav className="card agent-list" aria-label="에이전트">
          {GROUPS.map((g) => (
            <div key={g.key}>
              <h4 title={g.note}>{g.title}</h4>
              {(agents ?? []).filter((a) => a.group === g.key).map((a) => (
                <button key={a.key} className={a.key === key ? 'on' : ''} onClick={() => setParams({ key: a.key })}>
                  <span className={a.enabled ? '' : 'muted'}>{a.title}</span>
                  <span className="muted small">{a.name || a.role}</span>
                  <span className={`route-pill p-${a.route.provider}`}>{a.route.provider}</span>
                </button>
              ))}
            </div>
          ))}
        </nav>
        {agent ? <AgentDetail key={agent.key} a={agent} all={agents ?? []} stat={stats?.find((s) => s.agent === agent.key)}
          onChanged={reload} /> : <div className="card muted">에이전트를 고르세요.</div>}
      </div>
    </section>
  )
}

function AgentDetail({ a, all, stat, onChanged }: { a: AgentSpec; all: AgentSpec[]; stat?: AgentStat; onChanged: () => void }) {
  const [tab, setTab] = useState<Tab>('개요')
  return (
    <div className="card agent-detail">
      <div className="agent-head">
        <h2>{a.title}</h2>
        <span className="muted">{[a.name && a.full_name ? `${a.name} (${a.full_name})` : a.name, a.country].filter(Boolean).join(' · ')}</span>
        {!a.enabled && <span className="chip">꺼짐</span>}
        {a.role === 'executor' && <span className="chip missed">주문 도구 보유</span>}
        {a.warning && <span className="chip missed">{a.warning}</span>}
      </div>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>
            {t}{t === '도구' ? ` ${a.tools.length}` : t === '규칙' ? ` ${a.rules.length}` : ''}
          </button>
        ))}
      </div>
      {tab === '개요' && <Overview a={a} stat={stat} onChanged={onChanged} />}
      {tab === '프롬프트' && (
        <div className="stack">
          {a.persona && <><h4>성향 (Team 화면에서 편집)</h4><p>{a.persona}</p></>}
          <CodeBlock text={a.system_prompt} note={`시스템 프롬프트 — 예시 세션(가짜 후보 · 보유)으로 그린 모양 · ${a.system_prompt.length.toLocaleString()}자`} />
          {a.extra_prompts.map((p) => (
            <div key={p.title}>
              <h4>{p.title} <a className="small" href={githubHref(p.ref)} target="_blank" rel="noreferrer">코드 ↗</a></h4>
              <CodeBlock text={p.text} />
            </div>
          ))}
        </div>
      )}
      {tab === '도구' && <Tools a={a} />}
      {tab === '규칙' && (
        <ul className="rule-list">
          {a.rules.map((r) => (
            <li key={r.title}>
              <b>{r.title}</b> <a className="small" href={githubHref(r.ref)} target="_blank" rel="noreferrer">{r.ref.label} ↗</a>
              <pre className="prompt">{r.text}</pre>
            </li>
          ))}
        </ul>
      )}
      {tab === '연결' && <Links a={a} all={all} />}
    </div>
  )
}

function Overview({ a, stat, onChanged }: { a: AgentSpec; stat?: AgentStat; onChanged: () => void }) {
  return (
    <div className="stack">
      <RouteEditor a={a} onChanged={onChanged} />
      <div>
        <h4>최근 7일 (트레이스)</h4>
        {stat ? (
          <div className="stat-tiles">
            <span><b>{stat.turns}</b> 차례</span>
            <span><b>{stat.llm_calls}</b> LLM 호출</span>
            <span><b>{stat.tool_calls}</b> 도구 호출</span>
            <span><b>{(stat.input_tokens + stat.output_tokens).toLocaleString()}</b> 토큰</span>
            <span><b>{fmtUsd(stat.cost_usd)}</b> 요금 환산</span>
            <span><b>{fmtDur(stat.avg_turn_ms)}</b> 평균 차례</span>
            {stat.errors > 0 && <span className="error"><b>{stat.errors}</b> 실패</span>}
          </div>
        ) : <p className="muted small">아직 기록이 없어요.</p>}
        <Link to={`/observe/traces?agent=${a.key}`} className="small">이 에이전트가 나온 트레이스 →</Link>
      </div>
      <p className="small">
        기능 키 <code>{a.feature}</code>
        {a.refs.map((r) => <span key={r.label}> · <a href={githubHref(r)} target="_blank" rel="noreferrer">{r.label} ↗</a></span>)}
      </p>
    </div>
  )
}

function RouteEditor({ a, onChanged }: { a: AgentSpec; onChanged: () => void }) {
  const { data: routes, reload } = useLoad(() => systemApi.routes(), [])
  const { data: vllm } = useLoad(() => systemApi.vllm(), [])
  const saved = routes?.agents[a.key] ?? ''
  const [provider, setProvider] = useState(saved.split(':')[0] || '')
  const [model, setModel] = useState(saved.includes(':') ? saved.split(':').slice(1).join(':') : '')
  const [msg, setMsg] = useState<string | null>(null)
  const save = async (text: string) => {
    try {
      await systemApi.setRoutes({ agents: { [a.key]: text } })
      setMsg(text ? `저장했어요 — ${text}` : '이 에이전트 경로를 지웠어요 (다음 순서를 따름)')
      reload()
      onChanged()
    } catch (e) {
      setMsg((e as Error).message)
    }
  }
  const text = provider ? (model.trim() && provider !== 'mlx' ? `${provider}:${model.trim()}` : provider) : ''
  return (
    <div className="route-box">
      <h4>LLM 경로</h4>
      <p>
        지금: <b>{a.route.label}</b> <span className="muted small">← {ROUTE_SOURCE[a.route.source]}</span>
      </p>
      <div className="route-form">
        <select value={provider} onChange={(e) => setProvider(e.target.value)} aria-label="공급자">
          <option value="">따로 정하지 않음 (다음 순서)</option>
          <option value="claude">Claude (Agent SDK)</option>
          <option value="mlx">로컬 MLX</option>
          <option value="vllm">vLLM (OpenAI 호환)</option>
        </select>
        {provider && provider !== 'mlx' && (
          <input value={model} onChange={(e) => setModel(e.target.value)} list={`models-${a.key}`}
            placeholder={provider === 'claude' ? 'claude-sonnet-5-5 (비우면 기본)' : '모델 이름 (비우면 서버 기본)'} />
        )}
        <datalist id={`models-${a.key}`}>
          {provider === 'vllm' && (vllm?.models ?? []).map((m) => <option key={m} value={m} />)}
          {provider === 'claude' && ['claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5'].map((m) => <option key={m} value={m} />)}
        </datalist>
        <button className="primary" disabled={text === saved} onClick={() => save(text)}>저장</button>
      </div>
      {provider === 'vllm' && vllm && !vllm.configured && (
        <p className="muted small">vLLM 주소가 없어요 — <code>.env</code> 에 <code>MIA_VLLM_URL</code> 을 넣고 다시 시작하세요.</p>
      )}
      <p className="muted small">정하는 순서: 이 에이전트 경로 → 토론 설정의 모델 → 기능 경로 → 기본 경로 (Settings &gt; LLM)</p>
      {msg && <p className="small">{msg}</p>}
    </div>
  )
}

function Tools({ a }: { a: AgentSpec }) {
  const [open, setOpen] = useState<string | null>(null)
  if (!a.tools.length) return <p className="muted">도구 없이 글만 읽고 답해요.</p>
  return (
    <table className="data-table tool-table">
      <thead><tr><th>도구</th><th>하는 일</th><th>자료</th><th>쓰기</th></tr></thead>
      <tbody>
        {a.tools.map((t) => (
          <tr key={t.name} onClick={() => setOpen(open === t.name ? null : t.name)} className="row-open">
            <td><code>{t.name}</code><div className="muted small">{t.title}</div></td>
            <td>{t.description}{open === t.name && <pre className="prompt">{JSON.stringify(t.schema, null, 2)}</pre>}</td>
            <td><span className="chip">{SOURCE_LABEL[t.source] ?? t.source}</span></td>
            <td>{t.read_only ? <span className="muted">읽기</span> : <span className="error">쓰기</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Links({ a, all }: { a: AgentSpec; all: AgentSpec[] }) {
  const [, setParams] = useSearchParams()
  const known = new Set(all.map((x) => x.key))
  const name = (k: string) => ({ admin: '관리자', gate: '게이트(코드)', trading: '주문 검사(코드)', debate: '토론 에이전트들', desk: '리서치 데스크' } as Record<string, string>)[k] ?? agentLabel(k)
  const chip = (k: string, why: string) => (
    <li key={k + why}>
      {known.has(k) ? <button className="link" onClick={() => setParams({ key: k })}>{name(k)}</button> : <b>{name(k)}</b>}
      <span className="muted small"> — {why}</span>
    </li>
  )
  return (
    <div className="links-grid">
      <div><h4>말하는 상대 · 넘기는 곳</h4><ul>{a.talks_to.map((l) => chip(l.to ?? '', l.why))}</ul></div>
      <div><h4>듣는 곳 · 받는 것</h4><ul>{a.hears_from.map((l) => chip(l.from ?? '', l.why))}</ul></div>
      <p className="small"><Link to="/observe/graph">구조 그래프에서 보기 →</Link></p>
    </div>
  )
}
