/** 투자 화면 종목 줄 — 종목 찾기(검색·관심·보유·랭킹·최근)와 지금 보는 종목의 이름·시세·관심 ☆ */
import type { StockSnapshot } from './api'
import { dir, pct, price, signed } from './format'
import StockExplorer from './StockExplorer'

interface Props {
  symbol: string
  snap: StockSnapshot | null
  error: string | null
  watched: boolean
  watchVersion: number
  onSelect: (symbol: string) => void
  onToggleWatch: () => void
  onWatchChanged: () => void
}

export default function StockBar({ symbol, snap, error, watched, watchVersion, onSelect, onToggleWatch, onWatchChanged }: Props) {
  return (
    <div className="stock-bar card">
      <StockExplorer current={symbol} currentName={snap?.name} watchVersion={watchVersion} onPick={onSelect} onWatchChanged={onWatchChanged} />
      <div className="stock-head">
        {snap ? (
          <>
            <div className="stock-name">
              <button className={`star${watched ? ' on' : ''}`} onClick={onToggleWatch}
                aria-label={watched ? '관심 종목에서 빼기' : '관심 종목에 추가'} title={watched ? '모든 관심 그룹에서 빼기' : '첫 관심 그룹에 담기'}>
                {watched ? '★' : '☆'}
              </button>
              <strong>{snap.name}</strong>
              <span className="muted small mono">{snap.symbol}</span>
              <span className="chip">{snap.market}</span>
              {snap.security_type === 'ETF' && <span className="chip etf">ETF</span>}
              {snap.suspended && <span className="badge warn">거래정지</span>}
            </div>
            <div className={`stock-price ${dir(snap.change)}`}>
              <span className="big">{price(snap.price, snap.currency)}</span>
              <span>
                {signed(snap.change, snap.currency)} ({pct(snap.change_rate)})
              </span>
            </div>
          </>
        ) : (
          <span className={error ? 'error small' : 'muted small'}>{error ?? '불러오는 중…'}</span>
        )}
      </div>
    </div>
  )
}
