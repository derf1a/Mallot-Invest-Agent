/**
 * Traces — 실제 호출 추적 (LangSmith 처럼). 왼쪽 트레이스 목록(실행 · 비서 답 하나), 오른쪽 span 트리 + 시간 막대(워터폴),
 * span 을 누르면 입력 · 출력 · 속성(토큰 · 요금 · 경로). 진행 중인 트레이스는 3초마다 다시 읽는다.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useLoad } from '../../shared/useLoad'
import { observeApi, type Span, type TraceDetail } from './api'
import { agentLabel, fmtDur, fmtTime, fmtUsd, SPAN_KIND_LABEL } from './format'

export default function TracesPage() {
  const [params, setParams] = useSearchParams()
  const agent = params.get('agent') ?? ''
  const { data: traces, error, reload } = useLoad(() => observeApi.traces({ limit: 100, agent: agent || undefined }), [agent])
  const { data: ls } = useLoad(() => observeApi.langsmith(), [])
  const id = params.get('id') ?? traces?.[0]?.trace_id ?? null
  const set = (next: Record<string, string>) => setParams({ ...(agent ? { agent } : {}), ...next })
  return (
    <section className="page observe-page">
      <div className="page-header">
        <h1>Traces</h1>
        <span className="muted small">
          {ls?.active ? `LangSmith '${ls.project}' 로 내보내는 중 (${ls.exported}건)` : 'LangSmith 내보내기 꺼짐 — 기록은 이 컴퓨터의 DB 에만'}
        </span>
        <button className="small" onClick={reload}>새로고침</button>
      </div>
      {error && <p className="error">{error}</p>}
      {agent && <p className="small">에이전트 <b>{agentLabel(agent)}</b> 가 나온 트레이스만 · <button className="link" onClick={() => setParams({})}>모두 보기</button></p>}
      <div className="trace-split">
        <div className="card trace-list">
          {traces?.length === 0 && <p className="muted">아직 기록이 없어요. AI Trading 에서 토론을 돌리거나 비서에게 말을 걸어 보세요.</p>}
          <ul>
            {(traces ?? []).map((t) => (
              <li key={t.trace_id}>
                <button className={t.trace_id === id ? 'on' : ''} onClick={() => set({ id: t.trace_id })}>
                  <span className="trace-row-top">
                    <span className={`status-dot ${t.summary.running ? 'running' : t.summary.errors ? 'error' : t.status}`} />
                    <b>{t.name}</b>
                    <span className="muted small">{fmtDur(t.duration_ms)}</span>
                  </span>
                  <span className="muted small">
                    {fmtTime(t.start_ms)} · LLM {t.summary.llm_calls} · 도구 {t.summary.tool_calls} ·
                    {' '}{(t.summary.input_tokens + t.summary.output_tokens).toLocaleString()} 토큰 · {fmtUsd(t.summary.cost_usd)}
                  </span>
                  {t.summary.agents.length > 0 && <span className="small trace-agents">{t.summary.agents.map(agentLabel).join(' · ')}</span>}
                </button>
              </li>
            ))}
          </ul>
        </div>
        {id ? <TraceView key={id} id={id} /> : <div className="card muted">트레이스를 고르세요.</div>}
      </div>
    </section>
  )
}

interface Row { span: Span; depth: number }

function flatten(spans: Span[]): Row[] {
  const kids = new Map<string | null, Span[]>()
  for (const s of spans) kids.set(s.parent_id, [...(kids.get(s.parent_id) ?? []), s])
  const ids = new Set(spans.map((s) => s.id))
  const out: Row[] = []
  const walk = (s: Span, depth: number) => {
    out.push({ span: s, depth })
    for (const c of kids.get(s.id) ?? []) walk(c, depth + 1)
  }
  for (const s of spans) if (!s.parent_id || !ids.has(s.parent_id)) walk(s, 0)
  return out
}

function TraceView({ id }: { id: string }) {
  const navigate = useNavigate()
  const [detail, setDetail] = useState<TraceDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sel, setSel] = useState<string | null>(null)
  const [hide, setHide] = useState<Set<string>>(new Set())
  const [now, setNow] = useState(0)  // 진행 중인 span 의 막대 끝 — 읽어 온 시각

  useEffect(() => {
    let alive = true
    let timer: number | undefined
    const tick = () => observeApi.trace(id).then((d) => {
      if (!alive) return
      setDetail(d)
      setNow(Date.now())
      if (d.summary.running) timer = window.setTimeout(tick, 3000)
    }).catch((e: Error) => alive && setError(e.message))
    tick()
    return () => { alive = false; window.clearTimeout(timer) }
  }, [id])

  const rows = useMemo(() => (detail ? flatten(detail.spans) : []), [detail])
  if (error) return <div className="card error">{error}</div>
  if (!detail) return <div className="card muted">불러오는 중…</div>
  const t0 = detail.root.start_ms
  const end = Math.max(...detail.spans.map((s) => s.end_ms ?? now), t0 + 1)
  const total = end - t0
  const shown = rows.filter((r) => !hide.has(r.span.kind))
  const span = detail.spans.find((s) => s.id === sel) ?? null
  const s = detail.summary
  return (
    <div className="trace-detail">
      <div className="card">
        <div className="trace-head">
          <h3>{detail.root.name}</h3>
          <span className="muted small">{fmtTime(t0)} · {fmtDur(detail.root.duration_ms)} · span {s.spans} · LLM {s.llm_calls} · 도구 {s.tool_calls}
            {s.errors ? ` · 실패 ${s.errors}` : ''} · 입력 {s.input_tokens.toLocaleString()} / 출력 {s.output_tokens.toLocaleString()} 토큰 · {fmtUsd(s.cost_usd)}</span>
          <span className="trace-actions">
            <button className="small" onClick={() => navigate(`/observe/graph?flow=observed&trace=${id}`)}>호출 그래프</button>
            {detail.root.run_id && <button className="small" onClick={() => navigate('/invest/ai')}>실행 #{detail.root.run_id}</button>}
          </span>
        </div>
        <div className="segmented small-seg">
          {(['step', 'agent', 'llm', 'tool', 'event'] as const).map((k) => (
            <button key={k} className={hide.has(k) ? '' : 'on'}
              onClick={() => setHide((h) => { const n = new Set(h); if (n.has(k)) n.delete(k); else n.add(k); return n })}>
              {SPAN_KIND_LABEL[k]}
            </button>
          ))}
        </div>
        <div className="waterfall" role="list">
          {shown.map(({ span: sp, depth }) => {
            const left = ((sp.start_ms - t0) / total) * 100
            const width = Math.max((((sp.end_ms ?? now) - sp.start_ms) / total) * 100, 0.4)
            return (
              <button key={sp.id} role="listitem" className={`wf-row${sel === sp.id ? ' sel' : ''}`} onClick={() => setSel(sp.id)}>
                <span className="wf-name" style={{ paddingLeft: depth * 14 }}>
                  <span className={`kind-tag kind-${sp.kind}`}>{SPAN_KIND_LABEL[sp.kind]}</span>
                  <span className="wf-label">{sp.kind === 'agent' ? agentLabel(sp.name) : sp.name}</span>
                  {sp.kind === 'llm' && <span className="muted small"> {String(sp.attrs.route ?? '')}</span>}
                  {sp.status === 'error' && <span className="error small"> 실패</span>}
                </span>
                <span className="wf-track">
                  <span className={`wf-bar kind-${sp.kind}${sp.status === 'running' ? ' running' : ''}`} style={{ left: `${left}%`, width: `${width}%` }} />
                </span>
                <span className="wf-dur muted small">{sp.kind === 'event' ? '' : fmtDur(sp.duration_ms)}</span>
              </button>
            )
          })}
        </div>
      </div>
      {span && <SpanPanel span={span} />}
    </div>
  )
}

function SpanPanel({ span }: { span: Span }) {
  const a = span.attrs
  const facts: [string, unknown][] = [
    ['종류', SPAN_KIND_LABEL[span.kind]], ['에이전트', agentLabel(span.agent)], ['상태', span.status], ['걸린 시간', fmtDur(span.duration_ms)],
    ['경로', a.route], ['어디서 정해졌나', a.route_source], ['공급자', a.provider], ['모델', a.model], ['입력 토큰', a.input_tokens],
    ['캐시 토큰', a.cache_read_tokens], ['출력 토큰', a.output_tokens], ['턴', a.turns], ['요금 환산', a.cost_usd ? fmtUsd(Number(a.cost_usd)) : null],
  ]
  return (
    <div className="card span-panel">
      <h3>{span.kind === 'agent' ? agentLabel(span.name) : span.name}</h3>
      <dl className="span-facts">
        {facts.filter(([, v]) => v !== undefined && v !== null && v !== '').map(([k, v]) => (
          <div key={k}><dt>{k}</dt><dd>{String(v)}</dd></div>
        ))}
      </dl>
      {span.error && <p className="error small">{span.error}</p>}
      <Json title="입력" value={span.input} />
      <Json title="출력" value={span.output} />
    </div>
  )
}

function Json({ title, value }: { title: string; value: unknown }) {
  if (value === null || value === undefined) return null
  const obj = value as Record<string, unknown>
  // LLM 입력은 시스템 프롬프트 · 프롬프트를 글 그대로 보여 준다 (JSON 따옴표 없이)
  if (title === '입력' && typeof obj === 'object' && obj && 'prompt' in obj) {
    return (
      <details open>
        <summary>{title}</summary>
        {Array.isArray(obj.tools) && <p className="small">도구: {(obj.tools as string[]).join(', ') || '없음'}</p>}
        {obj.system ? <><h4>시스템 프롬프트</h4><pre className="prompt">{String(obj.system)}</pre></> : null}
        <h4>프롬프트</h4><pre className="prompt">{String(obj.prompt)}</pre>
      </details>
    )
  }
  return (
    <details open>
      <summary>{title}</summary>
      <pre className="prompt">{JSON.stringify(value, null, 2)}</pre>
    </details>
  )
}
