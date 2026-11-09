/**
 * 종목 찾기 — 검색창 하나로 종목을 고른다. 글자를 넣으면 종목·ETF 검색, 비워 두면 시장 둘러보기:
 * 관심(그룹 관리 포함) · 보유 · 랭킹(거래대금·거래량·상승·하락) · 최근 본 종목. ↑↓·Enter 로 고르고 Esc 로 닫는다.
 * 토스 앱의 관심 그룹은 Open API 로 받을 수 없어 관심 그룹은 앱 안에서 관리한다.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useLoad } from '../../shared/useLoad'
import { investApi, type SearchHit, type WatchGroup } from './api'
import { compact, dir, pct, price, qty } from './format'
import { load, RECENT, save, type Recent } from './recent'

type Browse = 'watch' | 'held' | 'rank' | 'recent'
type RankKind = 'amount' | 'volume' | 'gainers' | 'losers'

const BROWSE: { key: Browse; label: string }[] = [
  { key: 'watch', label: '관심' },
  { key: 'held', label: '보유' },
  { key: 'rank', label: '랭킹' },
  { key: 'recent', label: '최근' },
]
const RANKS: { key: RankKind; label: string }[] = [
  { key: 'amount', label: '거래대금' },
  { key: 'volume', label: '거래량' },
  { key: 'gainers', label: '상승' },
  { key: 'losers', label: '하락' },
]

interface Row {
  symbol: string
  name: string
  etf?: boolean
  lead?: ReactNode  // 앞쪽 (순위)
  meta?: ReactNode  // 이름 아래 작은 줄
  right?: ReactNode // 오른쪽 숫자
  action?: ReactNode
}

export default function StockExplorer({ current, currentName, watchVersion, onPick, onWatchChanged }: {
  current: string
  currentName?: string
  watchVersion: number
  onPick: (symbol: string) => void
  onWatchChanged: () => void // 머리글 ☆ 갱신
}) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState<Browse>(() => load('invest.browse', 'watch'))
  const [rank, setRank] = useState<RankKind>('amount')
  const [groupId, setGroupId] = useState<number | null>(() => load('invest.watchGroup', null))
  const [active, setActive] = useState(0)
  const [hits, setHits] = useState<SearchHit[]>([])
  const [searchError, setSearchError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [local, setLocal] = useState(0)
  const boxRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const searching = !!q.trim()

  // 패널이 열렸을 때 보는 목록만 불러온다
  const { data: groups, error: groupsError } = useLoad(
    () => (open ? investApi.watchGroups() : Promise.resolve(null)), [open, watchVersion, local])
  const { data: account, error: heldError } = useLoad(
    () => (open && !searching && tab === 'held' ? investApi.account() : Promise.resolve(null)), [open, searching, tab])
  const { data: ranks, error: rankError } = useLoad(
    () => (open && !searching && tab === 'rank' ? investApi.rankings(rank) : Promise.resolve(null)), [open, searching, tab, rank])
  const recent = open ? load<Recent[]>(RECENT, []) : []

  useEffect(() => {
    if (!searching) return
    let alive = true
    const id = window.setTimeout(() => {
      investApi.search(q)
        .then((r) => {
          if (!alive) return
          setHits(r)
          setActive(0)
          setSearchError(null)
        })
        .catch((e: Error) => alive && setSearchError(e.message))
    }, 180)
    return () => {
      alive = false
      window.clearTimeout(id)
    }
  }, [q, searching])

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  // 키보드로 고른 줄이 보이도록
  useEffect(() => {
    listRef.current?.querySelector('li.on')?.scrollIntoView({ block: 'nearest' })
  }, [active])

  const group: WatchGroup | undefined = groups?.find((g) => g.id === groupId) ?? groups?.[0]
  const inGroup = new Set(group?.items.map((i) => i.symbol) ?? [])

  const act = async (fn: () => Promise<unknown>) => {
    setMessage(null)
    try {
      await fn()
      setLocal((v) => v + 1)
      onWatchChanged()
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e))
    }
  }
  const chooseGroup = (id: number) => {
    setGroupId(id)
    save('invest.watchGroup', id)
    setActive(0)
  }
  const toggleInGroup = (symbol: string, name: string, extra: { market?: string; security_type?: string } = {}) => {
    if (!group) return
    act(() => (inGroup.has(symbol) ? investApi.removeWatch(symbol, group.id) : investApi.addWatch(symbol, { name, ...extra, group_id: group.id })))
  }
  const star = (symbol: string, name: string, extra?: { market?: string; security_type?: string }) =>
    group && (
      <button className={`star small${inGroup.has(symbol) ? ' on' : ''}`} title={inGroup.has(symbol) ? `'${group.name}'에서 빼기` : `'${group.name}'에 담기`}
        aria-label={inGroup.has(symbol) ? `${name} '${group.name}'에서 빼기` : `${name} '${group.name}'에 담기`}
        onMouseDown={(e) => {
          e.preventDefault()
          e.stopPropagation()
          toggleInGroup(symbol, name, extra)
        }}>
        {inGroup.has(symbol) ? '★' : '☆'}
      </button>
    )

  let rows: Row[] = []
  let error: string | null = null
  let empty = ''
  if (searching) {
    rows = hits.map((h) => ({
      symbol: h.symbol, name: h.name, etf: h.security_type === 'ETF',
      meta: <><span className="mono">{h.symbol}</span> · {h.market}</>,
      action: star(h.symbol, h.name, { market: h.market, security_type: h.security_type ?? '' }),
    }))
    error = searchError
    empty = '검색 결과가 없어요'
  } else if (tab === 'watch') {
    rows = (group?.items ?? []).map((w) => ({
      symbol: w.symbol, name: w.name || w.symbol, etf: w.security_type === 'ETF',
      meta: <span className="mono">{w.symbol}</span>,
      right: <span className="num">{price(w.price)}</span>,
      action: (
        <button className="icon-button" aria-label={`${w.name} 이 그룹에서 빼기`} title="이 그룹에서 빼기"
          onMouseDown={(e) => {
            e.preventDefault()
            e.stopPropagation()
            act(() => investApi.removeWatch(w.symbol, group!.id))
          }}>
          ✕
        </button>
      ),
    }))
    error = groupsError
    empty = '비어 있어요. 종목을 검색해 ☆를 누르거나, 아래에서 지금 종목을 담으세요.'
  } else if (tab === 'held') {
    rows = (account?.holdings ?? []).map((h) => ({
      symbol: h.symbol, name: h.name || h.symbol,
      meta: <>{qty(h.quantity)}주 · 평단 {price(h.avg_price, h.currency)}</>,
      right: <span className={`num ${dir(h.pnl_rate)}`}>{pct(h.pnl_rate)}</span>,
    }))
    error = heldError
    empty = '토스증권 계좌에 보유 종목이 없어요'
  } else if (tab === 'rank') {
    rows = (ranks ?? []).map((r) => ({
      symbol: r.symbol, name: r.name || r.symbol,
      lead: <span className="rank muted">{r.rank}</span>,
      meta: rank === 'volume' ? `거래량 ${compact(r.volume)}` : `거래대금 ${compact(r.amount)}원`,
      right: (
        <span className="num explorer-quote">
          <span>{price(r.price, r.currency)}</span>
          <span className={dir(r.change_rate)}>{pct(r.change_rate)}</span>
        </span>
      ),
    }))
    error = rankError
    empty = '랭킹을 불러오는 중…'
  } else {
    rows = recent.map((r) => ({ symbol: r.symbol, name: r.name || r.symbol, meta: <span className="mono">{r.symbol}</span> }))
    empty = '아직 본 종목이 없어요'
  }

  const pick = (symbol: string) => {
    onPick(symbol)
    setQ('')
    setOpen(false)
  }
  const switchTab = (t: Browse) => {
    setTab(t)
    save('invest.browse', t)
    setActive(0)
  }
  const groupAction = (kind: 'new' | 'rename' | 'delete') => {
    if (kind === 'new') {
      const name = window.prompt('새 관심 그룹 이름')
      if (name?.trim()) act(async () => {
        const next = await investApi.createGroup(name.trim())
        chooseGroup(next[next.length - 1].id)
      })
    } else if (group && kind === 'rename') {
      const name = window.prompt('그룹 이름', group.name)
      if (name?.trim() && name.trim() !== group.name) act(() => investApi.renameGroup(group.id, name.trim()))
    } else if (group && window.confirm(`'${group.name}' 그룹을 지울까요? 그룹 안 종목 ${group.items.length}개도 이 그룹에서 빠져요.`)) {
      act(() => investApi.deleteGroup(group.id))
    }
  }

  return (
    <div className="explorer" ref={boxRef}>
      <input
        type="search"
        placeholder="종목명·코드 검색 · 비워 두면 관심·보유·랭킹"
        value={q}
        aria-label="종목 찾기"
        role="combobox"
        aria-expanded={open}
        aria-controls="explorer-list"
        onFocus={() => setOpen(true)}
        onClick={() => setOpen(true)}
        onChange={(e) => {
          setQ(e.target.value)
          setOpen(true)
          setActive(0)
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setOpen(true)
            setActive((a) => Math.min(a + 1, rows.length - 1))
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setActive((a) => Math.max(a - 1, 0))
          } else if (e.key === 'Enter' && open && rows[active]) {
            pick(rows[active].symbol)
          } else if (e.key === 'Escape') {
            setOpen(false)
          }
        }}
      />
      {open && (
        <div className="explorer-panel">
          {searching ? (
            <div className="explorer-head small muted">
              검색 결과 {hits.length}개{group && ` · ☆는 '${group.name}'에 담기`}
            </div>
          ) : (
            <>
              <div className="explorer-tabs" role="tablist" aria-label="둘러보기">
                {BROWSE.map((t) => (
                  <button key={t.key} role="tab" aria-selected={tab === t.key} className={tab === t.key ? 'on' : ''} onClick={() => switchTab(t.key)}>
                    {t.label}
                  </button>
                ))}
              </div>
              {tab === 'rank' && (
                <div className="segmented small explorer-sub">
                  {RANKS.map((k) => (
                    <button key={k.key} className={rank === k.key ? 'on' : ''} onClick={() => setRank(k.key)}>
                      {k.label}
                    </button>
                  ))}
                </div>
              )}
              {tab === 'watch' && groups && (
                <div className="watch-groups explorer-sub" role="tablist" aria-label="관심 그룹">
                  {groups.map((g) => (
                    <button key={g.id} role="tab" aria-selected={g.id === group?.id} className={`group-chip${g.id === group?.id ? ' on' : ''}`}
                      onClick={() => chooseGroup(g.id)}>
                      {g.name} <span className="muted small">{g.items.length}</span>
                    </button>
                  ))}
                  <button className="group-chip add" onClick={() => groupAction('new')}>+ 그룹</button>
                </div>
              )}
            </>
          )}

          <ul id="explorer-list" className="explorer-list scroll-y" role="listbox" ref={listRef}>
            {error && <li className="error small explorer-note">{error}</li>}
            {!error && rows.length === 0 && <li className="muted small explorer-note">{empty}</li>}
            {rows.map((r, i) => (
              <li key={r.symbol} role="option" aria-selected={i === active}
                className={`${i === active ? 'on' : ''}${r.symbol === current ? ' current' : ''}`}
                onMouseDown={(e) => {
                  e.preventDefault()
                  pick(r.symbol)
                }}
                onMouseEnter={() => setActive(i)}>
                {r.lead}
                <span className="explorer-name">
                  <span className="explorer-title">
                    {r.name}
                    {r.etf && <span className="chip etf">ETF</span>}
                    {r.symbol === current && <span className="chip">보는 중</span>}
                  </span>
                  {r.meta && <span className="muted small">{r.meta}</span>}
                </span>
                {r.right}
                {r.action}
              </li>
            ))}
          </ul>

          {!searching && tab === 'watch' && group && (
            <div className="explorer-foot small">
              {!inGroup.has(current) && (
                <button className="link" onClick={() => toggleInGroup(current, currentName ?? current)}>
                  ＋ {currentName ?? current} 담기
                </button>
              )}
              <span className="explorer-foot-actions">
                <button className="link" onClick={() => groupAction('rename')}>이름 변경</button>
                <button className="link" onClick={() => groupAction('delete')} disabled={(groups?.length ?? 0) <= 1}>그룹 삭제</button>
              </span>
            </div>
          )}
          {message && <p className="error small explorer-note">{message}</p>}
          <div className="explorer-hint muted">↑↓ 이동 · Enter 선택 · Esc 닫기</div>
        </div>
      )}
    </div>
  )
}
