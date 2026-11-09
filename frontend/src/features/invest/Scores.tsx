import { Fragment, useState } from 'react'
import { useLoad } from '../../shared/useLoad'
import { investApi, type ScoreKind, type ScoreRow } from './api'

const KINDS: { key: ScoreKind | ''; label: string }[] = [
  { key: '', label: '모든 판단' },
  { key: 'ideas', label: '종목 발굴' },
  { key: 'meeting', label: '회의 후보' },
  { key: 'value', label: '가치 스크린' },
  { key: 'sector', label: '섹터 평가' },
]
const REGIME: Record<string, string> = { risk_on: '위험 선호', neutral: '중립', risk_off: '위험 회피' }
const DECISION: Record<string, string> = { picked: '선정', passed: '지나침', excluded: '제외', overweight: '확대', underweight: '축소' }
const GROUP_ORDER = ['trend', 'pullback', 'value', 'risk', 'source']
const PCT_KEYS = new Set(['upside', 'roe', 'op_margin', 'rev_growth', 'op_growth', 'debt_ratio', 'current_ratio', 'm20', 'm5', 'chase', 'pullback'])

const num = (v: number | null | undefined, digits = 2) => (v === null || v === undefined ? '-' : Number(v).toFixed(digits))
const shown = (key: string, v: number | null) => (v === null ? '-' : PCT_KEYS.has(key) ? `${(v * 100).toFixed(1)}%` : num(v))

/** 줄 하나의 근거 — 그룹별 점수, 항목별 점수, 가치평가 기둥·항목. 기록은 오래 남으니 옛 줄에 없는 필드는 없는 대로 그린다 */
function Detail({ row }: { row: ScoreRow }) {
  const { factors, valuation: v, rates } = row.detail
  return (
    <div className="score-detail">
      {factors && (
        <div>
          <h4>그룹 (가중치 적용)</h4>
          <ul>
            {GROUP_ORDER.filter((k) => factors.groups[k]).map((k) => (
              <li key={k}><span>{factors.groups[k].label} {factors.groups[k].raw} × {factors.groups[k].weight}</span><b>{factors.groups[k].points}</b></li>
            ))}
          </ul>
        </div>
      )}
      {factors && (
        <div>
          <h4>항목</h4>
          <ul>
            {factors.items.map((x) => (
              <li key={x.key}><span>{x.label}{x.input !== null ? ` (${shown(x.key, x.input)})` : ''}</span><b>{x.points}</b></li>
            ))}
          </ul>
        </div>
      )}
      {v && (
        <div>
          <h4>가치 {v.score ?? '-'}점 {v.grade ?? ''} · {v.basis.report ?? ''}</h4>
          <ul>
            {Object.entries(v.pillars).map(([k, p]) => (
              <li key={k}><span>{p.label} (가중치 {p.weight})</span><b>{p.score}</b></li>
            ))}
          </ul>
        </div>
      )}
      {v && (
        <div>
          <h4>가치 항목 · 근거 {v.basis.rcept_no ?? ''}</h4>
          <ul>
            {v.items.map((x) => (
              <li key={x.key}><span>{x.label} {x.note || shown(x.key, x.value)}</span><b>{x.points}</b></li>
            ))}
          </ul>
          {!!v.missing_labels?.length && <p className="muted small">평가 못 한 항목: {v.missing_labels.join(', ')}</p>}
        </div>
      )}
      {rates && (
        <div>
          <h4>기간 등락</h4>
          <ul>
            {Object.entries(rates).map(([k, r]) => <li key={k}><span>{k}</span><b>{(r * 100).toFixed(1)}%</b></li>)}
          </ul>
        </div>
      )}
    </div>
  )
}

/** 판단 기록 — 무엇을 어떤 기준으로 몇 점을 줘서 골랐는지 (고르지 않은 종목도 남는다) */
export default function Scores() {
  const [kind, setKind] = useState<ScoreKind | ''>('')
  const [selected, setSelected] = useState<number | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  const { data: status } = useLoad(() => investApi.valuationStatus().catch(() => null), [])
  const { data: runs, error } = useLoad(() => investApi.scoreRuns(kind || undefined), [kind])
  const { data: run } = useLoad(() => (selected ? investApi.scoreRun(selected) : Promise.resolve(null)), [selected])
  const { data: rules } = useLoad(() => (run ? investApi.scoreRules(run.rules_hash) : Promise.resolve(null)), [run?.rules_hash])

  return (
    <>
      <article className="card">
        <h2>가치평가 재무 (OpenDART)</h2>
        {!status ? (
          <p className="muted small">상태를 받지 못했어요 — 서버를 새 버전으로 다시 시작하면 보여요.</p>
        ) : (
          <>
            <p className="small">
              {status.configured ? '인증키 있음' : '인증키 없음 — 설정 > 투자에서 입력하면 다음 리서치 때 재무를 받아요'}
              {' · '}종목 {status.symbols.toLocaleString()}개 · 보고서 {status.reports.toLocaleString()}건
              {status.last_run ? ` · 마지막 ${status.last_run}` : ''}
            </p>
            {status.last_error && <p className="error small">{status.last_error}</p>}
          </>
        )}
      </article>

      <article className="card">
        <div className="filter-row score-filter">
          <select value={kind} onChange={(e) => { setKind(e.target.value as ScoreKind | ''); setSelected(null) }} aria-label="판단 종류">
            {KINDS.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
          </select>
          <a className="link-button small" href={`/api/invest/score-runs/export${kind ? `?kind=${kind}` : ''}`}>JSONL 내려받기</a>
        </div>
        {error && <p className="muted small">기록을 받지 못했어요 — 서버를 새 버전으로 다시 시작하면 보여요.</p>}
        {!error && !runs?.length && <p className="muted small">아직 기록이 없어요. 리서치나 회의가 돌면 쌓여요.</p>}
        {!!runs?.length && (
          <div className="table-wrap">
            <table className="data-table score-rows">
              <thead>
                <tr><th>시각</th><th>종류</th><th>국면</th><th>훑은 수</th><th>고른 수</th><th>고른 쪽</th><th>기준</th></tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id} role="button" tabIndex={0} className={selected === r.id ? 'best' : ''}
                    onClick={() => { setSelected(r.id); setOpen(null) }}
                    onKeyDown={(e) => { if (e.key === 'Enter') { setSelected(r.id); setOpen(null) } }}>
                    <td>{r.created_at.slice(5, 16)}</td>
                    <td>{r.kind_label}</td>
                    <td>{REGIME[r.regime] ?? r.regime}</td>
                    <td>{r.pool}</td>
                    <td>{r.picked}</td>
                    <td>{r.picked_by === 'llm' ? 'LLM' : '코드'}</td>
                    <td className="muted">{r.rules_hash.slice(0, 8)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </article>

      {run && (
        <article className="card">
          <h2>{run.kind_label} · {run.created_at.slice(0, 16)}</h2>
          {run.summary && <p className="small">{run.summary}</p>}
          <div className="table-wrap">
            <table className="data-table score-rows">
              <thead>
                <tr><th>#</th><th>{run.kind === 'sector' ? '섹터' : '종목'}</th><th>점수</th><th>가치</th><th>가격</th><th>결정</th><th>이유</th></tr>
              </thead>
              <tbody>
                {run.rows.map((r) => (
                  <Fragment key={r.id}>
                    <tr role="button" tabIndex={0} className={open === r.id ? 'row-open' : ''} aria-expanded={open === r.id}
                      onClick={() => setOpen(open === r.id ? null : r.id)}
                      onKeyDown={(e) => { if (e.key === 'Enter') setOpen(open === r.id ? null : r.id) }}>
                      <td>{r.rank}</td>
                      <td>{r.name || r.subject}</td>
                      <td><b>{run.kind === 'sector' && r.score !== null ? `${(r.score * 100).toFixed(1)}%` : num(r.score)}</b></td>
                      <td>{num(r.value_score, 0)}</td>
                      <td>{r.price ? r.price.toLocaleString() : '-'}</td>
                      <td>{DECISION[r.decision] ?? r.decision}</td>
                      <td className="score-why muted" title={r.reason}>{r.reason}</td>
                    </tr>
                    {open === r.id && <tr><td colSpan={7}><Detail row={r} /></td></tr>}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          {run.notes.length > 0 && <p className="muted small">{run.notes.join(' · ')}</p>}
          {rules && (
            <details className="small">
              <summary className="muted">이 판단의 기준표 ({rules.hash.slice(0, 8)})</summary>
              <pre className="score-rules">{JSON.stringify(rules.rules, null, 2)}</pre>
            </details>
          )}
        </article>
      )}
    </>
  )
}
