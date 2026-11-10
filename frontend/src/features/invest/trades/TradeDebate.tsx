/**
 * 거래 기록 > 펼친 행 — 이 주문을 낳은 토론. 안건 → 입장판 결과(매수 · 매도 무게, 머릿수) → 에이전트별 마지막 의견 → 트레이더 판단 · 위험 메모 → 주문 검사.
 * 열 때 한 번 불러온다 (/api/invest/trades/{id}/debate). 직접 낸 주문은 토론 없이 근거 · 검사만, 토스에서 한 거래는 체결 내용만.
 */
import { Link } from 'react-router-dom'
import { useLoad } from '../../../shared/useLoad'
import { AgentAvatar } from '../agents/Face'
import { nameOf, type Person } from '../agents/people'
import { investApi, type Trade, type TradeDebate, type TradeOpinion } from '../api'
import { STANCE_LABEL } from '../format'

export default function TradeDebateView({ trade, people }: { trade: Trade; people: Map<string, Person> }) {
  if (trade.source === 'toss') {
    return (
      <div className="trade-debate">
        <p className="small"><b>토스에서 직접 한 거래</b>예요 — 앱 밖(또는 앱을 쓰기 전)에 체결돼 토스 체결 내역에서 가져왔어요. 에이전트 토론은 없어요.</p>
        <p className="small muted">체결 {trade.at.slice(0, 16)} · {trade.quantity}주 · 체결가 {trade.price?.toLocaleString() ?? '–'}{trade.currency === 'KRW' ? '원' : ` ${trade.currency}`}</p>
      </div>
    )
  }
  return <Debate trade={trade} people={people} />
}

function Debate({ trade, people }: { trade: Trade; people: Map<string, Person> }) {
  const { data, error } = useLoad(() => investApi.tradeDebate(trade.id), [trade.id])
  return (
    <div className="trade-debate">
      {error && <p className="error small">{error}</p>}
      {!data && !error && <p className="muted small">토론 기록을 불러오는 중…</p>}
      {data && <Body d={data} people={people} />}
    </div>
  )
}

function Body({ d, people }: { d: TradeDebate; people: Map<string, Person> }) {
  if (d.source === 'manual') {
    return (
      <>
        <p className="small"><b>직접 낸 주문</b>이에요 — 에이전트 토론 없이 화면에서 냈어요.</p>
        {d.reason && <p className="small">{d.reason}</p>}
        <Checks checks={d.checks} />
      </>
    )
  }
  return (
    <>
      <div className="trade-debate-head">
        <span className="small">
          {d.agenda ? <><span className="muted">안건</span> {d.agenda}</> : <span className="muted">{d.run_name ?? '토론'}</span>}
        </span>
        {d.run_id && <Link className="small" to={`/invest/ai?session=${d.run_id}`}>토론 전체 보기 →</Link>}
      </div>
      {!d.found && (
        <p className="muted small">
          {d.run_id ? '이 주문이 나온 토론 기록을 찾지 못했어요 (지워졌거나 예전 형식).' : '어느 토론에서 나왔는지 기록이 없어요.'}
          {d.reason && <> 트레이더가 남긴 근거: {d.reason}</>}
        </p>
      )}
      {d.gate && <Gate g={d.gate} side={d.side} />}
      {d.opinions.length > 0 && (
        <section>
          <h4 className="trade-debate-title">에이전트 의견 <span className="muted small">주문 전 마지막 입장 · 같은 편 먼저</span></h4>
          <ul className="trade-opinions">
            {d.opinions.map((o) => <Opinion key={o.agent} o={o} p={people.get(o.agent) ?? { key: o.agent, title: o.title }} side={d.side} />)}
          </ul>
        </section>
      )}
      {d.decision && (
        <section>
          <h4 className="trade-debate-title">{d.decision.title ?? '트레이더'} 판단 {d.decision.at && <span className="muted small">{d.decision.at.slice(0, 5)}</span>}</h4>
          <p className="small trade-rationale">{d.decision.rationale || d.reason}</p>
          {d.decision.risk_notes.length > 0 && (
            <ul className="small trade-risks">{d.decision.risk_notes.map((r, i) => <li key={i}>{r}</li>)}</ul>
          )}
        </section>
      )}
      {!d.decision && d.found && d.reason && <p className="small trade-rationale">{d.reason}</p>}
      <Checks checks={d.checks} />
    </>
  )
}

function Gate({ g, side }: { g: NonNullable<TradeDebate['gate']>; side: 'buy' | 'sell' }) {
  const w = g.weights
  const total = w ? w.buy + w.sell : 0
  return (
    <section className="trade-gate">
      <h4 className="trade-debate-title">입장판 결과 {g.at && <span className="muted small">{g.at.slice(0, 5)}</span>}</h4>
      {w && total > 0 && (
        <div className="trade-gate-bar" role="img" aria-label={`매수 무게 ${w.buy}, 매도 무게 ${w.sell}`}>
          <i className="buy" style={{ width: `${(w.buy / total) * 100}%` }} />
          <i className="sell" style={{ width: `${(w.sell / total) * 100}%` }} />
        </div>
      )}
      <p className="small">
        {w && <><b className={side === 'buy' ? 'up' : ''}>매수 무게 {w.buy}</b> · <b className={side === 'sell' ? 'down' : ''}>매도 무게 {w.sell}</b></>}
        {g.counts && <span className="muted"> (매수 {g.counts.buy} · 매도 {g.counts.sell} · 의견 없음 {g.counts.none})</span>}
      </p>
      {g.reason && <p className="muted small">{g.reason}</p>}
    </section>
  )
}

function Opinion({ o, p, side }: { o: TradeOpinion; p: Person; side: 'buy' | 'sell' }) {
  return (
    <li className={`trade-opinion${o.stance === side ? '' : ' against'}`}>
      <AgentAvatar p={p} className="mini" />
      <div>
        <div className="trade-opinion-head">
          <b>{nameOf(p)}</b>{p.name && <span className="muted small">{p.title}</span>}
          <span className={`stance ${o.stance}`}>{STANCE_LABEL[o.stance]}</span>
          {o.confidence !== null && (
            <span className="trade-conf" title={`확신 ${o.confidence}`}><span className="conf"><i style={{ width: `${o.confidence * 100}%` }} /></span>
              <span className="muted small">확신 {o.confidence.toFixed(2)}</span></span>
          )}
          {o.changed && <span className="chip small">입장 바꿈</span>}
          {o.at && <span className="muted small">{o.at.slice(0, 5)}</span>}
        </div>
        <p className="small">{o.reason}</p>
      </div>
    </li>
  )
}

function Checks({ checks }: { checks: string[] }) {
  if (!checks.length) return null
  return (
    <details className="small trade-checks">
      <summary>주문 검사 {checks.length}개 통과</summary>
      <ul>{checks.map((c, i) => <li key={i}>{c}</li>)}</ul>
    </details>
  )
}
