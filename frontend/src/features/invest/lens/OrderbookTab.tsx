import { useLoad } from '../../../shared/useLoad'
import { investApi, type Level, type StockSnapshot } from '../api'
import { compact, dir, pct, price, useTicker } from '../format'

/** 호가 — 매도 호가(위, 높은 가격부터)와 매수 호가(아래). 가격을 누르면 주문 가격으로 넣는다. */
export default function OrderbookTab({ symbol, snap, onPickPrice }: {
  symbol: string
  snap: StockSnapshot | null
  onPickPrice: (price: number) => void
}) {
  const tick = useTicker(3000)
  const { data, error } = useLoad(() => investApi.orderbook(symbol), [symbol, tick])
  if (error) return <p className="error small">{error}</p>
  if (!data) return <p className="muted small">불러오는 중…</p>
  const currency = data.currency || snap?.currency || 'KRW'
  const asks = data.asks.slice(0, 10).reverse()
  const bids = data.bids.slice(0, 10)
  const maxVol = Math.max(1, ...asks.map((a) => a.volume), ...bids.map((b) => b.volume))
  const askTotal = data.asks.reduce((s, a) => s + (a.volume || 0), 0)
  const bidTotal = data.bids.reduce((s, b) => s + (b.volume || 0), 0)
  const prev = snap?.prev_close ?? null
  const current = snap?.price ?? null

  const row = (l: Level, side: 'ask' | 'bid') => {
    const rate = prev ? l.price / prev - 1 : null
    const bar = `${Math.max(2, (l.volume / maxVol) * 100)}%`
    return (
      <tr key={`${side}-${l.price}`} className={`ob-row ${side}${l.price === current ? ' current' : ''}`}>
        <td className="ob-vol ask-vol">
          {side === 'ask' && (
            <>
              <span className="ob-bar" style={{ width: bar }} />
              <span>{compact(l.volume)}</span>
            </>
          )}
        </td>
        <td className={`ob-price ${dir(rate)}`}>
          <button onClick={() => onPickPrice(l.price)} title="주문 가격으로">
            {price(l.price, currency)}
          </button>
          <span className="small">{pct(rate)}</span>
        </td>
        <td className="ob-vol bid-vol">
          {side === 'bid' && (
            <>
              <span className="ob-bar" style={{ width: bar }} />
              <span>{compact(l.volume)}</span>
            </>
          )}
        </td>
      </tr>
    )
  }

  const ratio = askTotal + bidTotal ? bidTotal / (askTotal + bidTotal) : 0.5
  return (
    <div className="orderbook">
      <div className="ob-balance" aria-label={`매수 잔량 비율 ${(ratio * 100).toFixed(0)}%`}>
        <span className="down small">매도 잔량 {compact(askTotal)}</span>
        <div className="ob-balance-bar">
          <i className="ask" style={{ width: `${(1 - ratio) * 100}%` }} />
          <i className="bid" style={{ width: `${ratio * 100}%` }} />
        </div>
        <span className="up small">매수 잔량 {compact(bidTotal)}</span>
      </div>
      <table className="ob-table">
        <tbody>
          {asks.map((a) => row(a, 'ask'))}
          {bids.map((b) => row(b, 'bid'))}
        </tbody>
      </table>
      <p className="muted small">
        3초마다 새로 고침{data.timestamp ? ` · ${data.timestamp.slice(11, 19)}` : ''} · 가격을 누르면 주문 가격으로 들어가요
      </p>
    </div>
  )
}
