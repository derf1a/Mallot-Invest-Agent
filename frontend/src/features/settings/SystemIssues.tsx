/**
 * Settings > 시스템 이슈 — 에이전트들이 알린 시스템 문제를 시스템 관리자(마테우스)가 이슈로 묶거나 답한 기록.
 * 10분마다 스스로 정리하고, GitHub 토큰(.env 의 MIA_GITHUB_TOKEN)이 있으면 이슈를 올리고 닫혔는지 읽어 온다. '지금 정리'로 바로 돌릴 수 있다.
 */
import { useState } from 'react'
import { useReadOnly } from '../../shared/readOnly'
import { useLoad } from '../../shared/useLoad'
import { systemIssuesApi, type SystemIssue, type SystemIssueDetail, type TriageResult } from './systemApi'

type Filter = 'open' | 'resolved' | 'all'
const FILTERS: { key: Filter; label: string }[] = [{ key: 'open', label: '열림' }, { key: 'resolved', label: '해결' }, { key: 'all', label: '전체' }]
const EVENT_KO: Record<string, string> = { opened: '등록', published: 'GitHub', reported: '요청 연결', resolved: '해결', reopened: '다시 열림', imported: '가져옴' }

function triageNote(r: TriageResult['result']): string {
  const t = r.triage
  const parts = t.error ? [t.error] : t.pending === 0 ? ['대기 요청 없음'] : [
    `요청 ${t.pending}건 → 새 이슈 ${t.issue ?? 0} · 기존 이슈에 연결 ${t.link ?? 0} · 답 ${t.answer ?? 0} · 버림 ${t.ignore ?? 0}`]
  if (r.publish.github) parts.push(r.publish.error ?? `GitHub 올림 ${r.publish.published}건`)
  if (r.sync?.github) parts.push(r.sync.error ?? `GitHub 맞춤 ${r.sync.synced}건${r.sync.changed ? ` (바뀜 ${r.sync.changed})` : ''}`)
  return parts.join(' · ')
}

export default function SystemIssues() {
  const readOnly = useReadOnly()
  const [filter, setFilter] = useState<Filter>('open')
  const [version, setVersion] = useState(0)
  const { data, error } = useLoad(() => systemIssuesApi.overview(filter === 'all' ? undefined : filter), [filter, version])
  const [open, setOpen] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  if (!data) return error ? <p className="error">{error}</p> : <p className="muted">불러오는 중…</p>
  const triage = async () => {
    setBusy(true)
    setNote(null)
    try {
      const out = await systemIssuesApi.triage()
      setNote(triageNote(out.result))
      setVersion((v) => v + 1)
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  const { counts, github } = data
  return (
    <div className="card system-issues">
      <div className="section-head">
        <h3>시스템 이슈</h3>
        <span className="muted small">열림 {counts.open} · 해결 {counts.resolved} · 대기 요청 {counts.pending}</span>
        <button className="primary" disabled={busy || readOnly} onClick={triage}>{busy ? '정리하는 중…' : '지금 정리'}</button>
      </div>
      <p className="muted small">
        {data.admin.name}({data.admin.title})가 에이전트들이 알린 도구 오류 · 데이터 불일치를 이슈로 묶거나 답해요. 10분마다 스스로 정리해요.{' '}
        {github.configured
          ? <>GitHub <b>{github.repo}</b> 에 올리고, 거기서 닫히면 해결로 바뀌어요.</>
          : <>GitHub 에는 올리지 않아요 — <code>.env</code> 에 <code>{github.token_env}</code> 를 넣으면 <b>{github.repo}</b> 에 올려요.</>}
      </p>
      {note && <p className="small">{note}</p>}

      {data.pending.length > 0 && (
        <details>
          <summary className="small">대기 요청 {data.pending.length}건</summary>
          <ul className="issue-requests">
            {data.pending.map((r) => (
              <li key={r.id}><span className="muted">{r.at.slice(5, 16)} · {r.by}{r.seen > 1 ? ` · ${r.seen}번` : ''}</span>{r.text}</li>
            ))}
          </ul>
        </details>
      )}

      <div className="issue-filters" role="group" aria-label="이슈 상태">
        {FILTERS.map((f) => (
          <button key={f.key} className={`chip-toggle${filter === f.key ? ' on' : ''}`} aria-pressed={filter === f.key}
            onClick={() => { setFilter(f.key); setOpen(null) }}>{f.label}</button>
        ))}
      </div>
      {data.issues.length === 0
        ? <p className="muted small">{filter === 'resolved' ? '해결한 이슈가 없어요' : '열린 이슈가 없어요'}</p>
        : (
          <ul className="issue-list">
            {data.issues.map((x) => (
              <IssueRow key={x.id} x={x} open={open === x.id} readOnly={readOnly}
                onToggle={() => setOpen(open === x.id ? null : x.id)} onChanged={() => setVersion((v) => v + 1)} />
            ))}
          </ul>
        )}

      {data.answers.length > 0 && (
        <section>
          <h4 className="small muted">이슈로 만들지 않고 답한 요청</h4>
          <ul className="issue-answers">
            {data.answers.map((a) => (
              <li key={a.id} className="small">
                <span className="muted">{a.at.slice(5, 16)} · {a.by}</span>
                <span>{a.text}</span>
                <span>→ {a.answer || '(버림)'}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

function IssueRow({ x, open, readOnly, onToggle, onChanged }: {
  x: SystemIssue; open: boolean; readOnly: boolean; onToggle: () => void; onChanged: () => void
}) {
  return (
    <li className="issue-row">
      <button className="issue-head" aria-expanded={open} onClick={onToggle}>
        <span className={`chip issue-state ${x.status}`}>{x.status === 'open' ? '열림' : '해결'}</span>
        <span className="issue-title">{x.number ? `#${x.number} ` : ''}{x.title}</span>
        <span className="issue-meta muted small">{x.updated_at.slice(5, 16)} · {x.by} · 요청 {x.reports}건</span>
      </button>
      {x.url && <a className="small" href={x.url} target="_blank" rel="noreferrer">GitHub ↗</a>}
      {open && <IssueDetail id={x.id} readOnly={readOnly} onChanged={onChanged} />}
    </li>
  )
}

function IssueDetail({ id, readOnly, onChanged }: { id: number; readOnly: boolean; onChanged: () => void }) {
  const [saved, setSaved] = useState<SystemIssueDetail | null>(null)
  const { data } = useLoad(() => systemIssuesApi.detail(id), [id])
  const d = saved ?? data
  if (!d) return <p className="muted small issue-detail">불러오는 중…</p>
  const flip = async () => {
    setSaved(await (d.status === 'open' ? systemIssuesApi.resolve(id) : systemIssuesApi.reopen(id)))
    onChanged()
  }
  return (
    <div className="issue-detail">
      <p className="issue-body">{d.body || '(본문 없음)'}</p>
      {d.resolution && <p className="small">해결: {d.resolution}</p>}
      <h4>이어진 요청</h4>
      <ul className="issue-requests">
        {d.requests.map((r, i) => (
          <li key={i}><span className="muted">{r.at.slice(5, 16)} · {r.by}{r.seen > 1 ? ` · ${r.seen}번` : ''}</span>{r.text}</li>
        ))}
      </ul>
      <h4>기록</h4>
      <ul className="issue-events small">
        {d.events.map((e) => (
          <li key={e.id}><span className="muted">{e.at.slice(5, 16)}</span><b>{EVENT_KO[e.kind] ?? e.kind}</b><span>{e.text}</span></li>
        ))}
      </ul>
      {!readOnly && <div><button onClick={flip}>{d.status === 'open' ? '해결로 닫기' : '다시 열기'}</button></div>}
    </div>
  )
}
