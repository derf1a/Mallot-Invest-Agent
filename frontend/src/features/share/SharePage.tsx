/**
 * 손님 화면 (공유 링크) — 운용 상황과 보고서만, 읽기만.
 * 자동 운용 상태 · 오늘 일정, 오늘 리서치(시장 · 섹터 · 발굴 · 경보), 지금 보고서, 회의 목록과 회의방 대화, 지난 보고서.
 * 관리자도 이 화면을 열 수 있다 (손님에게 어떻게 보이는지 미리 보기).
 */
import { useEffect, useState } from 'react'
import { useLoad } from '../../shared/useLoad'
import { AgentAvatar } from '../invest/agents/Face'
import ReportCard from '../invest/agents/Report'
import { STANCE_LABEL, won } from '../invest/format'
import { shareApi, type SavedReport, type ShareMessage, type ShareSession, type ShareStatus } from './api'

const KIND_KO: Record<string, string> = { fixed: '정기', extra: 'AI 추가', manual: '지금 판단', risk: '리스크 경보', stream: '상시 운용' }
const STATUS_KO: Record<string, string> = {
  pending: '예정', started: '열림', skipped: '건너뜀', missed: '놓침',
  queued: '대기', running: '진행 중', succeeded: '끝남', failed: '실패', cancelled: '취소',
}
const SIDE_KO = { buy: '매수', sell: '매도' } as const

type Tab = 'status' | 'report' | 'meetings'
const TABS: { key: Tab; label: string; icon: string }[] = [
  { key: 'status', label: '현황', icon: '◎' },
  { key: 'report', label: '보고서', icon: '▤' },
  { key: 'meetings', label: '회의', icon: '☰' },
]

export default function SharePage({ preview = false }: { preview?: boolean }) {
  const { data: status, error, reload } = useLoad(() => shareApi.status(), [])
  const [openId, setOpenId] = useState<number | null>(null)
  // 휴대폰에서는 아래쪽 탭으로 한 칸씩, 넓은 화면에서는 모두 펼쳐 보인다 (CSS 가 data-tab 으로 가린다)
  const [tab, setTab] = useState<Tab>('status')
  useEffect(() => {
    const t = window.setInterval(reload, 30_000)  // 운용 상황은 30초마다
    return () => window.clearInterval(t)
  }, [reload])
  return (
    <section className="page share-page" data-tab={tab}>
      <div className="page-header">
        <h1>운용 현황</h1>
        <span className="muted small">{preview ? '손님에게 보이는 화면 미리 보기 · ' : ''}읽기 전용 · 30초마다 갱신</span>
        <button className="small" onClick={reload}>새로고침</button>
      </div>
      {error && <p className="error">{error}</p>}
      {status && (
        <div className="share-grid share-section" data-tab="status">
          <Autopilot s={status} />
          <Research s={status} />
        </div>
      )}
      <LiveReport />
      <Sessions openId={openId} onOpen={setOpenId} />
      <SavedReports />
      <nav className="share-tabbar" aria-label="손님 화면">
        {TABS.map((x) => (
          <button key={x.key} className={tab === x.key ? 'on' : ''} aria-current={tab === x.key ? 'page' : undefined}
            onClick={() => { setTab(x.key); window.scrollTo({ top: 0 }) }}>
            <span aria-hidden>{x.icon}</span>{x.label}
          </button>
        ))}
      </nav>
    </section>
  )
}

function Autopilot({ s }: { s: ShareStatus }) {
  const a = s.autopilot
  return (
    <div className="card share-card">
      <h3>자동 운용 <span className={`chip ${a.enabled ? 'paid' : 'unset'}`}>{a.enabled ? '켜짐' : '꺼짐'}</span></h3>
      <p className="small">
        {a.mode === 'continuous' ? '상시 운용 — 장 내내 에이전트들이 계속 대화하고 필요할 때 주문' : '정기 회의 — 정해진 시각에 토론'}
        {a.intents > 0 && <span className="muted"> · 운용 지시 {a.intents}개</span>}
      </p>
      {a.blocked && <p className="error small">{a.blocked}</p>}
      {a.window_note && <p className="muted small">{a.window_note}</p>}
      {a.window && <p className="muted small">자동 판단 시간 {a.window[0]} ~ {a.window[1]}</p>}
      {a.next && <p className="small">다음 판단 <b>{a.next.time}</b> {a.next.agenda && <span className="muted">— {a.next.agenda}</span>}</p>}
      {a.mode !== 'continuous' && a.today.entries.length > 0 && (
        <ul className="share-schedule">
          {a.today.entries.map((e) => (
            <li key={e.key}>
              <b>{e.time}</b> <span className="chip">{KIND_KO[e.kind] ?? e.kind}</span> <span className="muted small">{STATUS_KO[e.status] ?? e.status}</span>
              {(e.agenda || e.reason) && <div className="muted small">{e.agenda || e.reason}</div>}
            </li>
          ))}
        </ul>
      )}
      {s.latest && <p className="small">최근 회의: <b>{s.latest.minutes?.headline ?? s.latest.name}</b> <span className="muted">{s.latest.created_at.slice(5, 16)}</span></p>}
    </div>
  )
}

const list = (v: unknown): Record<string, unknown>[] => (Array.isArray(v) ? (v as Record<string, unknown>[]) : [])
const str = (v: unknown) => (typeof v === 'string' ? v : v === null || v === undefined ? '' : String(v))
const REGIME_KO: Record<string, string> = { risk_on: '위험 선호', neutral: '중립', risk_off: '위험 회피' }

function Research({ s }: { s: ShareStatus }) {
  const m = s.desk.market?.data
  const sec = s.desk.sector?.data
  const ideas = s.desk.ideas?.data
  if (!m && !sec && !ideas && !s.desk.alerts.length) {
    return <div className="card share-card"><h3>오늘 리서치</h3><p className="muted small">아직 오늘 리서치가 없어요 (장 시작 40분 전부터).</p></div>
  }
  return (
    <div className="card share-card">
      <h3>오늘 리서치</h3>
      {m && (
        <div>
          <p><span className="chip">{REGIME_KO[str(m.regime)] ?? str(m.regime)}</span> <b>{str(m.headline)}</b></p>
          {str(m.summary) && <p className="small">{str(m.summary)}</p>}
          <p className="small">
            {list(m.favor).length > 0 && <>주목 {list(m.favor).map((x) => str(x.name ?? x)).join(' · ')} </>}
            {list(m.avoid).length > 0 && <span className="muted">· 피할 {list(m.avoid).map((x) => str(x.name ?? x)).join(' · ')}</span>}
          </p>
        </div>
      )}
      {sec && (
        <p className="small">
          비중 확대 {list(sec.overweight).map((x) => str(x.name)).join(' · ') || '없음'}
          {list(sec.underweight).length > 0 && <span className="muted"> · 축소 {list(sec.underweight).map((x) => str(x.name)).join(' · ')}</span>}
        </p>
      )}
      {ideas && list(ideas.picks).length > 0 && (
        <div className="share-ideas">
          {list(ideas.picks).map((x) => <span key={str(x.symbol)} className="chip" title={str(x.reason)}>{str(x.name)}</span>)}
        </div>
      )}
      {s.desk.alerts.length > 0 && (
        <ul className="share-alerts">{s.desk.alerts.map((a, i) => <li key={i} className="small">⚠ {a.text}</li>)}</ul>
      )}
    </div>
  )
}

function LiveReport() {
  const { data, error, reload } = useLoad(() => shareApi.report(), [])
  return (
    <div className="share-section" data-tab="report">
      <div className="share-section-head"><h2>지금 보고서</h2><button className="small" onClick={reload}>다시 만들기</button></div>
      {error && <p className="error">{error}</p>}
      {!data && !error && <p className="muted">보고서를 만드는 중…</p>}
      {data && <ReportCard report={data} by="실시간 보고서" />}
    </div>
  )
}

function Sessions({ openId, onOpen }: { openId: number | null; onOpen: (id: number | null) => void }) {
  const { data } = useLoad(() => shareApi.sessions(15), [])
  return (
    <div className="share-section" data-tab="meetings">
      <div className="share-section-head"><h2>회의</h2></div>
      {data?.length === 0 && <p className="muted">아직 회의가 없어요.</p>}
      <ul className="share-sessions">
        {(data ?? []).map((r) => (
          <li key={r.id} className="card">
            <button className="share-session-head" onClick={() => onOpen(openId === r.id ? null : r.id)} aria-expanded={openId === r.id}>
              <b>{r.minutes?.headline ?? r.name}</b>
              <span className="muted small">{r.created_at.slice(5, 16)} · {STATUS_KO[r.status] ?? r.status}{r.autopilot ? ' · 자동 운용' : ''}</span>
            </button>
            {r.minutes?.summary && <p className="small">{r.minutes.summary}</p>}
            <Orders r={r} />
            {openId === r.id && <Transcript id={r.id} running={r.status === 'running' || r.status === 'queued'} />}
          </li>
        ))}
      </ul>
    </div>
  )
}

function Orders({ r }: { r: ShareSession }) {
  if (!r.orders.length) return null
  return (
    <div className="share-orders">
      {r.orders.map((o, i) => (
        <span key={i} className={`chip ${o.side === 'buy' ? 'up' : 'down'}`}>
          {o.name || o.symbol} {SIDE_KO[o.side]} {o.quantity}주{o.price ? ` @ ${won(o.price)}` : ''} · {o.status}
        </span>
      ))}
    </div>
  )
}

function Transcript({ id, running }: { id: number; running: boolean }) {
  const [msgs, setMsgs] = useState<ShareMessage[]>([])
  useEffect(() => {
    let alive = true
    let timer: number | undefined
    let last = 0
    const tick = () => shareApi.messages(id, last).then((r) => {
      if (!alive) return
      if (r.messages.length) {
        last = r.messages[r.messages.length - 1].seq
        setMsgs((prev) => [...prev, ...r.messages])
      }
      if (running) timer = window.setTimeout(tick, 2000)
    }).catch(() => alive && running && (timer = window.setTimeout(tick, 5000)))
    tick()
    return () => { alive = false; window.clearTimeout(timer) }
  }, [id, running])
  const names = new Map<string, string>()
  for (const m of msgs) for (const c of list(m.candidates)) names.set(str(c.symbol), str(c.name))
  const sym = (s: string) => names.get(s) || s
  return (
    <div className="share-transcript">
      {msgs.length === 0 && <p className="muted small">대화를 불러오는 중…</p>}
      {msgs.map((m) => <Line key={m.seq} m={m} sym={sym} />)}
    </div>
  )
}

function Line({ m, sym }: { m: ShareMessage; sym: (s: string) => string }) {
  switch (m.type) {
    case 'round':
      return <div className="share-divider">{str(m.label) || `라운드 ${str(m.round)}`}</div>
    case 'user':
      return <div className="share-line admin"><b>관리자</b> {str(m.text)}</div>
    case 'statement':
      return (
        <div className="share-line">
          <AgentAvatar p={{ key: str(m.agent), title: str(m.title) }} className="mini" />
          <div>
            <b>{str(m.title)}</b>{' '}
            {list(m.views).filter((v) => !v.implicit).map((v) => (
              <span key={str(v.symbol)} className={`chip stance-${str(v.stance)}`} title={str(v.reason)}>
                {sym(str(v.symbol))} {STANCE_LABEL[str(v.stance) as 'buy' | 'sell' | 'none'] ?? str(v.stance)}
              </span>
            ))}
            {str(m.thesis) && <p className="small">{str(m.thesis)}</p>}
            {m.error ? <p className="error small">응답 실패</p> : null}
          </div>
        </div>
      )
    case 'gate': {
      const g = (m.gate ?? {}) as Record<string, unknown>
      return <div className="share-divider gate">게이트 · {str(g.reason) || str(g.status)}</div>
    }
    case 'decision': {
      const d = (m.decision ?? {}) as Record<string, unknown>
      return <div className="share-line trader"><b>트레이더 결정</b> {str(d.rationale)}</div>
    }
    case 'order': {
      const o = (m.order ?? m) as Record<string, unknown>
      return <div className="share-line order">주문 · {sym(str(o.symbol))} {SIDE_KO[str(o.side) as 'buy' | 'sell'] ?? str(o.side)} {str(o.quantity)}주 · {str(o.status)}</div>
    }
    case 'minutes': {
      const r = (m.minutes ?? m) as Record<string, unknown>
      return <div className="share-line minutes"><b>회의록</b> {str(r.headline)} <span className="muted small">{str(r.summary)}</span></div>
    }
    case 'end':
      return <div className="share-divider">끝 {m.reason ? `· ${str(m.reason)}` : ''}</div>
    default:
      return null
  }
}

function SavedReports() {
  const { data } = useLoad(() => shareApi.reports(10), [])
  const [open, setOpen] = useState<number | null>(null)
  if (!data?.length) return null
  return (
    <div className="share-section" data-tab="report">
      <div className="share-section-head"><h2>지난 보고서</h2></div>
      <ul className="share-reports">
        {data.map((r: SavedReport) => (
          <li key={r.id}>
            <button className="link" onClick={() => setOpen(open === r.id ? null : r.id)}>
              {r.morning ? '아침 브리핑' : r.title} <span className="muted small">{r.created_at.slice(5, 16)}</span>
            </button>
            {open === r.id && r.report && <ReportCard report={r.report} text={r.body} by={r.morning ? '아침 브리핑' : '보고서'} />}
          </li>
        ))}
      </ul>
    </div>
  )
}
