/**
 * 수익률 (현황 서랍) — 운용 수익률(기준 원금 대비 토스 계좌 총액, 기준 원금·입출금 고치기), 내 투자현황 평가 손익(원금을 아는 보유),
 * 항목별 수익률(탭: 종목별 매입·평가·손익·수익률·기여도 / AI 주문별 체결가 → 지금(매도는 판 뒤 내린 만큼) / 회의별 합).
 * 상승 빨강·하락 파랑. 숫자는 서버(briefing.performance)가 계산.
 */
import { useState } from 'react'
import { investApi, type Performance } from '../api'
import { useLoad } from '../../../shared/useLoad'

const won = (n: number) => `${n > 0 ? '+' : ''}${Math.round(n).toLocaleString('ko-KR')}원`
const plain = (n: number) => `${Math.round(n).toLocaleString('ko-KR')}원`
const pct = (v: number | null) => (v === null ? '-' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`)
const tone = (v: number | null) => (!v ? '' : v > 0 ? 'up' : 'down')
const SIDE = { buy: '매수', sell: '매도' }

export default function PerformanceView() {
  const { data, error, reload } = useLoad(() => investApi.performance(), [])
  if (error) return <p className="muted small">{error}</p>
  if (!data) return <p className="muted small">불러오는 중…</p>
  const pf = data.portfolio
  return (
    <div className="perf">
      <BaselineBlock bl={data.baseline} onSaved={reload} />
      {pf ? (
        <div className="perf-kpis">
          <div><span className="muted small">총 자산</span><strong>{plain(pf.total_assets_krw)}</strong></div>
          <div>
            <span className="muted small">평가 손익</span>
            <strong className={tone(pf.pnl_krw)}>{won(pf.pnl_krw)}</strong>
            <span className={`small ${tone(pf.return_rate)}`}>{pct(pf.return_rate)}</span>
          </div>
          <div>
            <span className="muted small">현금</span>
            <strong>{plain(pf.cash_krw)}</strong>
            {pf.cash_ratio !== null && <span className="muted small">{(pf.cash_ratio * 100).toFixed(0)}%</span>}
          </div>
        </div>
      ) : (
        <p className="muted small">{data.portfolio_note}</p>
      )}
      <Items data={data} />
    </div>
  )
}

const TABS = [{ key: 'stocks', label: '종목별' }, { key: 'orders', label: 'AI 주문별' }, { key: 'meetings', label: '회의별' }] as const

/** 항목별 수익률 — 종목별(매입·평가·손익·수익률·기여도) / AI 주문별(체결가 → 지금) / 회의별(그 회의 주문의 합) */
function Items({ data }: { data: Performance }) {
  const [tab, setTab] = useState<(typeof TABS)[number]['key']>('stocks')
  const pf = data.portfolio
  const ai = data.ai
  const items = pf?.items ?? []
  const sum = (xs: (number | null)[]) => xs.reduce<number>((a, x) => a + (x ?? 0), 0)
  return (
    <section className="perf-items">
      <div className="pilot-sec-head">
        <h4>항목별 수익률</h4>
        <div className="perf-tabs" role="tablist" aria-label="항목">
          {TABS.map((t) => (
            <button key={t.key} role="tab" aria-selected={tab === t.key} className={`chip-toggle${tab === t.key ? ' on' : ''}`}
              onClick={() => setTab(t.key)}>{t.label}</button>
          ))}
        </div>
      </div>
      {tab === 'stocks' && (items.length ? (
        <div className="scroll-x">
          <table className="holdings-table small">
            <thead>
              <tr><th scope="col">종목</th><th scope="col">매입</th><th scope="col">평가</th><th scope="col">손익</th><th scope="col">수익률</th>
                <th scope="col" title={`그 종목 손익 ÷ ${pf?.contribution_base}`}>기여도</th></tr>
            </thead>
            <tbody>
              {items.map((x) => (
                <tr key={`${x.where}:${x.symbol}`}>
                  <th scope="row">{x.name || x.symbol}<span className="holdings-sub">{x.quantity ?? '–'}주{x.where !== '토스' ? ` · ${x.where}` : ''}</span></th>
                  <td>{x.cost_krw !== null ? plain(x.cost_krw) : '–'}</td>
                  <td>{plain(x.value_krw)}</td>
                  <td className={tone(x.pnl_krw)}>{x.pnl_krw !== null ? won(x.pnl_krw) : '–'}</td>
                  <td className={tone(x.pnl_rate)}>{pct(x.pnl_rate)}</td>
                  <td className={tone(x.contribution)}>{pct(x.contribution)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th scope="row">합계</th>
                <td>{plain(sum(items.map((x) => x.cost_krw)))}</td>
                <td>{plain(sum(items.map((x) => x.value_krw)))}</td>
                <td className={tone(pf?.pnl_krw ?? null)}>{pf ? won(pf.pnl_krw) : '–'}</td>
                <td className={tone(pf?.return_rate ?? null)}>{pct(pf?.return_rate ?? null)}</td>
                <td className={tone(sum(items.map((x) => x.contribution)))}>{pct(sum(items.map((x) => x.contribution)))}</td>
              </tr>
            </tfoot>
          </table>
          <p className="muted small">기여도 = 그 종목 손익 ÷ {pf?.contribution_base} · 손익이 큰 순{pf?.unpriced ? ` · 원금·현재가를 몰라 뺀 보유 ${pf.unpriced}개` : ''}</p>
        </div>
      ) : <p className="muted small">{pf ? '보유 종목이 없어요' : data.portfolio_note}</p>)}
      {tab === 'orders' && (ai.orders.length ? (
        <div className="scroll-x">
          <p className="small">
            체결 {ai.count}건 · {plain(ai.amount_krw)} → <b className={tone(ai.change)}>{pct(ai.change)}</b>{' '}
            <span className={tone(ai.pnl_krw)}>({won(ai.pnl_krw)})</span> <span className="muted">매도는 판 뒤 내린 만큼</span>
          </p>
          <table className="holdings-table small">
            <thead>
              <tr><th scope="col">종목</th><th scope="col">체결가</th><th scope="col">지금</th><th scope="col">변화</th><th scope="col">손익</th></tr>
            </thead>
            <tbody>
              {ai.orders.map((o) => (
                <tr key={o.id}>
                  <th scope="row">
                    {o.name} <span className={`stance ${o.side}`}>{SIDE[o.side]} {o.quantity}주</span>
                    <span className="holdings-sub">{o.at.slice(5)}{o.run_id ? ` · 회의 #${o.run_id}` : ''}</span>
                  </th>
                  <td>{Math.round(o.fill).toLocaleString('ko-KR')}</td>
                  <td>{o.now !== null ? Math.round(o.now).toLocaleString('ko-KR') : '–'}</td>
                  <td className={tone(o.change)}>{pct(o.change)}</td>
                  <td className={tone(o.pnl_krw)}>{o.pnl_krw !== null ? won(o.pnl_krw) : '–'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="muted small">{ai.note ?? '아직 체결된 AI 주문이 없어요'}</p>)}
      {tab === 'meetings' && (ai.by_meeting.length ? (
        <div className="scroll-x">
          <table className="holdings-table small">
            <thead>
              <tr><th scope="col">회의</th><th scope="col">주문</th><th scope="col">체결 금액</th><th scope="col">손익</th><th scope="col">변화</th></tr>
            </thead>
            <tbody>
              {ai.by_meeting.map((m) => (
                <tr key={m.run_id ?? 'none'}>
                  <th scope="row">{m.name}<span className="holdings-sub">{m.at.slice(5)}{m.run_id ? ` · #${m.run_id}` : ''}</span></th>
                  <td>{m.orders}건</td>
                  <td>{plain(m.amount_krw)}</td>
                  <td className={tone(m.pnl_krw)}>{won(m.pnl_krw)}</td>
                  <td className={tone(m.change)}>{pct(m.change)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">그 회의에서 낸 AI 주문의 체결가 → 지금 가격 합 (실현 손익이 아니라 판단의 평가)</p>
        </div>
      ) : <p className="muted small">{ai.note ?? '아직 체결된 AI 주문이 없어요'}</p>)}
    </section>
  )
}

const today = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** 운용 수익률 — 기준 원금(처음 넣은 돈 + 추가 입금 − 출금) 대비 토스 계좌 총액. 기준 다시 정하기 · 입금/출금 더하기 */
function BaselineBlock({ bl, onSaved }: { bl: Performance['baseline']; onSaved: () => void }) {
  const [mode, setMode] = useState<'reset' | 'add' | null>(bl ? null : 'reset')
  const [amount, setAmount] = useState('')
  const [day, setDay] = useState(today())
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const save = async () => {
    const n = Number(amount.replace(/[^0-9-]/g, ''))
    if (!n) return setNote('금액을 넣어 주세요')
    setBusy(true)
    setNote(null)
    try {
      if (mode === 'reset') await investApi.saveBaseline([{ date: day, amount: n, note: '기준 원금' }])
      else await investApi.addContribution({ date: day, amount: n, note: n > 0 ? '추가 입금' : '출금' })
      setMode(null)
      setAmount('')
      onSaved()
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="perf-baseline">
      {bl ? (
        <>
          <div className="perf-baseline-head">
            <span className="muted small">운용 수익률 <span className="muted">기준 원금 대비 · 토스 계좌</span></span>
            <strong className={tone(bl.return_rate)}>{pct(bl.return_rate)}</strong>
            {bl.pnl_krw !== null && <span className={`small ${tone(bl.pnl_krw)}`}>{won(bl.pnl_krw)}</span>}
          </div>
          <p className="muted small">
            토스 계좌 {bl.value_krw === null ? '-' : plain(bl.value_krw)} / 기준 원금 {plain(bl.amount)} · {bl.since}부터 {bl.days}일
            {bl.contributions.length > 1 && ` · 입출금 ${bl.contributions.length - 1}건 반영`}
          </p>
        </>
      ) : (
        <p className="small">기준 원금을 정하면 그날부터 <b>운용 수익률</b>(토스 계좌 총액 ÷ 넣은 돈)을 보여 줘요. 채팅으로 "기준 원금 100만원"이라고 해도 돼요.</p>
      )}
      {mode ? (
        <div className="perf-baseline-form">
          <input inputMode="numeric" value={amount} placeholder={mode === 'reset' ? '기준 원금 (원)' : '입금 + / 출금 −'} aria-label="금액"
            onChange={(e) => setAmount(e.target.value)} />
          <input type="date" value={day} aria-label="날짜" onChange={(e) => setDay(e.target.value)} />
          <button className="primary" disabled={busy} onClick={save}>{mode === 'reset' ? '기준 정하기' : '더하기'}</button>
          {bl && <button disabled={busy} onClick={() => setMode(null)}>취소</button>}
        </div>
      ) : (
        <div className="perf-baseline-actions">
          <button className="link-button small" onClick={() => setMode('add')}>입금·출금 더하기</button>
          <button className="link-button small" onClick={() => setMode('reset')}>기준 다시 정하기</button>
        </div>
      )}
      {note && <p className="muted small">{note}</p>}
    </div>
  )
}
