import { useState } from 'react'
import { useLoad } from '../../../shared/useLoad'
import { investApi, type StockSnapshot } from '../api'
import CandleChart from '../CandleChart'
import { compact, price, useTicker } from '../format'

// 이동평균(최대 60일)을 처음부터 그리려고 보이는 봉보다 60개 더 받는다
const RANGES = [
  { key: '1D', label: '1일', interval: '1m', count: 400, visible: 400, mas: [5, 20] },
  { key: '3M', label: '3개월', interval: '1d', count: 123, visible: 63, mas: [5, 20, 60] },
  { key: '6M', label: '6개월', interval: '1d', count: 186, visible: 126, mas: [5, 20, 60] },
  { key: '1Y', label: '1년', interval: '1d', count: 310, visible: 250, mas: [5, 20, 60] },
  { key: '2Y', label: '2년', interval: '1d', count: 560, visible: 500, mas: [20, 60] },
] as const

export default function ChartTab({ symbol, snap }: { symbol: string; snap: StockSnapshot | null }) {
  const [range, setRange] = useState<(typeof RANGES)[number]['key']>('6M')
  const r = RANGES.find((x) => x.key === range)!
  const tick = useTicker(r.interval === '1m' ? 20_000 : 60_000)
  const { data, error } = useLoad(() => investApi.candles(symbol, r.interval, r.count), [symbol, range, tick])
  const currency = snap?.currency ?? 'KRW'

  return (
    <div className="lens-chart">
      <div className="toolbar">
        <div className="segmented small" role="tablist" aria-label="차트 기간">
          {RANGES.map((x) => (
            <button key={x.key} className={range === x.key ? 'on' : ''} onClick={() => setRange(x.key)}>
              {x.label}
            </button>
          ))}
        </div>
        <span className="muted small">{r.interval === '1m' ? '분봉' : '일봉'} · 수정주가</span>
      </div>
      {error && <p className="error small">{error}</p>}
      {data && (
        <CandleChart candles={data.candles} visible={r.visible} interval={r.interval} currency={currency}
          prevClose={r.interval === '1m' ? snap?.prev_close : null} mas={[...r.mas]} />
      )}
      {snap && (
        <dl className="quote-grid">
          <dt>시가</dt>
          <dd>{price(snap.open, currency)}</dd>
          <dt>고가</dt>
          <dd className="up">{price(snap.high, currency)}</dd>
          <dt>저가</dt>
          <dd className="down">{price(snap.low, currency)}</dd>
          <dt>거래량</dt>
          <dd>{compact(snap.volume)}</dd>
          <dt>상한가</dt>
          <dd className="up">{price(snap.upper_limit, currency)}</dd>
          <dt>하한가</dt>
          <dd className="down">{price(snap.lower_limit, currency)}</dd>
        </dl>
      )}
    </div>
  )
}
