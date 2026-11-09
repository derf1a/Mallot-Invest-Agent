/**
 * 한 페이지 보고서 카드 — 비서가 만든 보고서를 채팅방에 그림 위주로 보여 주고, '이미지로 저장'(캔버스로 그린 PNG, reportImage.ts)·'글 복사'.
 * 칸은 관리자가 계속 볼 것만: 주의 · 수익률(추이·목표 대비) · 자산 배분·보유 종목 · 회의 요약 · 장내 이슈 (briefing.ts reportView).
 * 숫자는 서버(briefing.py)가 계산한 그대로, 맨 위 의견(comment)만 비서가 쓴다. 막대·칸 옆에 늘 숫자가 있어 색만으로 읽지 않는다.
 */
import { useState } from 'react'
import { LineChart } from '../../../shared/charts'
import { useTip } from '../../../shared/ui/useTip'
import type { Briefing } from '../api'
import { AllocationBar } from '../portfolio/charts'
import { HEAT_MAX, RATE_KEYS, REGIMES, axisPct, pct, pointLabel, reportView, toneOf, won, type Curve, type Goal, type Holding, type MarketView, type MeetingView, type Tone } from './briefing'
import { reportPng, saveImage } from './reportImage'

/** 카드에 바로 보이는 뉴스 수 — 나머지는 '더 보기' (이미지에는 전부) */
const NEWS_SHOWN = 4
const toneClass = (t: Tone) => (t === 'up' ? 'up' : t === 'down' ? 'down' : t === 'muted' ? 'muted' : t === 'warn' ? 'warn-text' : '')

/** 0 을 가운데 둔 막대 — 오르면 오른쪽 빨강, 내리면 왼쪽 파랑 */
function Diverge({ value, max }: { value: number | null; max: number }) {
  const w = `${Math.min(Math.abs(value ?? 0) / max, 1) * 50}%`
  return (
    <span className="pnl-track" aria-hidden>
      {!!value && <i className={value > 0 ? 'gain' : 'loss'} style={value > 0 ? { left: '50%', width: w } : { right: '50%', width: w }} />}
    </span>
  )
}

function CurveChart({ c }: { c: Curve }) {
  const series = [{ label: '내 계좌', points: c.points.map((p, i): [number, number] => [i, p.rate]) }]
  const index = c.points.flatMap((p, i): [number, number][] => (p.index === null ? [] : [[i, p.index]]))
  if (c.index && index.length) series.push({ label: c.index, points: index })
  return (
    <LineChart title="운용 수익률 추이" series={series} height={170} xLabel="시각" format={axisPct} xTicks={c.ticks}
      xFormat={(x) => pointLabel(c.points[x]?.at ?? '')} tickFormat={(x) => pointLabel(c.points[x]?.at ?? '').split(' ')[0]} />
  )
}

function Goals({ rows }: { rows: Goal[] }) {
  return (
    <div className="report-goals">
      <span className="report-label">목표 대비</span>
      {rows.map((g) => {
        const at = (n: number) => Math.max(0, Math.min(1, (n - g.min) / (g.max - g.min))) * 100
        const [from, to] = g.signed ? [Math.min(at(0), at(g.actual)), Math.max(at(0), at(g.actual))] : [0, at(g.actual)]
        const fill = g.signed ? (g.actual >= 0 ? 'gain' : 'loss') : g.warn ? 'over' : ''
        return (
          <div key={g.label} className="report-goal">
            <div className="meter-head">
              <span>{g.label}</span>
              <span><span className="muted small">{g.note}</span> <b className={`num ${g.warn ? 'warn-text' : toneClass(g.tone)}`}>{g.value}</b></span>
            </div>
            <div className="meter-track" role="img" aria-label={`${g.label} ${g.value}, ${g.note}`}>
              <i className={fill} style={{ left: `${from}%`, width: `${to - from}%` }} />
              {g.signed && <span className="report-zero" style={{ left: `${at(0)}%` }} />}
              <span className="meter-mark" style={{ left: `${at(g.mark)}%` }} />
            </div>
          </div>
        )
      })}
    </div>
  )
}

function Holdings({ rows }: { rows: Holding[] }) {
  const { box, show, hide, node } = useTip()
  const weightMax = Math.max(...rows.map((h) => h.weight ?? 0), 0.01)
  const rateMax = Math.max(...rows.map((h) => Math.abs(h.rate ?? 0)), 0.02)
  return (
    <div className="report-holdings" ref={box} onMouseLeave={hide}>
      <div className="rh-row rh-head">
        <span>종목</span><span>비중</span><span>손익률</span><span className="right">오늘</span>
      </div>
      {rows.map((h) => (
        <div key={h.key} className="rh-row" onMouseMove={(e) => show(e, (
          <>
            <b>{h.name}</b>
            <span>{h.sub}</span>
            {h.pnl !== null && <span>손익 {h.pnl > 0 ? '+' : ''}{won(h.pnl)} ({pct(h.rate)})</span>}
          </>
        ))}>
          <span className="rh-name">
            <b><i className="swatch" style={{ background: h.color }} />{h.name}</b>
            <small className="muted">{h.sub}</small>
          </span>
          <span className="rh-bar">
            <span className="rh-track"><i style={{ width: `${((h.weight ?? 0) / weightMax) * 100}%`, background: h.color }} /></span>
            <b className="num">{h.weight === null ? '-' : `${(h.weight * 100).toFixed(1)}%`}</b>
          </span>
          <span className="rh-bar">
            <Diverge value={h.rate} max={rateMax} />
            <b className={`num ${toneClass(toneOf(h.rate))}`}>{pct(h.rate)}</b>
          </span>
          <b className={`num right ${toneClass(toneOf(h.day))}`}>{pct(h.day)}</b>
        </div>
      ))}
      {node}
    </div>
  )
}

/** 최근 회의는 다 펼치고, 지난 회의의 문장은 '자세히' 안에 — 결론·주문이 먼저 보이게 */
function Meeting({ m, open }: { m: MeetingView; open: boolean }) {
  const words = (m.summary || m.trader || m.follow.length > 0) && (
    <>
      {m.summary && <p className="muted small">{m.summary}</p>}
      {m.trader && <p className="muted small">트레이더 · {m.trader}</p>}
      {m.follow.map((f, i) => <p key={i} className="rm-follow small">{f}</p>)}
    </>
  )
  return (
    <li>
      <div className="rm-head">
        <span className="muted small num">{m.at}</span>
        <b>{m.name}</b>
        {m.status && <span className={`chip${m.live ? ' rm-live' : ''}`}>{m.status}</span>}
      </div>
      {m.headline && <p className="rm-headline">{m.headline}</p>}
      {m.talks && <span className="muted small">{m.talks}</span>}
      {m.board.map((b) => (
        <div key={b.name} className="rm-board">
          <span>{b.name}</span>
          <span className="rm-stack" role="img" aria-label={`매수 ${b.buy}, 매도 ${b.sell}`}>
            {b.buy > 0 && <i className="gain" style={{ flexGrow: b.buy }} />}
            {b.sell > 0 && <i className="loss" style={{ flexGrow: b.sell }} />}
          </span>
          <span className="muted small num">매수 {b.buy} · 매도 {b.sell}</span>
        </div>
      ))}
      {m.orders.map((o, i) => (
        <div key={i} className="rm-order">
          <span className={`rm-side ${o.buy ? 'buy' : 'sell'}`}>{o.side}</span>
          <span>{o.name} {o.quantity}</span>
          <span className="muted small">{o.status}</span>
          {o.change !== null && <b className={`num ${toneClass(toneOf(o.change))}`}>{pct(o.change)}</b>}
        </div>
      ))}
      {words && (open ? words : <details className="report-more"><summary>자세히</summary>{words}</details>)}
    </li>
  )
}

function Sectors({ s }: { s: NonNullable<MarketView['sectors']> }) {
  const { box, show, hide, node } = useTip()
  if (!s.rows.length) {
    return (
      <div className="report-chips">
        {s.over.map((x) => <span key={x} className="chip sector-over">▲ {x}</span>)}
        {s.under.map((x) => <span key={x} className="chip sector-under">▼ {x}</span>)}
      </div>
    )
  }
  return (
    <div className="report-sectors" ref={box} onMouseLeave={hide}>
      <table>
        <thead>
          <tr><th scope="col">섹터</th>{RATE_KEYS.map((k) => <th key={k.key} scope="col">{k.label}</th>)}</tr>
        </thead>
        <tbody>
          {s.rows.map((r) => (
            <tr key={r.name} onMouseMove={(e) => r.reason && show(e, <><b>{r.name}</b><span>{r.reason}</span></>)}>
              <th scope="row"><span className={r.side}>{r.side === 'over' ? '▲' : '▼'}</span> {r.name}</th>
              {r.rates.map((n, i) => (
                <td key={i} className="num" style={n === null ? undefined : {
                  background: `color-mix(in srgb, var(--${n >= 0 ? 'up' : 'down'}) ${Math.round(Math.min(Math.abs(n) / HEAT_MAX, 1) * 42 + 4)}%, transparent)` }}>
                  {pct(n)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {node}
    </div>
  )
}

function NewsList({ rows }: { rows: MarketView['news'] }) {
  return (
    <ul className="report-news">
      {rows.map((n, i) => (
        <li key={i}>
          <span>{n.tag && <span className="chip">{n.tag}</span>} {n.title}</span>
          {n.source && <span className="muted small">{n.source}</span>}
        </li>
      ))}
    </ul>
  )
}

function Market({ m }: { m: MarketView }) {
  const max = Math.max(...m.indicators.map((i) => Math.abs(i.rate ?? 0)), 0.005)
  const v = m.view
  const empty = !m.alerts.length && !m.note && !m.indicators.length && !v && !m.sectors && !m.news.length
  return (
    <>
      {m.alerts.map((a, i) => <p key={i} className="report-alert">{a}</p>)}
      {m.note && <p className="warn-text small">{m.note}</p>}
      {empty && <p className="muted small">아직 모은 이슈가 없어요</p>}
      {m.indicators.length > 0 && (
        <ul className="report-indicators">
          {m.indicators.map((i) => (
            <li key={i.name}>
              <span>{i.name}</span>
              <span className="num">{i.price}</span>
              <Diverge value={i.rate} max={max} />
              <b className={`num ${toneClass(toneOf(i.rate))}`}>{pct(i.rate, 2)}</b>
            </li>
          ))}
        </ul>
      )}
      {v && (
        <div className="report-regime">
          <span className="report-label">시장 국면{v.by && <small> · {v.by}</small>}</span>
          <div className="regime-gauge" role="img" aria-label={`시장 국면 ${v.regime}`}>
            {REGIMES.map((g) => <span key={g.key} className={g.key === v.key ? `on ${g.key}` : ''}>{g.label}</span>)}
          </div>
          {v.headline && <b>{v.headline}</b>}
          {v.summary && <p className="muted small">{v.summary}</p>}
          {(v.favor.length > 0 || v.avoid.length > 0 || v.cash) && (
            <div className="report-chips">
              {v.favor.map((x) => <span key={x} className="chip theme-favor">주목 {x}</span>)}
              {v.avoid.map((x) => <span key={x} className="chip theme-avoid">피할 것 {x}</span>)}
              {v.cash && <span className="chip">{v.cash}</span>}
            </div>
          )}
        </div>
      )}
      {m.sectors && (
        <div className="report-block">
          <span className="report-label">섹터 비중 의견</span>
          <Sectors s={m.sectors} />
        </div>
      )}
      {m.news.length > 0 && (
        <div className="report-block">
          <span className="report-label">뉴스</span>
          <NewsList rows={m.news.slice(0, NEWS_SHOWN)} />
          {m.news.length > NEWS_SHOWN && (
            <details className="report-more">
              <summary>뉴스 {m.news.length - NEWS_SHOWN}개 더 보기</summary>
              <NewsList rows={m.news.slice(NEWS_SHOWN)} />
            </details>
          )}
        </div>
      )}
    </>
  )
}

export default function ReportCard({ report, text, by }: { report: Briefing; text?: string; by: string }) {
  const v = reportView(report)
  const [state, setState] = useState<string | null>(null)
  const filename = `투자현황보고-${report.created_at.replace(/[-: ]/g, '').slice(0, 12)}.png`
  const toImage = async () => {
    setState('이미지를 만드는 중…')
    try {
      await saveImage(await reportPng(report, by), filename)
      setState(null)
    } catch (e) {
      setState(e instanceof Error ? e.message : String(e))
    }
  }
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text ?? '')
      setState('글을 복사했어요')
    } catch {
      setState('복사하지 못했어요 — 길게 눌러 선택해 주세요')
    }
  }
  return (
    <article className="report-card">
      <header>
        <b>{report.title}</b>
        <span className="muted small">{report.created_at} · {by}{report.source === 'sample' ? ' · 샘플 시세' : ''}</span>
      </header>
      {report.comment && <p className="report-comment">{report.comment}</p>}
      {v.kpis.length > 0 && (
        <div className="report-kpis">
          {v.kpis.map((k) => (
            <div key={k.label}>
              <span className="muted small">{k.label}</span>
              <strong className={toneClass(k.tone)}>{k.value}</strong>
              {k.sub && <span className={`small ${k.subMuted ? 'muted' : toneClass(k.tone)}`}>{k.sub}</span>}
            </div>
          ))}
        </div>
      )}
      {v.warnings.map((w) => <p key={w} className="report-alert">{w}</p>)}
      {(v.curve || v.goals.length > 0) && (
        <section>
          <h5>수익률</h5>
          {v.curve && <CurveChart c={v.curve} />}
          {v.goals.length > 0 && <Goals rows={v.goals} />}
        </section>
      )}
      <section>
        <h5>자산 배분·보유 종목</h5>
        {v.slices.length > 0 && <AllocationBar slices={v.slices} />}
        {v.holdings.length > 0 && <Holdings rows={v.holdings} />}
        {v.notes.map((n) => <p key={n} className="muted small">{n}</p>)}
      </section>
      <section>
        <h5>회의 요약</h5>
        {v.meetings.length > 0
          ? <ol className="report-meetings">{v.meetings.map((m, i) => <Meeting key={m.key} m={m} open={i === 0} />)}</ol>
          : <p className="muted small">아직 회의가 없어요</p>}
      </section>
      <section>
        <h5>장내 이슈</h5>
        <Market m={v.market} />
      </section>
      <footer>
        <button className="primary" onClick={toImage}>이미지로 저장</button>
        {text && <button onClick={copy}>글 복사</button>}
        {state && <span className="muted small">{state}</span>}
      </footer>
    </article>
  )
}
