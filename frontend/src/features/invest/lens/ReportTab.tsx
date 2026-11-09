import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { isActive, STATUS_LABEL, useRunLive } from '../../runs/live'
import { useLoad } from '../../../shared/useLoad'
import { ApiError, investApi, type NewsReport, type Sentiment, type StockSnapshot } from '../api'
import { MODELS } from '../format'

const SCOPES = [
  { key: 'all', label: '보유·관심·시장' },
  { key: 'holdings', label: '보유 종목' },
  { key: 'watchlist', label: '관심 종목' },
  { key: 'market', label: '시장 전체' },
  { key: 'symbols', label: '이 종목' },
] as const
const SENT: Record<Sentiment, string> = { positive: '긍정', neutral: '중립', negative: '부정', mixed: '혼재' }

/** AI 리포트 — 실험실 작업(news_report)으로 뉴스를 분석·요약해 보고서를 만든다 */
export default function ReportTab({ snap }: { snap: StockSnapshot | null }) {
  const [scope, setScope] = useState<(typeof SCOPES)[number]['key']>('all')
  const [model, setModel] = useState('')
  const [selected, setSelected] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)
  const { data: runs } = useLoad(() => investApi.reports(), [version])
  const bump = useCallback(() => setVersion((v) => v + 1), [])
  const current = selected ?? runs?.[0]?.id ?? null

  const create = async () => {
    setError(null)
    try {
      const run = await investApi.createReport({ scope, symbols: scope === 'symbols' && snap ? [snap.symbol] : [], model })
      setSelected(run.id)
      setVersion((v) => v + 1)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    }
  }

  return (
    <div className="report-tab">
      <div className="toolbar">
        <select value={scope} onChange={(e) => setScope(e.target.value as typeof scope)} aria-label="리포트 범위">
          {SCOPES.map((s) => (
            <option key={s.key} value={s.key}>
              {s.key === 'symbols' && snap ? `이 종목 (${snap.name})` : s.label}
            </option>
          ))}
        </select>
        <select value={model} onChange={(e) => setModel(e.target.value)} aria-label="모델">
          {MODELS.map((m) => (
            <option key={m.key} value={m.key}>
              {m.label}
            </option>
          ))}
        </select>
        <button className="primary" onClick={create}>
          리포트 만들기
        </button>
      </div>
      <p className="muted small">뉴스를 모아 종목별로 분석하고 하나의 보고서로 묶어요. 근거 기사 번호를 코드가 검증해요. 매수·매도 권유가 아니에요.</p>
      {error && <p className="error small">{error}</p>}
      {runs && runs.length > 0 && (
        <div className="report-list">
          {runs.slice(0, 8).map((r) => (
            <button key={r.id} className={`report-pill${r.id === current ? ' on' : ''}`} onClick={() => setSelected(r.id)}>
              <span>{(r.summary?.title as string) ?? r.name}</span>
              <span className={`chip run-${r.status}`}>{STATUS_LABEL[r.status]}</span>
              <span className="muted small">{r.created_at.slice(5, 16)}</span>
            </button>
          ))}
        </div>
      )}
      {current !== null && <ReportView key={current} id={current} onDone={bump} />}
    </div>
  )
}

function ReportView({ id, onDone }: { id: number; onDone: () => void }) {
  const { run, logs } = useRunLive(id)
  const finished = run && !isActive(run.status)
  const { data } = useLoad(() => (finished ? investApi.report(id) : Promise.resolve(null)), [id, finished])
  useEffect(() => {
    if (finished) onDone() // 목록의 상태·제목을 새로 고침
  }, [finished, onDone])
  if (!run) return <p className="muted small">불러오는 중…</p>
  if (isActive(run.status)) {
    return (
      <div className="card inner">
        <div className="run-progress">
          <span className="chip run-running">{STATUS_LABEL[run.status]}</span>
          <div className="progress-bar">
            <i style={{ width: `${Math.round(run.progress * 100)}%` }} />
          </div>
          <span className="muted small">{Math.round(run.progress * 100)}%</span>
        </div>
        <ul className="log-lines small">
          {logs.slice(-6).map((l) => (
            <li key={l.id} className={l.level}>
              {l.message}
            </li>
          ))}
        </ul>
      </div>
    )
  }
  if (run.status !== 'succeeded') return <p className="error small">리포트 실패: {run.error}</p>
  const rep = data?.report
  return rep ? <Report rep={rep} runId={id} /> : <p className="muted small">보고서를 불러오는 중…</p>
}

function Report({ rep, runId }: { rep: NewsReport; runId: number }) {
  const byId = new Map(rep.news.map((n) => [n.id, n]))
  return (
    <article className="report">
      <header>
        <h3>{rep.title}</h3>
        {rep.headline && <p className="report-headline">{rep.headline}</p>}
        <p className="muted small">
          {rep.scope_label} · 기사 {rep.news.length}건{rep.provider === 'sample' ? ' · 시세는 샘플 데이터' : ''}
          {rep.citations_dropped > 0 && ` · 잘못된 인용 ${rep.citations_dropped}개 제거`} ·{' '}
          <Link to={`/lab?tab=overview&run=${runId}`}>실험실 실행 #{runId}</Link>
        </p>
      </header>
      {rep.market_summary && (
        <section>
          <h4>시장</h4>
          <p>{rep.market_summary}</p>
        </section>
      )}
      {rep.portfolio_view && (
        <section>
          <h4>포트폴리오 관점</h4>
          <p>{rep.portfolio_view}</p>
        </section>
      )}
      {rep.sections.map((s) => (
        <section key={s.label} className="report-section">
          <div className="report-section-head">
            <h4>{s.label}</h4>
            <span className={`sent ${s.analysis.sentiment}`}>{SENT[s.analysis.sentiment]}</span>
            <span className="sent-meter" aria-label={`감성 점수 ${s.analysis.sentiment_score.toFixed(2)}`}>
              <i style={{ left: `${((s.analysis.sentiment_score + 1) / 2) * 100}%` }} />
            </span>
            <span className="muted small">{s.analysis.sentiment_score > 0 ? '+' : ''}{s.analysis.sentiment_score.toFixed(2)}</span>
          </div>
          <p>{s.analysis.summary}</p>
          <ul>
            {s.analysis.key_points.map((p, k) => (
              <li key={k}>
                {p.point}
                {p.news_ids.map((nid) =>
                  byId.get(nid) ? (
                    <a key={nid} className="cite" href={byId.get(nid)!.url} target="_blank" rel="noopener noreferrer"
                      title={`${byId.get(nid)!.title} — ${byId.get(nid)!.source}`}>
                      [{nid}]
                    </a>
                  ) : null,
                )}
              </li>
            ))}
          </ul>
          {(s.analysis.risks.length > 0 || s.analysis.catalysts.length > 0) && (
            <div className="report-tags small">
              {s.analysis.catalysts.map((c) => (
                <span key={c} className="chip catalyst">촉매 · {c}</span>
              ))}
              {s.analysis.risks.map((r) => (
                <span key={r} className="chip risk">위험 · {r}</span>
              ))}
            </div>
          )}
          {s.analysis.information_gaps.length > 0 && <p className="muted small">자료 부족: {s.analysis.information_gaps.join(' · ')}</p>}
        </section>
      ))}
      {rep.action_items.length > 0 && (
        <section>
          <h4>점검할 것</h4>
          <ul>
            {rep.action_items.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </section>
      )}
      <details>
        <summary className="muted small">근거 기사 {rep.news.length}건</summary>
        <ol className="news-cites small">
          {rep.news.map((n) => (
            <li key={n.id}>
              [{n.id}]{' '}
              <a href={n.url} target="_blank" rel="noopener noreferrer">
                {n.title}
              </a>{' '}
              <span className="muted">— {n.source} {n.published_at ?? ''}</span>
            </li>
          ))}
        </ol>
      </details>
    </article>
  )
}
