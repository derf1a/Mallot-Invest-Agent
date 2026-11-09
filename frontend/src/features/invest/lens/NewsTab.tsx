import { useState } from 'react'
import { useLoad } from '../../../shared/useLoad'
import { investApi, type StockSnapshot } from '../api'

const SCOPES = [
  { key: 'symbol', label: '이 종목' },
  { key: 'holdings', label: '보유' },
  { key: 'watchlist', label: '관심' },
  { key: 'market', label: '시장' },
] as const

/** 뉴스 — Google 뉴스 RSS. 제목을 누르면 원문(새 창). */
export default function NewsTab({ snap }: { snap: StockSnapshot | null }) {
  const [scope, setScope] = useState<(typeof SCOPES)[number]['key']>('symbol')
  const [refresh, setRefresh] = useState(0)
  const { data, error } = useLoad(
    () => investApi.news(scope, { symbol: snap?.symbol, name: snap?.name, refresh: refresh > 0 }),
    [scope, snap?.symbol, refresh],
  )

  return (
    <div className="news-tab">
      <div className="toolbar">
        <div className="segmented small">
          {SCOPES.map((s) => (
            <button key={s.key} className={scope === s.key ? 'on' : ''} onClick={() => setScope(s.key)}>
              {s.label}
            </button>
          ))}
        </div>
        <button onClick={() => setRefresh((r) => r + 1)}>새로 받기</button>
      </div>
      {error && <p className="error small">{error}</p>}
      {data?.warnings.map((w) => (
        <p key={w} className="muted small">
          {w}
        </p>
      ))}
      {data && data.items.length === 0 && (
        <p className="muted small">
          {scope === 'holdings' ? '보유 종목이 없어요.' : scope === 'watchlist' ? '관심 종목이 없어요.' : '뉴스가 없어요.'}
        </p>
      )}
      <ul className="news-list">
        {data?.items.map((n) => (
          <li key={n.id}>
            <a href={n.url} target="_blank" rel="noopener noreferrer">
              {n.title}
            </a>
            <span className="muted small">
              {n.source || '출처 미상'} · {n.published_at ?? ''}
              {n.symbols.length > 0 && scope !== 'symbol' && ` · ${data.targets.filter((t) => n.symbols.includes(t.symbol)).map((t) => t.name).join(', ')}`}
            </span>
            {n.snippet && <span className="small news-snippet">{n.snippet}</span>}
          </li>
        ))}
      </ul>
      <p className="muted small">Google 뉴스 RSS · 같은 검색은 15분 동안 다시 받지 않아요</p>
    </div>
  )
}
