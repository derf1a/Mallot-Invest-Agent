/** 보유 목록 — 계좌로 거르고 평가금액·손익·수익률·이름으로 정렬. 줄을 누르면 투자 화면에서 그 종목을 연다. */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Position } from '../api'
import { dir, pct, price, qty, ratio, signed, won } from '../format'
import { sortPositions, where, type SortKey } from './model'

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'name', label: '종목' },
  { key: 'value', label: '평가금액' },
  { key: 'pnl', label: '손익' },
  { key: 'rate', label: '수익률' },
]

export default function HoldingsGrid({ positions, colors }: { positions: Position[]; colors: Map<string, string> }) {
  const navigate = useNavigate()
  const accounts = [...new Set(positions.map(where))]
  const [account, setAccount] = useState<string>('all')
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: 'value', desc: true })
  const rows = sortPositions(account === 'all' ? positions : positions.filter((p) => where(p) === account), sort.key, sort.desc)
  const sum = rows.reduce((a, p) => a + p.value_krw, 0)

  const toggle = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, desc: !s.desc } : { key, desc: key !== 'name' }))

  return (
    <div className="holdings-grid">
      <div className="section-head">
        <div className="watch-groups" role="tablist" aria-label="계좌">
          {['all', ...accounts].map((a) => (
            <button key={a} role="tab" aria-selected={account === a} className={`group-chip${account === a ? ' on' : ''}`} onClick={() => setAccount(a)}>
              {a === 'all' ? '전체' : a}{' '}
              <span className="muted small">{a === 'all' ? positions.length : positions.filter((p) => where(p) === a).length}</span>
            </button>
          ))}
        </div>
        <span className="muted small">{rows.length}종목 · {won(sum)}</span>
      </div>

      <div className="hg-table" role="table" aria-label="보유 종목">
        <div className="hg-row hg-head" role="row">
          {COLUMNS.map((c) => (
            <button key={c.key} role="columnheader" className={`hg-sort hg-${c.key}${sort.key === c.key ? ' on' : ''}`}
              aria-sort={sort.key === c.key ? (sort.desc ? 'descending' : 'ascending') : 'none'} onClick={() => toggle(c.key)}>
              {c.label}{sort.key === c.key ? (sort.desc ? ' ↓' : ' ↑') : ''}
            </button>
          ))}
          <span role="columnheader" className="hg-weight">비중</span>
        </div>
        {rows.map((p) => (
          <button key={`${p.where}:${p.symbol}`} role="row" className="hg-row" onClick={() => navigate(`/invest?s=${p.symbol}`)}
            title={`${p.name} — 투자 화면에서 열기`}>
            <span role="cell" className="hg-name">
              <i className="swatch" style={{ background: colors.get(p.symbol) ?? 'var(--series-other)' }} aria-hidden />
              <span className="hg-name-text">
                <b>{p.name || p.symbol}{p.security_type === 'ETF' && <span className="chip etf">ETF</span>}</b>
                <span className="muted small">
                  {where(p)} · {qty(p.quantity)}주 · 평단 {p.avg_price ? price(p.avg_price, p.currency) : '–'}
                  {p.currency !== 'KRW' && ` ${p.currency}`}
                </span>
              </span>
            </span>
            <span role="cell" className="hg-value num">
              <b>{won(p.value_krw)}</b>
              <span className="muted small">
                현재 {price(p.last_price, p.currency)}{p.priced === false ? ' (평단 기준)' : ''}
              </span>
            </span>
            <span role="cell" className={`hg-pnl num ${dir(p.pnl_krw)}`}>{p.pnl_krw === null ? '–' : signed(p.pnl_krw)}</span>
            <span role="cell" className={`hg-rate num ${dir(p.pnl_rate)}`}>{pct(p.pnl_rate)}</span>
            <span role="cell" className="hg-weight">
              <span className="num small">{ratio(p.weight)}</span>
              <span className="hg-weight-bar" aria-hidden>
                <i style={{ width: `${Math.min(100, (p.weight ?? 0) * 100)}%`, background: colors.get(p.symbol) ?? 'var(--series-other)' }} />
              </span>
            </span>
          </button>
        ))}
        {!rows.length && <p className="muted small">보유 종목이 없어요.</p>}
      </div>
    </div>
  )
}
