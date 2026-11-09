import { useCallback, useEffect, useState } from 'react'
import { Link, Navigate, useSearchParams } from 'react-router-dom'
import { investApi, type Order } from './api'
import OrdersTab from './desk/OrdersTab'
import OrderTab, { type OrderDraft } from './desk/OrderTab'
import { useTicker } from './format'
import ChartTab from './lens/ChartTab'
import InfoTab from './lens/InfoTab'
import NewsTab from './lens/NewsTab'
import OrderbookTab from './lens/OrderbookTab'
import ReportTab from './lens/ReportTab'
import MarketStrip from './MarketStrip'
import StockBar from './StockBar'
import { pushRecent } from './recent'
import { useLoad } from '../../shared/useLoad'

// 시장(맨 위 줄) → 종목 찾기·고른 종목(종목 줄) → 그 종목의 Lens(정보) / Desk(행동)
const LENS_TABS = [
  { key: 'chart', label: '차트' },
  { key: 'book', label: '호가' },
  { key: 'info', label: '종목정보' },
  { key: 'news', label: '뉴스' },
  { key: 'report', label: 'AI 리포트' },
] as const
const DESK_TABS = [
  { key: 'order', label: '주문' },
  { key: 'orders', label: '주문내역' },
] as const
type LensTab = (typeof LENS_TABS)[number]['key']
type DeskTab = (typeof DESK_TABS)[number]['key']
const LAST_SYMBOL = 'invest.symbol'

function savedSymbol(): string | null {
  try {
    return localStorage.getItem(LAST_SYMBOL)
  } catch {
    return null
  }
}

export default function Invest() {
  const [params, setParams] = useSearchParams()
  const symbol = params.get('s') ?? savedSymbol() ?? '005930'
  const lens = (LENS_TABS.find((t) => t.key === params.get('lens'))?.key ?? 'chart') as LensTab
  const desk = (DESK_TABS.find((t) => t.key === params.get('desk'))?.key ?? 'order') as DeskTab
  const [pane, setPane] = useState<'lens' | 'desk'>('lens')
  const [tradeVersion, setTradeVersion] = useState(0)
  const [watchVersion, setWatchVersion] = useState(0)
  const [draft, setDraft] = useState<OrderDraft | null>(null)

  const set = useCallback(
    (patch: Record<string, string>) => setParams((p) => {
      const next = new URLSearchParams(p)
      Object.entries(patch).forEach(([k, v]) => next.set(k, v))
      return next
    }, { replace: true }),
    [setParams],
  )

  const fast = useTicker(5_000)
  const slow = useTicker(30_000)
  const { data: status } = useLoad(() => investApi.status(), [slow])
  const { data: market } = useLoad(() => investApi.market(), [slow])
  const { data: watchlist } = useLoad(() => investApi.watchlist(), [watchVersion, slow])
  const { data: snap, error: snapError } = useLoad(() => investApi.stock(symbol), [symbol, fast])

  useEffect(() => {
    try {
      localStorage.setItem(LAST_SYMBOL, symbol)
    } catch {
      /* 저장 못 해도 동작에는 지장 없음 */
    }
  }, [symbol])
  const snapName = snap?.symbol === symbol ? snap.name : null
  useEffect(() => {
    if (snapName) pushRecent({ symbol, name: snapName })  // 종목 찾기의 '최근'
  }, [symbol, snapName])

  const select = useCallback((s: string) => set({ s }), [set])
  const bumpTrade = useCallback(() => setTradeVersion((v) => v + 1), [])
  const bumpWatch = useCallback(() => setWatchVersion((v) => v + 1), [])

  // 예전 주소: AI 에이전트·보유는 따로 떨어진 화면으로 옮겼다
  const legacy = params.get('desk')
  if (legacy === 'agents') return <Navigate to={`/invest/ai${params.get('session') ? `?session=${params.get('session')}` : ''}`} replace />
  if (legacy === 'holdings') return <Navigate to="/invest/portfolio" replace />

  const watched = !!watchlist?.some((w) => w.symbol === symbol)
  const toggleWatch = async () => {
    if (watched) await investApi.removeWatch(symbol)  // 모든 그룹에서
    else await investApi.addWatch(symbol, { name: snap?.name ?? '', market: snap?.market ?? '', security_type: snap?.security_type ?? '' })
    bumpWatch()
  }
  const pickPrice = (p: number) => {
    setDraft({ symbol, price: p, nonce: Date.now() })
    set({ desk: 'order' })
    setPane('desk')
  }
  const onPlaced = (_order: Order) => bumpTrade()

  return (
    <section className="page invest-page">
      <MarketStrip indicators={market?.indicators ?? []} session={market?.session ?? null} status={status} />
      {status?.provider === 'sample' && (
        <p className="sample-note small">
          토스증권 API가 연결되지 않아 <b>샘플 데이터</b>(가상 시세)를 보여주고 있어요. <Link to="/settings?tab=invest">설정 &gt; 투자</Link>에서 키를 넣으면 실제 시세로 바뀌어요.
        </p>
      )}
      <StockBar symbol={symbol} snap={snap} error={snapError} watched={watched} watchVersion={watchVersion}
        onSelect={select} onToggleWatch={toggleWatch} onWatchChanged={bumpWatch} />

      <div className="segmented pane-switch" role="tablist" aria-label="창 전환">
        <button className={pane === 'lens' ? 'on' : ''} onClick={() => setPane('lens')}>Lens · 정보</button>
        <button className={pane === 'desk' ? 'on' : ''} onClick={() => setPane('desk')}>Desk · 주문</button>
      </div>

      <div className="invest-grid">
        <div className={`invest-pane lens card${pane === 'lens' ? ' active' : ''}`}>
          <div className="pane-title">
            <span className="pane-name">Lens</span>
            <span className="muted small">{snap?.name ?? symbol} 들여다보기</span>
          </div>
          <div className="tabs" role="tablist">
            {LENS_TABS.map((t) => (
              <button key={t.key} role="tab" aria-selected={lens === t.key} className={lens === t.key ? 'on' : ''} onClick={() => set({ lens: t.key })}>
                {t.label}
              </button>
            ))}
          </div>
          <div className="pane-body">
            {lens === 'chart' && <ChartTab symbol={symbol} snap={snap} />}
            {lens === 'book' && <OrderbookTab symbol={symbol} snap={snap} onPickPrice={pickPrice} />}
            {lens === 'info' && (snap ? <InfoTab snap={snap} /> : <p className="muted small">{snapError ?? '불러오는 중…'}</p>)}
            {lens === 'news' && <NewsTab snap={snap} />}
            {lens === 'report' && <ReportTab snap={snap} />}
          </div>
        </div>

        <div className={`invest-pane desk card${pane === 'desk' ? ' active' : ''}`}>
          <div className="pane-title">
            <span className="pane-name">Desk</span>
            <span className="muted small">주문</span>
            {status && (status.trading_blockers.length > 0
              ? <span className="badge warn" title={status.trading_blockers.join(' · ')}>주문 불가</span>
              : <span className="badge mode-live">실계좌</span>)}
          </div>
          <div className="tabs" role="tablist">
            {DESK_TABS.map((t) => (
              <button key={t.key} role="tab" aria-selected={desk === t.key} className={desk === t.key ? 'on' : ''} onClick={() => set({ desk: t.key })}>
                {t.label}
              </button>
            ))}
          </div>
          <div className="pane-body">
            {desk === 'order' && (snap ? <OrderTab key={`${snap.symbol}:${draft?.symbol === snap.symbol ? draft.nonce : 0}`} snap={snap}
              initialPrice={draft?.symbol === snap.symbol ? draft.price : undefined} version={tradeVersion} onPlaced={onPlaced} /> : <p className="muted small">종목을 고르세요</p>)}
            {desk === 'orders' && <OrdersTab version={tradeVersion} onChanged={bumpTrade} />}
          </div>
          <nav className="desk-links small" aria-label="다른 투자 화면">
            <Link to="/invest/portfolio">내 포트폴리오 →</Link>
            <Link to={`/invest/ai?s=${symbol}`}>AI Trading에서 {snap?.name ?? '이 종목'} 토론 →</Link>
          </nav>
        </div>
      </div>
    </section>
  )
}
