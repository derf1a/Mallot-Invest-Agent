/**
 * 포트폴리오 — 토스 실계좌(국내·해외·예수금)와 다른 계좌 보유를 합친 내 투자현황.
 * 총 자산·평가 손익 → 자산 배분(종목·계좌·유형) → 쏠림 점검 → 종목별 손익 → 보유 목록 → 다른 계좌 입력 순서.
 */
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { StatTile } from '../../shared/charts'
import { investApi } from './api'
import { dir, pct, ratio, signed, useTicker, won } from './format'
import { AllocationBar, Meter, PnlBars } from './portfolio/charts'
import HoldingsGrid from './portfolio/HoldingsGrid'
import { allocation, cashTotal, pnlBySymbol, symbolColors, totals, type AllocationMode } from './portfolio/model'
import OtherAccounts from './portfolio/OtherAccounts'
import { useLoad } from '../../shared/useLoad'

const MODES: { key: AllocationMode; label: string }[] = [
  { key: 'symbol', label: '종목' },
  { key: 'account', label: '계좌' },
  { key: 'type', label: '유형' },
]
// 쏠림 주의선 — 투자 조언이 아니라 눈에 띄게 하는 기준
const TOP1_MARK = 0.3
const TOP3_MARK = 0.6

export default function Portfolio() {
  const navigate = useNavigate()
  const tick = useTicker(30_000)
  const [version, setVersion] = useState(0)
  const [mode, setMode] = useState<AllocationMode>('symbol')
  const { data, error } = useLoad(
    () => investApi.portfolio().then((o) => ({ o, at: new Date().toTimeString().slice(0, 8) })),
    [tick, version],
  )
  const refresh = () => setVersion((v) => v + 1)
  const o = data?.o

  const t = o ? totals(o.positions) : null
  const cash = o ? cashTotal(o) : 0
  const stock = o?.stock_value_krw ?? 0
  // 주식 안에서 '사실상 몇 종목에 나눠 가졌나' = 1 / Σ(비중²)
  const bySymbol = new Map<string, number>()
  for (const p of o?.positions ?? []) bySymbol.set(p.symbol, (bySymbol.get(p.symbol) ?? 0) + p.value_krw)
  const effective = stock > 0 ? 1 / [...bySymbol.values()].reduce((a, v) => a + (v / stock) ** 2, 0) : null
  const accounts = new Set(o?.positions.map((p) => p.where) ?? [])

  return (
    <section className="page portfolio-page">
      <div className="page-header">
        <h1>포트폴리오</h1>
        <span className="toolbar">
          {data && <span className="muted small">기준 {data.at}</span>}
          <button onClick={refresh}>새로 고침</button>
          <Link className="button" to="/invest">종목·주문 →</Link>
        </span>
      </div>

      {error && (
        <p className="alert warn">
          내 투자현황을 불러오지 못했어요: {error}. 토스증권 연결은 <Link to="/settings?tab=invest">설정 &gt; 투자</Link>에서 확인하고, 다른 계좌 보유는 아래에서
          관리할 수 있어요.
        </p>
      )}
      {!o && !error && <p className="muted">불러오는 중…</p>}

      {o && t && (
        <>
          <article className="card pf-hero">
            <div className="pf-total">
              <span className="muted">총 자산</span>
              <strong className="pf-big">{won(o.total_assets_krw)}</strong>
              <span className={`pf-pnl ${dir(t.pnl)}`}>
                {signed(t.pnl)}원 ({pct(t.rate)}) <span className="muted small">평가 손익</span>
              </span>
            </div>
            <div className="tile-row pf-tiles">
              <StatTile label="주식 평가" value={won(stock)} hint={`${bySymbol.size}종목 · 계좌 ${accounts.size}곳`} />
              <StatTile label="현금" value={won(cash)}
                hint={o.cash.usd ? `원화 ${won(o.cash.krw)} + $${o.cash.usd.toLocaleString('en-US')}` : '토스 원화 예수금'} />
              <StatTile label="투자 원금" value={won(t.cost)} hint={t.unknown ? `평단 모르는 ${t.unknown}종목 제외` : '평단 × 수량'} />
              <StatTile label="현금 비중" value={ratio(o.cash_ratio)} hint={o.usd_krw ? `달러 환율 ${o.usd_krw.toLocaleString('ko-KR')}원` : undefined} />
            </div>
          </article>

          <div className="pf-grid">
            <article className="card pf-alloc">
              <div className="section-head">
                <h2>자산 배분</h2>
                <div className="segmented small" role="tablist" aria-label="나누는 기준">
                  {MODES.map((m) => (
                    <button key={m.key} role="tab" aria-selected={mode === m.key} className={mode === m.key ? 'on' : ''} onClick={() => setMode(m.key)}>
                      {m.label}
                    </button>
                  ))}
                </div>
              </div>
              <AllocationBar slices={allocation(o, mode)} />
            </article>

            <article className="card">
              <h2>쏠림 점검</h2>
              <div className="meters">
                <Meter label="가장 큰 종목" value={o.concentration.top1} mark={TOP1_MARK} markLabel={`한 종목이 전체의 ${TOP1_MARK * 100}%를 넘어요`}
                  hint={`주의선 ${TOP1_MARK * 100}%`} />
                <Meter label="상위 3종목" value={o.concentration.top3} mark={TOP3_MARK} markLabel={`상위 3종목이 ${TOP3_MARK * 100}%를 넘어요`}
                  hint={`주의선 ${TOP3_MARK * 100}%`} />
                <Meter label="현금" value={o.cash_ratio} hint="바로 쓸 수 있는 돈의 비중" />
              </div>
              {effective !== null && (
                <p className="small pf-effective">
                  주식은 <b>사실상 {effective.toFixed(1)}종목</b>에 나눠 가진 셈이에요 <span className="muted">(보유 {bySymbol.size}종목, 금액 쏠림 반영)</span>
                </p>
              )}
            </article>

            <article className="card">
              <h2>종목별 손익</h2>
              <PnlBars rows={pnlBySymbol(o.positions)} />
            </article>
          </div>

          <article className="card">
            <h2>보유 종목</h2>
            <HoldingsGrid positions={o.positions} colors={symbolColors(o)} />
          </article>
        </>
      )}

      <article className="card">
        <OtherAccounts version={version} onSelect={(s) => navigate(`/invest?s=${s}`)} onChanged={refresh} />
      </article>
    </section>
  )
}
