/**
 * 캔들 차트 (SVG, 외부 라이브러리 없음) — 캔들 + 이동평균선 + 거래량 + 현재가 선 + 구간 최고·최저 + 십자선 툴팁 + 표 보기.
 * 색: 상승 --up(빨강) / 하락 --down(파랑) (국내 관례), 이동평균은 --series-1..3. 글자는 본문 잉크.
 */
import { useMemo, useState } from 'react'
import { niceTicks, useWidth } from '../../shared/chartUtils'
import type { Candle } from './api'
import { compact, pct, price } from './format'

const MA_COLORS = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)']

function sma(values: number[], n: number): (number | null)[] {
  const out: (number | null)[] = []
  let sum = 0
  values.forEach((v, i) => {
    sum += v
    if (i >= n) sum -= values[i - n]
    out.push(i >= n - 1 ? sum / n : null)
  })
  return out
}

interface Props {
  candles: Candle[]
  visible: number
  interval: '1d' | '1m'
  currency: string
  prevClose?: number | null
  mas?: number[]
  height?: number
}

/** 폭을 재는 바깥 상자는 항상 그대로 두고(ResizeObserver 대상 고정), 안쪽은 데이터가 있을 때만 그린다 */
export default function CandleChart(props: Props) {
  const [ref, width] = useWidth<HTMLDivElement>()
  return (
    <div ref={ref} className="candle-chart">
      {!props.candles.length ? (
        <div className="chart-empty muted small">차트 데이터가 없습니다</div>
      ) : (
        width > 0 && <Plot {...props} width={width} />
      )}
    </div>
  )
}

function Plot({ candles, visible, interval, currency, prevClose, mas = [5, 20, 60], height = 340, width }: Props & { width: number }) {
  const [hover, setHover] = useState<{ i: number; y: number } | null>(null)
  const [table, setTable] = useState(false)

  const maSeries = useMemo(() => {
    const closes = candles.map((c) => c.c)
    return mas.map((n) => ({ n, values: sma(closes, n) }))
  }, [candles, mas])

  const start = Math.max(0, candles.length - visible)
  const view = candles.slice(start)
  const n = view.length
  const padL = 6
  const padR = 62
  const axisH = 20
  const priceH = Math.round(height * 0.72)
  const gap = 10
  const volH = height - priceH - gap - axisH
  const plotW = Math.max(width - padL - padR, 10)
  const step = plotW / n
  const bodyW = Math.max(1, Math.min(step * 0.62, 14))
  const x = (i: number) => padL + (i + 0.5) * step

  const visibleMa = maSeries.map((s) => ({ ...s, values: s.values.slice(start) }))
  const lows = view.map((c) => c.l)
  const highs = view.map((c) => c.h)
  visibleMa.forEach((s) =>
    s.values.forEach((v) => {
      if (v !== null) {
        lows.push(v)
        highs.push(v)
      }
    }),
  )
  let yMin = Math.min(...lows)
  let yMax = Math.max(...highs)
  const pad = (yMax - yMin || yMax * 0.01 || 1) * 0.06
  yMin -= pad
  yMax += pad
  const y = (v: number) => 4 + (1 - (v - yMin) / (yMax - yMin)) * (priceH - 8)
  const vMax = Math.max(...view.map((c) => c.v), 1)
  const volTop = priceH + gap
  const vy = (v: number) => volTop + volH - (v / vMax) * volH

  const ticks = niceTicks(yMin, yMax, 4)
  const last = view[n - 1]
  const lastRef = prevClose ?? (n > 1 ? view[n - 2].c : last.o)
  const lastUp = last.c >= lastRef
  let hiIdx = 0
  let loIdx = 0
  view.forEach((c, i) => {
    if (c.h > view[hiIdx].h) hiIdx = i
    if (c.l < view[loIdx].l) loIdx = i
  })

  const span = view.length > 1 ? Date.parse(view[n - 1].t) - Date.parse(view[0].t) : 0
  const label = (t: string) =>
    interval === '1m' ? t.slice(11, 16) : span > 400 * 86400e3 ? `${t.slice(2, 4)}.${t.slice(5, 7)}` : `${t.slice(5, 7)}/${t.slice(8, 10)}`
  const xTickIdx = Array.from(new Set(Array.from({ length: 5 }, (_, k) => Math.round((k * (n - 1)) / 4))))

  const path = (values: (number | null)[]) => {
    let d = ''
    values.forEach((v, i) => {
      if (v === null) return
      d += `${d ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`
    })
    return d
  }

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const px = e.clientX - rect.left
    const i = Math.min(n - 1, Math.max(0, Math.floor((px - padL) / step)))
    setHover({ i, y: e.clientY - rect.top })
  }

  const h = hover ? view[hover.i] : null
  const hPrev = hover ? (hover.i > 0 ? view[hover.i - 1].c : start > 0 ? candles[start - 1].c : null) : null
  const hoverPrice = hover && hover.y <= priceH ? yMin + (1 - (hover.y - 4) / (priceH - 8)) * (yMax - yMin) : null
  const hiLabelEnd = hiIdx > n / 2
  const loLabelEnd = loIdx > n / 2

  return (
    <>
      <div className="chart-head">
        <span className="chart-legend">
          {visibleMa.map((s, k) => {
            const v = s.values[n - 1]
            return v === null ? null : (
              <span key={s.n}>
                <i style={{ background: MA_COLORS[k] }} />
                MA{s.n} {price(v, currency)}
              </span>
            )
          })}
        </span>
        <button className="link" onClick={() => setTable((t) => !t)}>
          {table ? '차트' : '표'}
        </button>
      </div>
      {table ? (
        <div className="chart-table">
          <table className="data-table">
            <thead>
              <tr>
                <th>{interval === '1m' ? '시각' : '날짜'}</th>
                <th>시가</th>
                <th>고가</th>
                <th>저가</th>
                <th>종가</th>
                <th>거래량</th>
              </tr>
            </thead>
            <tbody>
              {[...view].reverse().slice(0, 60).map((c) => (
                <tr key={c.t}>
                  <td>{interval === '1m' ? c.t.slice(11, 16) : c.t.slice(0, 10)}</td>
                  <td>{price(c.o, currency)}</td>
                  <td>{price(c.h, currency)}</td>
                  <td>{price(c.l, currency)}</td>
                  <td className={c.c >= c.o ? 'up' : 'down'}>{price(c.c, currency)}</td>
                  <td>{compact(c.v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="chart-plot">
          {
            <svg width={width} height={height} role="img"
              aria-label={`${interval === '1m' ? '분봉' : '일봉'} ${n}개, 마지막 ${price(last.c, currency)}`}>
              {ticks.map((t) => (
                <g key={t}>
                  <line x1={padL} x2={padL + plotW} y1={y(t)} y2={y(t)} className="grid" />
                  <text x={padL + plotW + 6} y={y(t) + 4} className="axis">
                    {price(t, currency)}
                  </text>
                </g>
              ))}
              <line x1={padL} x2={padL + plotW} y1={volTop + volH} y2={volTop + volH} className="grid" />
              {view.map((c, i) => {
                const up = c.c >= c.o
                const top = y(Math.max(c.o, c.c))
                const bh = Math.max(1, Math.abs(y(c.o) - y(c.c)))
                return (
                  <g key={c.t} className={up ? 'candle up' : 'candle down'}>
                    <line x1={x(i)} x2={x(i)} y1={y(c.h)} y2={y(c.l)} />
                    <rect x={x(i) - bodyW / 2} y={top} width={bodyW} height={bh} rx={bodyW > 4 ? 1 : 0} />
                    <rect className="vol" x={x(i) - bodyW / 2} y={vy(c.v)} width={bodyW} height={volTop + volH - vy(c.v)} />
                  </g>
                )
              })}
              {visibleMa.map((s, k) => (
                <path key={s.n} d={path(s.values)} fill="none" stroke={MA_COLORS[k]} strokeWidth={1.5} strokeLinejoin="round" />
              ))}
              <text x={x(hiIdx)} y={y(view[hiIdx].h) - 6} className="axis hi-lo" textAnchor={hiLabelEnd ? 'end' : 'start'}>
                최고 {price(view[hiIdx].h, currency)}
              </text>
              <text x={x(loIdx)} y={y(view[loIdx].l) + 14} className="axis hi-lo" textAnchor={loLabelEnd ? 'end' : 'start'}>
                최저 {price(view[loIdx].l, currency)}
              </text>
              <line x1={padL} x2={padL + plotW} y1={y(last.c)} y2={y(last.c)} className={`last-line ${lastUp ? 'up' : 'down'}`} />
              <g className={`price-tag ${lastUp ? 'up' : 'down'}`}>
                <rect x={padL + plotW + 2} y={y(last.c) - 9} width={padR - 4} height={18} rx={4} />
                <text x={padL + plotW + 6} y={y(last.c) + 4}>
                  {price(last.c, currency)}
                </text>
              </g>
              {xTickIdx.map((i) => (
                <text key={i} x={x(i)} y={height - 5} className="axis" textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}>
                  {label(view[i].t)}
                </text>
              ))}
              {hover && (
                <g className="crosshair">
                  <line x1={x(hover.i)} x2={x(hover.i)} y1={0} y2={volTop + volH} />
                  {hoverPrice !== null && (
                    <>
                      <line x1={padL} x2={padL + plotW} y1={hover.y} y2={hover.y} />
                      <rect x={padL + plotW + 2} y={hover.y - 9} width={padR - 4} height={18} rx={4} className="cross-tag" />
                      <text x={padL + plotW + 6} y={hover.y + 4} className="cross-text">
                        {price(hoverPrice, currency)}
                      </text>
                    </>
                  )}
                </g>
              )}
              <rect x={padL} y={0} width={plotW} height={volTop + volH} fill="transparent" style={{ touchAction: 'pan-y' }}
                onPointerMove={onMove} onPointerDown={onMove} onPointerLeave={() => setHover(null)} />
            </svg>
          }
          {h && hover && (
            <div className="chart-tip candle-tip" style={hover.i > n / 2 ? { left: 8 } : { right: padR + 8 }}>
              <div className="muted small">{interval === '1m' ? h.t.slice(11, 16) : h.t.slice(0, 10)}</div>
              <div className="tip-grid">
                <span>시가</span><b>{price(h.o, currency)}</b>
                <span>고가</span><b className="up">{price(h.h, currency)}</b>
                <span>저가</span><b className="down">{price(h.l, currency)}</b>
                <span>종가</span><b>{price(h.c, currency)}</b>
                {hPrev !== null && (
                  <>
                    <span>등락</span>
                    <b className={h.c >= hPrev ? 'up' : 'down'}>{pct(h.c / hPrev - 1)}</b>
                  </>
                )}
                <span>거래량</span><b>{compact(h.v)}</b>
                {visibleMa.map((s) =>
                  s.values[hover.i] === null ? null : (
                    <span key={s.n} style={{ display: 'contents' }}>
                      <span>MA{s.n}</span>
                      <b>{price(s.values[hover.i], currency)}</b>
                    </span>
                  ),
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </>
  )
}
