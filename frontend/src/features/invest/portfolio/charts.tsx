/**
 * 포트폴리오 그림 — 배분(가로 100% 막대 + 범례), 종목별 손익(가운데 0 기준 양쪽 막대), 비율 막대(meter).
 * 색은 정체성(종목·계좌)에만 쓰고 글자는 글자색. 범례·숫자가 늘 같이 보이고, 막대에 올리면 자세한 값이 뜬다.
 */
import type { ReactNode } from 'react'
import { useTip } from '../../../shared/ui/useTip'
import { pct, ratio, signed, won } from '../format'
import { CASH, type PnlRow, type Slice } from './model'

export function AllocationBar({ slices }: { slices: Slice[] }) {
  const total = slices.reduce((a, s) => a + s.value, 0)
  const { box, show, hide, node } = useTip()
  if (!total) return <p className="muted small">아직 자산이 없어요.</p>
  const body = (s: Slice) => (
    <>
      <b>{s.label}</b>
      <span>{won(s.value)} · {ratio(s.value / total)}</span>
      {s.note && <span className="muted">{s.note}</span>}
    </>
  )
  return (
    <div className="alloc">
      <div className="alloc-bar" ref={box} role="img" aria-label={slices.map((s) => `${s.label} ${ratio(s.value / total)}`).join(', ')}
        onMouseLeave={hide}>
        {slices.map((s) => (
          <div key={s.key} className={`alloc-seg${s.key === CASH ? ' cash' : ''}`}
            style={{ flexGrow: s.value, background: s.key === CASH ? undefined : s.color }}
            onMouseMove={(e) => show(e, body(s))} />
        ))}
        {node}
      </div>
      <ul className="alloc-legend">
        {slices.map((s) => (
          <li key={s.key} title={s.note}>
            <i className={`swatch${s.key === CASH ? ' cash' : ''}`} style={s.key === CASH ? undefined : { background: s.color }} />
            <span className="alloc-name">{s.label}</span>
            <b className="num">{ratio(s.value / total)}</b>
            <span className="num muted">{won(s.value)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** 종목별 평가 손익 — 0을 가운데 두고 이익은 오른쪽(빨강), 손실은 왼쪽(파랑). 길이는 가장 큰 손익 기준. */
export function PnlBars({ rows, limit = 8 }: { rows: PnlRow[]; limit?: number }) {
  const { box, show, hide, node } = useTip()
  if (!rows.length) return <p className="muted small">평단을 아는 보유가 없어 손익을 계산할 수 없어요.</p>
  // 손익이 큰 쪽(이익·손실 모두)부터 limit 개
  const shown = [...rows].sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl)).slice(0, limit).sort((a, b) => b.pnl - a.pnl)
  const max = Math.max(...shown.map((r) => Math.abs(r.pnl)), 1)
  return (
    <div className="pnl-chart" ref={box} onMouseLeave={hide}>
      <ul className="pnl-bars">
        {shown.map((r) => {
          const w = (Math.abs(r.pnl) / max) * 50
          return (
            <li key={r.symbol} onMouseMove={(e) => show(e, (
              <>
                <b>{r.name}</b>
                <span>손익 {signed(r.pnl)}원 ({pct(r.rate)})</span>
                <span className="muted">원금 {won(r.cost)}</span>
              </>
            ))}>
              <span className="pnl-name">{r.name}</span>
              <span className="pnl-track" aria-hidden>
                <i className={r.pnl >= 0 ? 'gain' : 'loss'} style={r.pnl >= 0 ? { left: '50%', width: `${w}%` } : { right: '50%', width: `${w}%` }} />
              </span>
              <span className={`num pnl-value ${r.pnl > 0 ? 'up' : r.pnl < 0 ? 'down' : ''}`}>
                {signed(r.pnl)} <span className="small">({pct(r.rate, 1)})</span>
              </span>
            </li>
          )
        })}
      </ul>
      {rows.length > shown.length && <p className="muted small">손익이 작은 {rows.length - shown.length}종목은 아래 보유 목록에서 보세요.</p>}
      {node}
    </div>
  )
}

/** 비율 하나를 막대로 — 주의선(mark)을 넘으면 경고 문구를 붙인다 (색만으로 알리지 않는다) */
export function Meter({ label, value, mark, markLabel, hint }: {
  label: string
  value: number | null
  mark?: number
  markLabel?: string
  hint?: ReactNode
}) {
  const over = value !== null && mark !== undefined && value > mark
  return (
    <div className="meter">
      <div className="meter-head">
        <span>{label}</span>
        <b className="num">{ratio(value)}</b>
      </div>
      <div className="meter-track" role="meter" aria-valuemin={0} aria-valuemax={1} aria-valuenow={value ?? 0} aria-label={label}>
        <i className={over ? 'over' : ''} style={{ width: `${Math.min(100, (value ?? 0) * 100)}%` }} />
        {mark !== undefined && <span className="meter-mark" style={{ left: `${mark * 100}%` }} title={markLabel} />}
      </div>
      {(over || hint) && <span className={`small ${over ? 'warn-text' : 'muted'}`}>{over ? `⚠ ${markLabel}` : hint}</span>}
    </div>
  )
}
