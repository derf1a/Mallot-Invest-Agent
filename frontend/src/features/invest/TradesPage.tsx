/**
 * 거래 기록 — 체결된 매수 · 매도마다 거래 금액 · 지금 금액 · 손익과, 그 주문을 낳은 에이전트 토론(행을 누르면 펼침).
 * 맨 위 합계 → 종목별 합계 → 거래 목록 순서. 숫자는 서버(invest/ops/trades.py)가 계산한다. 상승 빨강 · 하락 파랑.
 * 앱 주문 + 토스 계좌의 지난 체결(source='toss')을 시각순으로. 매도 손익은 매도 때 적어 둔 토스 평단(없으면 이 기록 안의 앞선 매수 단가)으로 내고, 둘 다 모르면 비워 둔다.
 */
import { Fragment, useMemo, useState } from 'react'
import { StatTile } from '../../shared/charts'
import { useLoad } from '../../shared/useLoad'
import type { Person } from './agents/people'
import { investApi, type Trade, type TradeHistory } from './api'
import { dir, pct, price, qty, signed, won } from './format'
import TradeDebateView from './trades/TradeDebate'

const SIDE = { buy: '매수', sell: '매도' } as const
const SOURCE = { agent: 'AI', manual: '직접', toss: '토스' } as const
type SideFilter = 'all' | 'buy' | 'sell'
type SourceFilter = 'all' | 'agent' | 'manual' | 'toss'

const signedWon = (v: number | null | undefined) => (v === null || v === undefined ? '–' : `${signed(v)}원`)

export default function TradesPage() {
  const [version, setVersion] = useState(0)
  const { data, error } = useLoad(
    () => investApi.trades().then((h) => ({ h, at: new Date().toTimeString().slice(0, 8) })),
    [version],
  )
  const { data: team } = useLoad(() => investApi.agents(), [])
  const people = useMemo(() => {
    const m = new Map<string, Person>()
    for (const a of [...(team?.roster ?? []), ...(team?.desk.people ?? [])]) m.set(a.key, a)
    return m
  }, [team])

  return (
    <section className="page trades-page">
      <div className="page-header">
        <h1>거래 기록</h1>
        <span className="toolbar">
          {data && <span className="muted small">기준 {data.at}</span>}
          <button onClick={() => setVersion((v) => v + 1)}>새로 고침</button>
        </span>
      </div>
      {error && <p className="alert warn">거래 기록을 불러오지 못했어요: {error}</p>}
      {!data && !error && <p className="muted">불러오는 중… (지금 가격도 함께 받아요)</p>}
      {data && (data.h.trades.length ? <History h={data.h} people={people} /> : (
        <article className="card"><p className="muted">아직 체결된 거래가 없어요. 주문이 체결되면 여기에 쌓여요.</p></article>
      ))}
    </section>
  )
}

function History({ h, people }: { h: TradeHistory; people: Map<string, Person> }) {
  const t = h.totals
  return (
    <>
      {!h.quoted && <p className="alert warn">지금 가격을 받지 못해 현재 금액 · 평가 손익을 비워 뒀어요. 거래 금액은 그대로예요.</p>}
      <div className="tile-row">
        <StatTile label="매수 금액" value={won(t.bought)} hint={`${t.buys}건`} />
        <StatTile label="매도 금액" value={won(t.sold)} hint={`${t.sells}건`} />
        <StatTile label="실현 손익" value={t.known_sells ? <span className={dir(t.realized)}>{signedWon(t.realized)}</span> : '–'}
          hint={t.known_sells ? `산 가격을 아는 매도 ${t.known_sells}건` : '산 가격을 아는 매도가 아직 없어요'} />
        <StatTile label="남은 매수분 지금" value={t.held_cost ? won(t.held_value) : '–'}
          hint={t.held_cost
            ? <>평가 <span className={dir(t.unrealized)}>{signedWon(t.unrealized)}</span> · 원금 {won(t.held_cost)}</>
            : '이 기록에서 산 것 중 남은 것이 없어요'} />
      </div>
      {t.other_currency > 0 && <p className="muted small">합계는 원화 종목만이에요 (외화 종목 {t.other_currency}개 제외).</p>}
      <Symbols h={h} />
      <Trades trades={h.trades} people={people} />
    </>
  )
}

/** 종목별 — 매수 · 매도 금액 합, 이 기록에서 산 것 중 남은 수량 · 지금 금액 · 평가 손익, 실현 손익 */
function Symbols({ h }: { h: TradeHistory }) {
  return (
    <article className="card">
      <h2>종목별</h2>
      <div className="scroll-x">
        <table className="holdings-table small">
          <thead>
            <tr>
              <th scope="col">종목</th><th scope="col">매수 금액</th><th scope="col">매도 금액</th><th scope="col">실현 손익</th>
              <th scope="col" title="이 기록에서 산 것 중 아직 팔지 않은 수량">남은 수량</th><th scope="col">현재가</th>
              <th scope="col">현재 금액</th><th scope="col">평가 손익</th>
            </tr>
          </thead>
          <tbody>
            {h.symbols.map((s) => (
              <tr key={s.symbol}>
                <th scope="row">{s.name}<span className="holdings-sub">{s.symbol}</span></th>
                <td>{s.buy_count ? <>{won(s.bought)}<span className="holdings-sub">{s.buy_count}건</span></> : '–'}</td>
                <td>{s.sell_count ? <>{won(s.sold)}<span className="holdings-sub">{s.sell_count}건</span></> : '–'}</td>
                <td className={dir(s.realized)}>
                  {s.known_sells ? signedWon(s.realized) : '–'}
                  {s.unknown_sold > 0 && <span className="holdings-sub">{qty(s.unknown_sold)}주 산 가격 모름</span>}
                </td>
                <td>{s.held_quantity ? `${qty(s.held_quantity)}주` : '–'}</td>
                <td>{price(s.now_price, s.currency)}</td>
                <td>{s.held_quantity ? won(s.held_value) : '–'}</td>
                <td className={dir(s.unrealized)}>{s.held_quantity ? signedWon(s.unrealized) : '–'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </article>
  )
}

function Trades({ trades, people }: { trades: Trade[]; people: Map<string, Person> }) {
  const [side, setSide] = useState<SideFilter>('all')
  const [source, setSource] = useState<SourceFilter>('all')
  const [open, setOpen] = useState<number | null>(null)
  const shown = trades.filter((x) => (side === 'all' || x.side === side) && (source === 'all' || x.source === source))
  return (
    <article className="card">
      <div className="section-head">
        <h2>거래 목록 <span className="muted small">{shown.length}건 · 행을 누르면 판단 근거</span></h2>
        <span className="trades-filters">
          <span className="segmented small" role="group" aria-label="매수 · 매도">
            {(['all', 'buy', 'sell'] as const).map((k) => (
              <button key={k} className={side === k ? 'on' : ''} onClick={() => setSide(k)}>{k === 'all' ? '전체' : SIDE[k]}</button>
            ))}
          </span>
          <span className="segmented small" role="group" aria-label="누가 냈나">
            {(['all', 'agent', 'manual', 'toss'] as const).map((k) => (
              <button key={k} className={source === k ? 'on' : ''} onClick={() => setSource(k)}>
                {{ all: '전체', agent: 'AI', manual: '직접', toss: '토스' }[k]}
              </button>
            ))}
          </span>
        </span>
      </div>
      {!shown.length && <p className="muted small">조건에 맞는 거래가 없어요.</p>}
      {shown.length > 0 && (
        <div className="scroll-x">
          <table className="holdings-table small trades-table">
            <thead>
              <tr>
                <th scope="col">종목</th><th scope="col">체결가</th><th scope="col">거래 금액</th><th scope="col">현재가</th>
                <th scope="col" title="거래한 수량 × 지금 가격">현재 금액</th>
                <th scope="col" title="매수: 지금 금액 − 매수 금액 · 매도: (매도가 − 산 가격) × 수량">손익</th>
                <th scope="col">판단</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((x) => (
                <Fragment key={x.id}>
                  <TradeRow x={x} open={open === x.id} onToggle={() => setOpen(open === x.id ? null : x.id)} />
                  {open === x.id && (
                    <tr className="trade-detail">
                      <td colSpan={7}><TradeDebateView trade={x} people={people} /></td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted small">
        토스 계좌의 지난 체결(앱 밖 · 앱을 쓰기 전 거래, '토스')도 그때 체결가로 함께 보여요. 매도 손익은 매도할 때 적어 둔 토스 평단으로,
        없으면 이 기록 안 앞선 매수의 이동평균 단가로 내요. 토스 내역보다 먼저 산 몫은 산 가격을 몰라 비워 둬요.
        '판 뒤'는 판 수량이 지금 얼마인지 — + 면 판 뒤에 올랐다는 뜻이에요.
      </p>
    </article>
  )
}

function TradeRow({ x, open, onToggle }: { x: Trade; open: boolean; onToggle: () => void }) {
  const gain = x.side === 'buy' ? x.pnl : x.realized
  const rate = x.side === 'buy' ? x.pnl_rate : x.realized_rate
  return (
    <tr className={`trade-row${open ? ' open' : ''}`} onClick={onToggle}>
      <th scope="row">
        <button type="button" className="trade-toggle" aria-expanded={open} onClick={(e) => { e.stopPropagation(); onToggle() }}>
          <span className="trade-caret" aria-hidden>{open ? '▾' : '▸'}</span>
          {x.name} <span className={`stance ${x.side}`}>{SIDE[x.side]} {qty(x.quantity)}주</span>
        </button>
        <span className="holdings-sub">{x.at.slice(0, 16)}{x.status === 'partial' ? ' · 일부 체결' : ''}</span>
      </th>
      <td>{price(x.price, x.currency)}</td>
      <td>{x.currency === 'KRW' ? won(x.amount) : price(x.amount, x.currency)}</td>
      <td>{price(x.now_price, x.currency)}</td>
      <td>{x.currency === 'KRW' ? won(x.now_value) : price(x.now_value, x.currency)}</td>
      <td className={dir(gain)}>
        {gain === null || gain === undefined
          ? <span className="muted">{x.side === 'sell' ? '산 가격 모름' : '–'}</span>
          : <>{signedWon(gain)} <span className="small">{pct(rate, 1)}</span></>}
        <span className="holdings-sub">
          {x.side === 'buy' ? '평가' : `실현${x.basis_price ? ` · 산 가격 ${price(x.basis_price)}` : ''}`}
          {x.side === 'sell' && x.after_rate !== null && x.after_rate !== undefined && <> · 판 뒤 <b className={dir(x.after_rate)}>{pct(x.after_rate, 1)}</b></>}
        </span>
      </td>
      <td>
        <span className="chip" title={x.source === 'toss' ? '앱 밖(토스 앱 · 앱을 쓰기 전)에서 한 거래' : undefined}>{SOURCE[x.source]}</span>
        {x.run_id && <span className="holdings-sub">{x.run_name ?? '토론'} #{x.run_id}</span>}
      </td>
    </tr>
  )
}
