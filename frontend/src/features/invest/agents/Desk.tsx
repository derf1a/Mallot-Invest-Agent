/**
 * 운용 데스크 — 자산운용사처럼 시장 분석(시장 전략가) → 섹터 탐색(섹터 애널리스트) → 종목 발굴(종목 발굴 애널리스트) → 투자위원회 →
 * 집행 → 리스크 감시. 채팅방 카드(그 에이전트 말풍선 아래: 시장 국면·섹터 표·발굴 점수표·경보)와 자동 운용 서랍의 '운용 프로세스'.
 * 숫자는 모두 서버 코드가 계산한 값, 상승 빨강·하락 파랑.
 */
import { useState } from 'react'
import { investApi, type Agent, type AssistantMessage, type DeskAlert, type Ideas, type MarketView, type Screening,
  type SectorView } from '../api'
import { useTicker } from '../format'
import { useLoad } from '../../../shared/useLoad'
import { AgentAvatar } from './Face'
import { nameOf, type Person } from './people'

const REGIME: Record<MarketView['regime'], string> = { risk_on: '위험 선호', neutral: '중립', risk_off: '위험 회피' }
const CASH: Record<MarketView['cash'], string> = { raise: '현금 늘리기', keep: '현금 유지', lower: '현금 줄이기' }
const RATE: Record<string, string> = { '1d': '1일', '1w': '1주', '1mo': '1개월', '3mo': '3개월' }
const ALERT: Record<DeskAlert['kind'], string> = { drop: '급락', jump: '급등 · 차익 검토', breakout: '발굴 종목 급등' }

const spct = (v: number | null | undefined) => (v === null || v === undefined ? '-' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`)
const stone = (v: number | null | undefined) => (!v ? '' : v > 0 ? 'up' : 'down')

/** 여러 종목을 훑은 점수표와 고른 종목 — 회의 후보 선정(포트폴리오 매니저)·운용 데스크 발굴 목록(종목 발굴 애널리스트)이 같이 쓴다 */
export function ScreeningCard({ m, title = '후보 선정', byText }: {
  m: Pick<Screening, 'pool' | 'rows' | 'picks' | 'summary' | 'by'> & { picker?: string | null }
  title?: string
  /** LLM 이 골랐을 때의 설명 (없으면 '○○가 운용 지시를 보고 골랐어요') */
  byText?: string
}) {
  const [all, setAll] = useState(false)
  const reason = new Map(m.picks.map((p) => [p.symbol, p.reason]))
  const rows = all ? m.rows : m.rows.slice(0, 8)
  return (
    <div className="chat-card screening">
      <div className="chat-card-head">
        {title} · {m.pool}종목을 훑어 {m.picks.length}개를 골랐어요
        <span className="muted small">{m.by === 'llm' ? byText ?? `${m.picker ?? '포트폴리오 매니저'}가 운용 지시를 보고 골랐어요` : '점수 순으로 골랐어요'}</span>
      </div>
      {m.summary && <p className="small">{m.summary}</p>}
      <div className="scroll-x">
        <table className="screening-table small">
          <thead>
            <tr><th scope="col">종목</th><th scope="col">1일</th><th scope="col">20일</th><th scope="col">거래량</th><th scope="col">RSI</th><th scope="col">가치</th><th scope="col">점수</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.symbol} className={r.picked ? 'picked' : ''}>
                <th scope="row">
                  <span className="screening-name">{r.picked && <b className="pick-mark" aria-label="선정">✓</b>}{r.name}</span>
                  <span className="muted screening-src">{[r.held ? '보유' : '', ...r.sources].filter(Boolean).join(' · ')}</span>
                  {r.picked && reason.get(r.symbol) && <span className="screening-why">{reason.get(r.symbol)}</span>}
                </th>
                <td className={stone(r.metrics?.change_1d)}>{spct(r.metrics?.change_1d)}</td>
                <td className={stone(r.metrics?.change_20d)}>{spct(r.metrics?.change_20d)}</td>
                <td>{r.metrics?.volume_ratio ? `${r.metrics.volume_ratio.toFixed(1)}배` : '-'}</td>
                <td>{r.metrics?.rsi ?? '-'}</td>
                <td title={r.value ? `PER ${r.value.per?.toFixed(1) ?? (r.value.loss ? '적자' : '-')} · PBR ${r.value.pbr?.toFixed(2) ?? '-'} · ${r.value.report ?? ''}` : '가치 평가 없음'}>
                  {r.value ? `${r.value.score.toFixed(0)} ${r.value.grade}` : '-'}
                </td>
                <td><b>{r.score ?? '-'}</b></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {m.rows.length > 8 && (
        <button className="link-button small" onClick={() => setAll((v) => !v)}>{all ? '접기' : `점수표 ${m.rows.length}개 모두 보기`}</button>
      )}
    </div>
  )
}


/** 운용 데스크 에이전트 말 아래의 카드 (meta.desk 로 단계를 안다) */
export function DeskCard({ m }: { m: AssistantMessage }) {
  const d = m.meta.data
  if (!d) return null
  if (m.meta.desk === 'market') return <MarketCard v={d as MarketView} />
  if (m.meta.desk === 'sector') return <SectorCard v={d as SectorView} />
  if (m.meta.desk === 'ideas') {
    const v = d as Ideas
    return <ScreeningCard m={v} title="발굴 목록" byText={`${m.meta.name ?? '종목 발굴 애널리스트'}가 확대 섹터·랭킹·운용 지시를 보고 골랐어요`} />
  }
  if (m.meta.desk === 'alert') return <AlertChips alerts={(d as { alerts: DeskAlert[] }).alerts} />
  return null
}

function MarketCard({ v }: { v: MarketView }) {
  const [news, setNews] = useState(false)
  return (
    <div className="chat-card desk-card">
      <div className="chat-card-head">
        시장 분석 <span className={`regime-chip ${v.regime}`}>{REGIME[v.regime]}</span>
        <span className="muted small">{CASH[v.cash]}{v.by === 'code' ? ' · 코드 판단' : ''}{v.source === 'sample' ? ' · 샘플 시세' : ''}</span>
      </div>
      <ul className="desk-indicators small">
        {v.indicators.filter((i) => i.price !== null).map((i) => (
          <li key={i.symbol}>
            <span className="muted">{i.name}</span>
            <b className="num">{i.price!.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}{i.unit === '%' ? '%' : ''}</b>
            <span className={`num ${stone(i.change_rate)}`}>{spct(i.change_rate)}</span>
          </li>
        ))}
      </ul>
      {(v.breadth.gainers || v.breadth.losers) && (
        <p className="small muted">
          {v.breadth.gainers && <>상승 상위 20 평균 <span className="up">{spct(v.breadth.gainers.avg)}</span></>}
          {v.breadth.gainers && v.breadth.losers && ' · '}
          {v.breadth.losers && <>하락 상위 20 평균 <span className="down">{spct(v.breadth.losers.avg)}</span></>}
        </p>
      )}
      {(v.favor.length > 0 || v.avoid.length > 0) && (
        <div className="desk-themes small">
          {v.favor.map((t) => <span key={`f${t}`} className="chip theme-favor">주목 · {t}</span>)}
          {v.avoid.map((t) => <span key={`a${t}`} className="chip theme-avoid">피할 것 · {t}</span>)}
        </div>
      )}
      {v.news.length > 0 && (
        <>
          <button className="link-button small" onClick={() => setNews((x) => !x)} aria-expanded={news}>
            {news ? '뉴스 접기' : `참고한 뉴스 제목 ${v.news.length}개`}
          </button>
          {news && <ul className="desk-news small">{v.news.map((n) => <li key={n.title}>{n.title} <span className="muted">{n.source}</span></li>)}</ul>}
        </>
      )}
    </div>
  )
}

function SectorCard({ v }: { v: SectorView }) {
  const [all, setAll] = useState(false)
  const keys = Object.keys(RATE).filter((k) => v.rows.some((r) => r.rates[k] !== undefined))
  const rows = all ? v.rows : v.rows.filter((r, i) => i < 8 || r.pick)
  return (
    <div className="chat-card desk-card">
      <div className="chat-card-head">
        섹터 탐색 · {v.count}개 섹터
        <span className="muted small">{v.by === 'code' ? '기간 등락 점수 순' : '시장 분석·운용 지시를 보고 골랐어요'}</span>
      </div>
      <ul className="sector-picks small">
        {v.overweight.map((x) => <li key={x.id}><span className="chip sector-over">확대</span> <b>{x.name}</b> <span className="muted">{x.reason}</span></li>)}
        {v.underweight.map((x) => <li key={x.id}><span className="chip sector-under">축소</span> <b>{x.name}</b> <span className="muted">{x.reason}</span></li>)}
      </ul>
      <div className="scroll-x">
        <table className="screening-table small">
          <thead>
            <tr><th scope="col">섹터</th>{keys.map((k) => <th key={k} scope="col">{RATE[k]}</th>)}<th scope="col">점수</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className={r.pick === 'over' ? 'picked' : r.pick === 'under' ? 'dimmed' : ''}>
                <th scope="row">
                  <span className="screening-name">{r.pick === 'over' && <b className="pick-mark" aria-label="확대">▲</b>}{r.pick === 'under' && <b className="under-mark" aria-label="축소">▼</b>}{r.name}</span>
                  {r.intent && <span className="muted screening-src">운용 지시</span>}
                </th>
                {keys.map((k) => <td key={k} className={stone(r.rates[k])}>{spct(r.rates[k])}</td>)}
                <td><b>{r.score === null ? '-' : (r.score * 100).toFixed(1)}</b></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {v.rows.length > rows.length || all ? (
        <button className="link-button small" onClick={() => setAll((x) => !x)}>{all ? '접기' : `섹터 ${v.rows.length}개 모두 보기`}</button>
      ) : null}
    </div>
  )
}

function AlertChips({ alerts }: { alerts: DeskAlert[] }) {
  return (
    <div className="desk-themes small">
      {alerts.map((a) => (
        <span key={`${a.symbol}${a.kind}`} className={`chip alert-chip ${a.kind}`}>
          {a.name} <b className={stone(a.change)}>{spct(a.change)}</b> · {ALERT[a.kind]}
        </span>
      ))}
    </div>
  )
}

/** 자동 운용 서랍 — 운용 프로세스: 단계마다 담당·마지막으로 한 때·한 줄 결과, 지금 리서치 */
export function DeskProcess({ roster, onChanged }: { roster: Agent[]; onChanged?: () => void }) {
  const [version, setVersion] = useState(0)
  const [running, setRunning] = useState(false)
  const tick = useTicker(3000, running)
  const { data, error } = useLoad(() => investApi.desk(), [version, tick])
  const [note, setNote] = useState<string | null>(null)
  if (data && running !== !!data.running) setRunning(!!data.running)
  if (!data) return error ? <p className="error small">{error}</p> : null
  const who = (key: string): Person => {
    const d = data.people.find((p) => p.key === key)
    if (d) return d
    const a = roster.find((r) => r.key === key)
    return a ?? { key, title: key }
  }
  const run = async () => {
    setNote(null)
    try {
      await investApi.runResearch()
      setVersion((v) => v + 1)
      onChanged?.()
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    }
  }
  return (
    <section className="desk-process">
      <div className="pilot-sec-head">
        <h3>운용 프로세스</h3>
        <span className="muted small">{data.running ? '리서치 중…' : data.research.length ? `오늘 리서치 ${data.research.length}번` : '오늘 리서치 전'}</span>
      </div>
      <ol className="desk-steps">
        {data.process.map((s) => {
          const p = who(s.who)
          return (
            <li key={s.key} className={`desk-step${s.at ? ' done' : ''}`}>
              <AgentAvatar p={p} className="tiny" label={false} />
              <span className="desk-step-text">
                <span className="desk-step-head"><b>{s.title}</b> <span className="muted small">{nameOf(p)} · {p.title}</span>
                  {s.at && <span className="num small muted">{s.at}</span>}{s.by === 'code' && <span className="muted small">코드</span>}</span>
                <span className="small">{s.text ?? <span className="muted">아직</span>}</span>
              </span>
            </li>
          )
        })}
      </ol>
      <div className="desk-actions">
        <button disabled={!!data.running} onClick={run}>{data.running ? `리서치 중 #${data.running.id}` : '지금 리서치'}</button>
        {note && <span className="error small">{note}</span>}
      </div>
      <p className="muted small">
        자동 운용이 켜져 있으면 장 시작 {data.limits.prep_min}분 전에 리서치하고, 장 중에는 {data.limits.watch_min}분마다 리스크를 살펴 급변하면
        그 종목으로 수시 회의를 열어요(하루 {data.limits.max_adhoc}번). 지수가 {(data.limits.refresh_move * 100).toFixed(1)}% 넘게 움직이면 리서치를 다시 해요.
        결과는 채팅방에 각 담당이 남겨요.
      </p>
    </section>
  )
}
