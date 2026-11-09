/**
 * 실험실 차트 (외부 라이브러리 없이 SVG).
 * dataviz 규칙: 2px 선·라운드 조인, 끝점 r=4 + 2px 표면 링, 범례(2개 이상), 끝값 직접 라벨(텍스트는 본문 잉크),
 * 가는 실선 격자, 호버 십자선+툴팁, 표 보기. 색은 --series-1..3 (검증한 팔레트, 라이트/다크 각각).
 */
import { useState, type ReactNode } from 'react'
import { niceTicks, useWidth } from './chartUtils'
import { fmtNum, fmtPct } from './format'

const SERIES_COLORS = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)']

export interface Series {
  label: string
  points: [number, number][]
}

interface LineChartProps {
  title?: string
  series: Series[]
  height?: number
  xLabel?: string
  yMin?: number
  yMax?: number
  format?: (v: number) => string
  /** x 값 표시 (예: 날짜). 없으면 숫자 그대로 */
  xFormat?: (x: number) => string
  /** 축에 쓸 x 와 그 글자 — 없으면 고른 간격에 xFormat */
  xTicks?: number[]
  tickFormat?: (x: number) => string
}

export function LineChart({ title, series, height = 200, xLabel = '스텝', yMin, yMax, format = fmtNum, xFormat = String, xTicks: xAt, tickFormat }: LineChartProps) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const [table, setTable] = useState(false)
  const shown = series.filter((s) => s.points.length > 0).slice(0, 3)
  const all = shown.flatMap((s) => s.points)

  const header = (
    <div className="chart-head">
      {title && <span className="chart-title">{title}</span>}
      {shown.length > 1 && (
        <span className="chart-legend">
          {shown.map((s, i) => (
            <span key={s.label}>
              <i style={{ background: SERIES_COLORS[i] }} />
              {s.label}
            </span>
          ))}
        </span>
      )}
      {all.length > 0 && (
        <button className="link small" onClick={() => setTable((v) => !v)}>
          {table ? '차트' : '표'}
        </button>
      )}
    </div>
  )

  if (all.length === 0) {
    return (
      <div className="chart">
        {header}
        <div className="chart-empty muted small">아직 기록된 지표가 없어요</div>
      </div>
    )
  }

  if (table) {
    const steps = [...new Set(all.map((p) => p[0]))].sort((a, b) => a - b)
    const lookup = shown.map((s) => new Map(s.points))
    return (
      <div className="chart">
        {header}
        <div className="chart-table">
          <table>
            <thead>
              <tr>
                <th>{xLabel}</th>
                {shown.map((s) => (
                  <th key={s.label}>{s.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {steps.map((st) => (
                <tr key={st}>
                  <td>{xFormat(st)}</td>
                  {lookup.map((m, i) => (
                    <td key={i}>{m.has(st) ? format(m.get(st)!) : '–'}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    )
  }

  const m = { l: 48, r: 72, t: 8, b: 26 }
  const w = Math.max(width, 200)
  const xs = all.map((p) => p[0])
  const ys = all.map((p) => p[1])
  const x0 = Math.min(...xs)
  const x1 = Math.max(...xs)
  let lo = yMin ?? Math.min(...ys)
  let hi = yMax ?? Math.max(...ys)
  if (lo === hi) {
    lo -= 1
    hi += 1
  } else if (yMin === undefined || yMax === undefined) {
    const pad = (hi - lo) * 0.08
    if (yMin === undefined) lo -= pad
    if (yMax === undefined) hi += pad
  }
  const sx = (x: number) => m.l + (x1 === x0 ? 0.5 : (x - x0) / (x1 - x0)) * (w - m.l - m.r)
  const sy = (y: number) => m.t + (1 - (y - lo) / (hi - lo)) * (height - m.t - m.b)
  const ticks = niceTicks(lo, hi)
  const xTicks = xAt ?? niceTicks(x0, x1, Math.min(6, Math.max(1, x1 - x0))).filter((t) => Number.isInteger(t))

  // 끝값 라벨: 겹치면 아래로 밀어낸다
  const ends = shown
    .map((s, i) => {
      const last = s.points[s.points.length - 1]
      return { i, x: sx(last[0]), y: sy(last[1]), v: last[1] }
    })
    .sort((a, b) => a.y - b.y)
  for (let k = 1; k < ends.length; k++) if (ends[k].y - ends[k - 1].y < 13) ends[k].y = ends[k - 1].y + 13

  const hoverStep = hover
  const onMove = (e: React.MouseEvent<SVGRectElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const px = e.clientX - rect.left + m.l
    let best = xs[0]
    for (const x of xs) if (Math.abs(sx(x) - px) < Math.abs(sx(best) - px)) best = x
    setHover(best)
  }

  return (
    <div className="chart" ref={ref}>
      {header}
      <div className="chart-plot">
        <svg width={w} height={height} role="img" aria-label={title}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={m.l} x2={w - m.r} y1={sy(t)} y2={sy(t)} className="grid" />
              <text x={m.l - 6} y={sy(t)} className="axis" textAnchor="end" dominantBaseline="middle">
                {format(t)}
              </text>
            </g>
          ))}
          {xTicks.map((t) => (
            <text key={t} x={sx(t)} y={height - 8} className="axis" textAnchor="middle">
              {(tickFormat ?? xFormat)(t)}
            </text>
          ))}
          {shown.map((s, i) => (
            <path
              key={s.label}
              d={s.points.map((p, k) => `${k ? 'L' : 'M'}${sx(p[0])},${sy(p[1])}`).join('')}
              fill="none"
              stroke={SERIES_COLORS[i]}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ))}
          {shown.map((s, i) => {
            const last = s.points[s.points.length - 1]
            return <circle key={s.label} cx={sx(last[0])} cy={sy(last[1])} r={4} fill={SERIES_COLORS[i]} className="ring" />
          })}
          {ends.map((e) => (
            <text key={e.i} x={e.x + 8} y={e.y} className="end-label" dominantBaseline="middle">
              {format(e.v)}
            </text>
          ))}
          {hoverStep !== null && (
            <g>
              <line x1={sx(hoverStep)} x2={sx(hoverStep)} y1={m.t} y2={height - m.b} className="crosshair" />
              {shown.map((s, i) => {
                const p = s.points.find((q) => q[0] === hoverStep)
                return p ? <circle key={s.label} cx={sx(p[0])} cy={sy(p[1])} r={4} fill={SERIES_COLORS[i]} className="ring" /> : null
              })}
            </g>
          )}
          <rect
            x={m.l}
            y={m.t}
            width={Math.max(0, w - m.l - m.r)}
            height={height - m.t - m.b}
            fill="transparent"
            onMouseMove={onMove}
            onMouseLeave={() => setHover(null)}
          />
        </svg>
        {hoverStep !== null && (
          <div className="chart-tip" style={{ left: Math.min(sx(hoverStep) + 10, w - 150), top: 4 }}>
            <div className="muted small">
              {xLabel} {xFormat(hoverStep)}
            </div>
            {shown.map((s, i) => {
              const p = s.points.find((q) => q[0] === hoverStep)
              return p ? (
                <div key={s.label} className="tip-row">
                  <i style={{ background: SERIES_COLORS[i] }} />
                  {s.label} <b>{format(p[1])}</b>
                </div>
              ) : null
            })}
          </div>
        )}
      </div>
    </div>
  )
}

interface BarItem {
  label: ReactNode
  value: number | null
  display?: string
}

/** 가로 막대 (단일 계열이라 범례 없음). 값은 막대 옆 본문 잉크로 */
export function BarList({ items, max = 1, format = fmtPct }: { items: BarItem[]; max?: number; format?: (v: number) => string }) {
  return (
    <div className="barlist">
      {items.map((it, k) => (
        <div key={k} className="barlist-row">
          <span className="barlist-label">{it.label}</span>
          <span className="barlist-track">
            {it.value !== null && (
              <span className="barlist-fill" style={{ width: `${Math.max(0, Math.min(1, it.value / max)) * 100}%` }} />
            )}
          </span>
          <span className="barlist-value">{it.value === null ? '–' : (it.display ?? format(it.value))}</span>
        </div>
      ))}
    </div>
  )
}

export function StatTile({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="stat-tile">
      <span className="muted small">{label}</span>
      <strong>{value}</strong>
      {hint && <span className="muted small">{hint}</span>}
    </div>
  )
}
