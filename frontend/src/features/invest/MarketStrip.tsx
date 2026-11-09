/** 투자 화면 맨 위 '시장' 줄 — 지수·금리·환율(좁으면 가로로 넘겨 보기), 장 운영, 시세 출처·주문 가능 상태 */
import type { Indicator, InvestStatus, MarketSession } from './api'
import { dir, pct } from './format'

function value(i: Indicator) {
  if (i.price === null) return '–'
  return i.unit === '%' ? `${i.price.toFixed(3)}%` : i.price.toLocaleString('ko-KR', { maximumFractionDigits: 2 })
}

function change(i: Indicator) {
  if (i.unit !== '%') return pct(i.change_rate)
  return i.change === null ? '–' : `${i.change > 0 ? '+' : ''}${(i.change * 100).toFixed(1)}bp`  // 금리는 bp
}

export default function MarketStrip({ indicators, session, status }: {
  indicators: Indicator[]
  session: MarketSession | null
  status: InvestStatus | null
}) {
  return (
    <div className="market-strip" aria-label="시장 요약">
      <div className="index-scroll" tabIndex={0} aria-label="지수·금리·환율">
        {indicators.map((i) => (
          <div key={i.symbol} className="index-chip">
            <span className="muted">{i.name}</span>
            <b className="num">{value(i)}</b>
            <span className={dir(i.change)}>{change(i)}</span>
          </div>
        ))}
        {!indicators.length && <span className="muted small">지수를 불러오는 중…</span>}
      </div>
      <div className="badges strip-badges">
        {session && (
          <span className={`badge ${session.is_open ? 'open' : ''}`} title={session.note ?? undefined}>
            {session.is_open ? '장중' : '장 마감'}
            {session.start && session.end ? ` ${session.start}–${session.end}` : ''}
          </span>
        )}
        {status && (
          <>
            <span className={`badge ${status.provider === 'sample' ? 'warn' : 'info'}`}
              title={status.provider === 'sample' ? '토스증권 API 키가 없어 가상 시세를 보여줍니다 (설정 > 투자)' : '토스증권 Open API'}>
              {status.provider === 'sample' ? '샘플 데이터' : '토스증권'}
            </span>
            {status.trading_blockers.length > 0 ? (
              <span className="badge warn" title={status.trading_blockers.join(' · ')}>주문 불가</span>
            ) : (
              <span className="badge mode-live" title="주문은 토스증권 실계좌로 나가요">실계좌</span>
            )}
            {status.halted && <span className="badge mode-live">주문 중지</span>}
          </>
        )}
      </div>
    </div>
  )
}
