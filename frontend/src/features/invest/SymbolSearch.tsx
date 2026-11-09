/** 종목·ETF 검색 자동완성 — 머리글 종목 선택, 관심 그룹에 담기 */
import { useEffect, useRef, useState } from 'react'
import { investApi, type SearchHit } from './api'

export default function SymbolSearch({ onPick, placeholder = '종목명·코드 검색', marked, keepOpen = false }: {
  onPick: (hit: SearchHit) => void
  placeholder?: string
  marked?: Set<string>  // 이미 담긴 종목 (✓ 표시)
  keepOpen?: boolean    // 고른 뒤에도 검색어·목록 유지 (여러 개 연달아 담기)
}) {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<SearchHit[]>([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const boxRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!q.trim()) return
    let alive = true
    const id = window.setTimeout(() => {
      investApi
        .search(q)
        .then((r) => {
          if (!alive) return
          setHits(r)
          setActive(0)
          setError(null)
        })
        .catch((e: Error) => alive && setError(e.message))
    }, 180)
    return () => {
      alive = false
      window.clearTimeout(id)
    }
  }, [q])

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  const shown = q.trim() ? hits : []
  const pick = (h: SearchHit) => {
    onPick(h)
    if (keepOpen) return
    setQ('')
    setOpen(false)
  }

  return (
    <div className="symbol-search" ref={boxRef}>
      <input
        type="search"
        placeholder={placeholder}
        value={q}
        aria-label="종목 검색"
        role="combobox"
        aria-expanded={open && shown.length > 0}
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setQ(e.target.value)
          setOpen(true)
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setActive((a) => Math.min(a + 1, shown.length - 1))
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setActive((a) => Math.max(a - 1, 0))
          } else if (e.key === 'Enter' && shown[active]) {
            pick(shown[active])
          } else if (e.key === 'Escape') {
            setOpen(false)
          }
        }}
      />
      {open && (shown.length > 0 || error) && (
        <ul className="search-results" role="listbox">
          {error && <li className="error small">{error}</li>}
          {shown.map((h, i) => (
            <li key={h.symbol} role="option" aria-selected={i === active} className={i === active ? 'on' : ''}
              onMouseDown={(e) => {
                e.preventDefault()
                pick(h)
              }}
              onMouseEnter={() => setActive(i)}>
              <span>
                {marked?.has(h.symbol) && <span className="ok-text">✓ </span>}
                {h.name}
                {h.security_type === 'ETF' && <span className="chip etf">ETF</span>}
              </span>
              <span className="muted small mono">{h.symbol}</span>
              <span className="chip">{h.market}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
