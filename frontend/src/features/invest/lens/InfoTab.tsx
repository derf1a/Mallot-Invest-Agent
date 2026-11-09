import { useLoad } from '../../../shared/useLoad'
import { investApi, type StockSnapshot } from '../api'
import { compact, dir, pct, price } from '../format'

const TYPE_LABEL: Record<string, string> = {
  STOCK: '주식', ETF: 'ETF', ETN: 'ETN', REIT: '리츠', FOREIGN_STOCK: '해외 주식', FOREIGN_ETF: '해외 ETF',
  DEPOSITARY_RECEIPT: '주식예탁증서', INFRASTRUCTURE_FUND: '인프라 펀드', STOCK_WARRANTS: '신주인수권',
}

/** 종목 정보 — 기본 정보, 기술 지표, 투자자별 순매수 */
export default function InfoTab({ snap }: { snap: StockSnapshot }) {
  const kr = snap.currency === 'KRW'
  const { data: flow, error } = useLoad(() => (kr ? investApi.investor(snap.symbol) : Promise.resolve([])), [snap.symbol, kr])
  const t = snap.technicals
  const cur = snap.currency
  const maxFlow = Math.max(1, ...(flow ?? []).flatMap((d) => [d.individual, d.foreigner, d.institution].map((v) => Math.abs(v ?? 0))))

  return (
    <div className="info-tab">
      <div className="res-split">
        <section>
          <h3 className="small">기본 정보</h3>
          <dl className="kv">
            <dt>시장</dt>
            <dd>{snap.market}</dd>
            <dt>종목 유형</dt>
            <dd>
              {TYPE_LABEL[snap.security_type] ?? snap.security_type}
              {snap.is_common === false && ' · 우선주'}
              {snap.leverage ? ` · ${snap.leverage}배` : ''}
            </dd>
            <dt>상장일</dt>
            <dd>{snap.list_date ?? '–'}</dd>
            <dt>발행주식수</dt>
            <dd>{compact(snap.shares_outstanding)}주</dd>
            <dt>시가총액</dt>
            <dd>{kr ? `${compact(snap.market_cap)}원` : `$${compact(snap.market_cap)}`}</dd>
            <dt>최근 {snap.range_bars}일 범위</dt>
            <dd>
              <span className="down">{price(snap.range_low, cur)}</span> ~ <span className="up">{price(snap.range_high, cur)}</span>
            </dd>
            <dt>20일 평균 거래량</dt>
            <dd>{compact(snap.avg_volume_20)}</dd>
          </dl>
        </section>
        {t && (
          <section>
            <h3 className="small">기술 지표</h3>
            <dl className="kv">
              <dt>이동평균 5일</dt>
              <dd>{price(t.ma5, cur)}</dd>
              <dt>이동평균 20일</dt>
              <dd>{price(t.ma20, cur)}</dd>
              <dt>이동평균 60일</dt>
              <dd>{price(t.ma60, cur)}</dd>
              <dt>RSI(14)</dt>
              <dd className={t.rsi14 !== null && t.rsi14 >= 70 ? 'up' : t.rsi14 !== null && t.rsi14 <= 30 ? 'down' : ''}>
                {t.rsi14?.toFixed(1) ?? '–'}
                {t.rsi14 !== null && (t.rsi14 >= 70 ? ' 과열' : t.rsi14 <= 30 ? ' 침체' : '')}
              </dd>
              <dt>MACD</dt>
              <dd className={dir(t.macd?.histogram)}>
                {t.macd ? t.macd.macd.toLocaleString('ko-KR', { maximumFractionDigits: 1 }) : '–'}
                {t.macd && <span className="muted small"> 시그널 {t.macd.signal.toLocaleString('ko-KR', { maximumFractionDigits: 1 })}</span>}
              </dd>
              <dt>변동성(연율)</dt>
              <dd>{t.volatility_20d_annualized !== null ? `${(t.volatility_20d_annualized * 100).toFixed(1)}%` : '–'}</dd>
              <dt>수익률 5일 · 20일</dt>
              <dd>
                <span className={dir(t.return_5d)}>{pct(t.return_5d, 1)}</span> · <span className={dir(t.return_20d)}>{pct(t.return_20d, 1)}</span>
              </dd>
              <dt>수익률 60일</dt>
              <dd className={dir(t.return_60d)}>{pct(t.return_60d, 1)}</dd>
            </dl>
          </section>
        )}
      </div>
      {kr && (
        <section>
          <h3 className="small">투자자별 순매수 (주)</h3>
          {error && <p className="error small">{error}</p>}
          {flow && flow.length > 0 && (
            <div className="table-wrap">
              <table className="data-table flow-table">
                <thead>
                  <tr>
                    <th>날짜</th>
                    <th>개인</th>
                    <th>외국인</th>
                    <th>기관</th>
                  </tr>
                </thead>
                <tbody>
                  {flow.map((d) => (
                    <tr key={d.date}>
                      <td>{d.date.slice(5)}</td>
                      {[d.individual, d.foreigner, d.institution].map((v, k) => (
                        <td key={k} className={dir(v)}>
                          <span className="flow-cell">
                            <i className={`flow-bar ${dir(v)}`} style={{ width: `${(Math.abs(v ?? 0) / maxFlow) * 48}px` }} />
                            {v === null ? '–' : `${v > 0 ? '+' : ''}${compact(v)}`}
                          </span>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  )
}
