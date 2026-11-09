/**
 * 현황 서랍의 보유 종목 표 (서랍의 중심) — 종목마다 비중·현재가·오늘 등락·손익과 지표(5일·20일 등락, RSI, 거래량 배수, 이동평균 정렬),
 * 보고 있는 회의의 에이전트 최신 입장(매수·매도·의견 없음 수). 숫자는 서버 코드 계산(/holdings/indicators), 30초마다 갱신.
 * 상승 빨강·하락 파랑, 종목을 누르면 투자 화면. 지표는 국내 종목만(해외·다른 계좌 시세가 없으면 –).
 */
import { useNavigate } from 'react-router-dom'
import { investApi, type Stance } from '../api'
import { pct, price, ratio, useTicker } from '../format'
import { useLoad } from '../../../shared/useLoad'

const TREND: Record<number, string> = { 1: '정배열', [-1]: '역배열', 0: '혼조' }
const tone = (v: number | null | undefined) => (!v ? '' : v > 0 ? 'up' : 'down')

export default function HoldingsTable({ views }: {
  /** 종목 → 이 회의 에이전트 최신 입장 수 */
  views: Map<string, Record<Stance, number>>
}) {
  const tick = useTicker(30000)
  const { data, error } = useLoad(() => investApi.holdingIndicators(), [tick])
  const navigate = useNavigate()
  if (!data) return error ? <p className="muted small">{error}</p> : <p className="muted small">불러오는 중…</p>
  if (!data.rows.length) {
    return <p className="muted small">보유 종목이 없어요 — 현금 {ratio(data.cash_ratio, 0)}</p>
  }
  return (
    <>
      <p className="muted small">
        총 자산 {price(data.total_assets_krw)}원 · 현금 {ratio(data.cash_ratio, 0)} · {data.rows.length}종목{data.source === 'sample' && ' · 샘플 시세'}
      </p>
      <div className="scroll-x">
        <table className="holdings-table small">
          <thead>
            <tr>
              <th scope="col">종목</th><th scope="col">비중</th><th scope="col">현재가</th><th scope="col">오늘</th>
              <th scope="col">손익</th><th scope="col">5일</th><th scope="col">20일</th><th scope="col">RSI</th>
              <th scope="col">거래량</th><th scope="col">추세</th><th scope="col">에이전트</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => {
              const v = views.get(r.symbol)
              return (
                <tr key={`${r.where}:${r.symbol}`}>
                  <th scope="row">
                    <button className="link-button holdings-name" onClick={() => navigate(`/invest?s=${r.symbol}`)}>{r.name || r.symbol}</button>
                    <span className="muted holdings-sub">
                      {r.quantity.toLocaleString('ko-KR')}주 · 평단 {price(r.avg_price, r.currency ?? 'KRW')}{r.where !== '토스' ? ` · ${r.where}` : ''}
                    </span>
                  </th>
                  <td>{ratio(r.weight)}</td>
                  <td>{price(r.last_price, r.currency ?? 'KRW')}</td>
                  <td className={tone(r.change_1d)}>{pct(r.change_1d, 1)}</td>
                  <td className={tone(r.pnl_rate)}>
                    {pct(r.pnl_rate, 1)}
                    {r.pnl_krw !== null && <span className="holdings-sub">{r.pnl_krw > 0 ? '+' : ''}{price(r.pnl_krw)}</span>}
                  </td>
                  <td className={tone(r.change_5d)}>{pct(r.change_5d, 1)}</td>
                  <td className={tone(r.change_20d)}>{pct(r.change_20d, 1)}</td>
                  <td className={r.rsi !== null && r.rsi >= 70 ? 'up' : r.rsi !== null && r.rsi <= 30 ? 'down' : ''}>{r.rsi ?? '–'}</td>
                  <td>{r.volume_ratio !== null ? `${r.volume_ratio.toFixed(1)}배` : '–'}</td>
                  <td>{r.trend !== null ? TREND[r.trend] : '–'}</td>
                  <td>
                    {v ? (
                      <span className="holdings-views">
                        {v.buy > 0 && <span className="stance buy">매수 {v.buy}</span>}
                        {v.sell > 0 && <span className="stance sell">매도 {v.sell}</span>}
                        {!v.buy && !v.sell && <span className="muted">의견 없음</span>}
                      </span>
                    ) : <span className="muted">–</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="muted small">RSI 70 이상 과열(빨강)·30 이하 과매도(파랑) · 거래량 = 오늘 ÷ 20일 평균 · 추세 = 이동평균 5·20·60 정렬 · 에이전트 = 이 회의 최신 입장</p>
    </>
  )
}
