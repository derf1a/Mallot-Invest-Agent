/**
 * Graph — 멀티 에이전트 구조 그래프. 흐름(투자 회의 · 상시 운용 · 운용 데스크 · 비서)마다 설계 그래프를 그리고,
 * '실제 호출' 탭은 트레이스 하나에서 실제로 누가 무엇을 몇 번 불렀는지(관찰 그래프)를 그린다.
 * 흐름마다 mermaid 글과 LangGraph 로 컴파일한 그래프도 볼 수 있다.
 */
import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useLoad } from '../../shared/useLoad'
import { githubHref, observeApi, type Flow, type ObservedGraph } from './api'
import CodeBlock from './CodeBlock'
import GraphView, { Legend, type GEdge, type GNode } from './GraphView'
import { agentLabel, fmtTime } from './format'

const OBSERVED = 'observed'

export default function GraphPage() {
  const [params, setParams] = useSearchParams()
  const { data: flows, error } = useLoad(() => observeApi.flows(), [])
  const tab = params.get('flow') ?? 'meeting'
  return (
    <section className="page observe-page">
      <div className="page-header">
        <h1>Graph</h1>
        <span className="muted small">설계 그래프는 지금 코드 · 명단으로, 실제 호출은 트레이스로 그린다</span>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="tabs" role="tablist">
        {(flows ?? []).map((f) => (
          <button key={f.key} role="tab" aria-selected={tab === f.key} className={tab === f.key ? 'on' : ''}
            onClick={() => setParams({ flow: f.key })}>{f.title}</button>
        ))}
        <button role="tab" aria-selected={tab === OBSERVED} className={tab === OBSERVED ? 'on' : ''}
          onClick={() => setParams({ flow: OBSERVED })}>실제 호출 (트레이스)</button>
      </div>
      {tab === OBSERVED ? <Observed traceId={params.get('trace')} onTrace={(t) => setParams({ flow: OBSERVED, trace: t })} />
        : <FlowView key={tab} flowKey={tab} />}
    </section>
  )
}

function FlowView({ flowKey }: { flowKey: string }) {
  const navigate = useNavigate()
  const { data: flow, error } = useLoad(() => observeApi.flow(flowKey), [flowKey])
  const [sel, setSel] = useState<string | null>(null)
  const [show, setShow] = useState<'' | 'mermaid' | 'langgraph'>('')
  if (error) return <p className="error">{error}</p>
  if (!flow) return <p className="muted">불러오는 중…</p>
  const node = flow.nodes.find((n) => n.id === sel) ?? null
  const kinds = [...new Set(flow.nodes.map((n) => n.kind))]
  const edgeKinds = [...new Set(flow.edges.map((e) => e.kind))]
  return (
    <div className="observe-split">
      <div className="card graph-card">
        <p className="muted small">{flow.summary}</p>
        <GraphView nodes={flow.nodes} edges={flow.edges} selected={sel} onNode={(n) => setSel(n.id)} />
        <Legend kinds={kinds} edgeKinds={edgeKinds} />
      </div>
      <aside className="card observe-side">
        {node ? (
          <>
            <h3>{node.label}</h3>
            {node.sub && <p className="muted">{node.sub}</p>}
            {node.agent && <button className="primary" onClick={() => navigate(`/observe/agents?key=${node.agent}`)}>에이전트 설정 보기</button>}
            {node.ref && <p className="small"><a href={githubHref(node.ref)} target="_blank" rel="noreferrer">{node.ref.label} ↗</a>
              <span className="muted"> {node.ref.path}:{node.ref.line}</span></p>}
            <EdgeList flow={flow} id={node.id} />
          </>
        ) : <p className="muted">노드를 누르면 설명 · 이어진 선 · 코드 위치가 보여요.</p>}
        <div className="segmented small-seg">
          <button className={show === 'mermaid' ? 'on' : ''} onClick={() => setShow(show === 'mermaid' ? '' : 'mermaid')}>mermaid</button>
          <button className={show === 'langgraph' ? 'on' : ''} onClick={() => setShow(show === 'langgraph' ? '' : 'langgraph')}>LangGraph</button>
        </div>
        {show === 'mermaid' && <CodeBlock text={flow.mermaid} note="README · GitHub 에 붙이면 그림으로 보여요" />}
        {show === 'langgraph' && <LangGraphBox flowKey={flow.key} />}
      </aside>
    </div>
  )
}

function EdgeList({ flow, id }: { flow: Flow; id: string }) {
  const name = (x: string) => flow.nodes.find((n) => n.id === x)?.label ?? x
  const out = flow.edges.filter((e) => e.source === id)
  const inc = flow.edges.filter((e) => e.target === id)
  return (
    <div className="edge-lists">
      {inc.length > 0 && <><h4>들어오는 선</h4><ul>{inc.map((e, i) => <li key={i}>{name(e.source)} → <span className="muted">{e.label || e.kind}</span></li>)}</ul></>}
      {out.length > 0 && <><h4>나가는 선</h4><ul>{out.map((e, i) => <li key={i}>→ {name(e.target)} <span className="muted">{e.label || e.kind}</span></li>)}</ul></>}
    </div>
  )
}

function LangGraphBox({ flowKey }: { flowKey: string }) {
  const { data, error } = useLoad(() => observeApi.langgraph(flowKey), [flowKey])
  if (error) return <p className="error">{error}</p>
  if (!data) return <p className="muted">컴파일하는 중…</p>
  if (!data.installed) return <p className="muted small">langgraph 가 설치되지 않았어요 — <code>{data.how}</code></p>
  return <CodeBlock text={data.mermaid ?? ''} note={`StateGraph 노드 ${data.nodes?.length ?? 0}개 (START · END 포함) — get_graph().draw_mermaid()`} />
}

// ---------- 관찰 그래프 ----------

const OBS_COL: Record<string, number> = { run: 0, human: 0, agent: 1, event: 2, tool: 2, model: 3 }

function layout(g: ObservedGraph): { nodes: GNode[]; edges: GEdge[] } {
  const rows: Record<number, number> = {}
  const nodes = g.nodes.map((n) => {
    const col = OBS_COL[n.kind] ?? 2
    const row = rows[col] ?? 0
    rows[col] = row + 1
    return { id: n.id, label: n.label, sub: n.kind === 'tool' ? n.source : n.kind, kind: n.kind === 'model' ? 'store' : n.kind === 'tool' ? 'data' : n.kind === 'run' ? 'code' : n.kind === 'event' ? 'code' : n.kind, col, row, agent: n.agent ?? null }
  })
  const edges = g.edges.map((e) => ({
    source: e.source, target: e.target, weight: e.count,
    kind: e.kind === 'llm' || e.kind === 'tool' ? 'data' : e.kind === 'event' ? 'control' : e.kind,
    label: e.kind === 'talk' ? `동의 ${e.agree} · 반박 ${e.disagree}`
      : e.kind === 'llm' ? `${e.count}회 · ${e.tokens.toLocaleString()} 토큰` : `${e.count}회${e.errors ? ` · 실패 ${e.errors}` : ''}`,
  }))
  return { nodes, edges }
}

function Observed({ traceId, onTrace }: { traceId: string | null; onTrace: (id: string) => void }) {
  const navigate = useNavigate()
  const { data: traces } = useLoad(() => observeApi.traces({ limit: 30 }), [])
  const pick = traceId ?? traces?.[0]?.trace_id ?? null
  const { data: g, error } = useLoad(() => (pick ? observeApi.observed(pick) : Promise.resolve(null)), [pick])
  const drawn = useMemo(() => (g ? layout(g) : null), [g])
  return (
    <div className="observe-split">
      <div className="card graph-card">
        {!pick && <p className="muted">아직 트레이스가 없어요. AI Trading 에서 토론을 한 번 돌리면 여기에 그려져요.</p>}
        {error && <p className="error">{error}</p>}
        {g && drawn && (
          <>
            <p className="small">
              <b>{g.root.name}</b> <span className="muted">{fmtTime(g.root.start_ms)} · LLM {g.summary.llm_calls}회 · 도구 {g.summary.tool_calls}회 ·
                토큰 {(g.summary.input_tokens + g.summary.output_tokens).toLocaleString()}</span>
              {' '}<button className="link" onClick={() => navigate(`/observe/traces?id=${g.trace_id}`)}>span 트리 보기</button>
            </p>
            <GraphView nodes={drawn.nodes} edges={drawn.edges}
              onNode={(n) => n.agent && navigate(`/observe/agents?key=${n.agent}`)} />
            <Legend kinds={['code', 'agent', 'human', 'data', 'store']} edgeKinds={['control', 'talk', 'data']} />
            <p className="muted small">외부 자료 칸 = 도구, 저장 칸 = 모델(공급자:모델). 말 선은 발언의 responses[].to 로 센 동의 · 반박.</p>
          </>
        )}
      </div>
      <aside className="card observe-side">
        <h3>트레이스</h3>
        <ul className="trace-pick">
          {(traces ?? []).map((t) => (
            <li key={t.trace_id}>
              <button className={t.trace_id === pick ? 'on' : ''} onClick={() => onTrace(t.trace_id)}>
                <b>{t.name}</b>
                <span className="muted small">{fmtTime(t.start_ms)} · {t.summary.agents.map(agentLabel).join(', ') || '에이전트 없음'}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
    </div>
  )
}
