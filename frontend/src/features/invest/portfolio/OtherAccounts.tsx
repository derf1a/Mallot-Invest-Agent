import { useState } from 'react'
import { useLoad } from '../../../shared/useLoad'
import { investApi, type SearchHit } from '../api'
import { digits, grouped, pct, price, won } from '../format'
import SymbolSearch from '../SymbolSearch'

/** 다른 계좌 보유(직접 입력) — 토스 밖 계좌의 종목. 포트폴리오와 포트폴리오 매니저 에이전트가 전체 자산으로 본다. */
export default function OtherAccounts({ version, onSelect, onChanged }: {
  version: number
  onSelect: (symbol: string) => void
  onChanged?: () => void // 포트폴리오 다시 계산
}) {
  const [local, setLocal] = useState(0)
  const { data: rows, error } = useLoad(() => investApi.externalHoldings(), [version, local])
  const [adding, setAdding] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const act = async (fn: () => Promise<unknown>) => {
    setMessage(null)
    try {
      await fn()
      setLocal((v) => v + 1)
      onChanged?.()
      return true
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e))
      return false
    }
  }

  return (
    <section className="other-accounts">
      <div className="section-head">
        <h2>다른 계좌 보유 <span className="muted small">직접 입력</span></h2>
        <button className="small" onClick={() => setAdding((v) => !v)}>{adding ? '닫기' : '+ 추가'}</button>
      </div>
      {adding && <AddForm onAdd={(body) => act(() => investApi.addExternal(body))} />}
      {(error || message) && <p className="error small">{error ?? message}</p>}
      {rows && rows.length === 0 && !adding && (
        <p className="muted small">토스 밖(다른 증권사·연금 계좌 등)에 가진 종목을 적어 두면 포트폴리오 매니저가 전체 자산으로 판단해요.</p>
      )}
      {rows && rows.length > 0 && (
        <ul className="holding-list">
          {rows.map((r) => (
            <li key={r.id} className="other-row">
              <button className="holding-row" onClick={() => onSelect(r.symbol)}>
                <span className="h-name">
                  <strong>{r.name || r.symbol}</strong>
                  <span className="muted small">
                    {r.account_name || '다른 계좌'} · {r.quantity.toLocaleString('ko-KR')}주{r.avg_price ? ` · 평단 ${price(r.avg_price, r.currency)}` : ''}
                    {!r.priced && ' · 현재가 없음(평단 기준)'}
                  </span>
                </span>
                <span className="h-value">
                  <strong>{won(r.value_krw)}</strong>
                  {r.pnl_rate !== null && <span className="small">{pct(r.pnl_rate)}</span>}
                </span>
              </button>
              <button className="icon-button" aria-label={`${r.name} 수량 변경`}
                onClick={() => {
                  const q = window.prompt(`${r.name} 수량`, String(r.quantity))
                  if (q && Number(q) > 0) act(() => investApi.updateExternal(r.id, { quantity: Number(q) }))
                }}>✎</button>
              <button className="icon-button" aria-label={`${r.name} 지우기`}
                onClick={() => window.confirm(`${r.name}(${r.account_name || '다른 계좌'})을 지울까요?`) && act(() => investApi.deleteExternal(r.id))}>✕</button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function AddForm({ onAdd }: { onAdd: (body: { symbol: string; name: string; quantity: number; avg_price: number | null; account_name: string }) => Promise<boolean> }) {
  const [hit, setHit] = useState<SearchHit | null>(null)
  const [qty, setQty] = useState('')
  const [avg, setAvg] = useState('')
  const [account, setAccount] = useState('')
  const submit = async () => {
    if (!hit || !(Number(qty) > 0)) return
    const ok = await onAdd({ symbol: hit.symbol, name: hit.name, quantity: Number(qty), avg_price: avg ? Number(avg) : null, account_name: account })
    if (ok) {
      setHit(null)
      setQty('')
      setAvg('')
    }
  }
  return (
    <div className="other-form">
      {hit ? (
        <p className="small"><b>{hit.name}</b> <span className="muted mono">{hit.symbol}</span> <button className="link small" onClick={() => setHit(null)}>바꾸기</button></p>
      ) : (
        <SymbolSearch placeholder="종목·ETF 검색" onPick={setHit} />
      )}
      <div className="other-fields">
        <input type="text" value={account} maxLength={40} placeholder="계좌 이름 (예: 키움 ISA)" aria-label="계좌 이름" onChange={(e) => setAccount(e.target.value)} />
        <input inputMode="numeric" value={grouped(qty)} placeholder="수량" aria-label="수량" onChange={(e) => setQty(digits(e.target.value))} />
        <input inputMode="numeric" value={grouped(avg)} placeholder="평단 (선택)" aria-label="평균 매입가" onChange={(e) => setAvg(digits(e.target.value))} />
        <button className="primary" disabled={!hit || !(Number(qty) > 0)} onClick={submit}>저장</button>
      </div>
    </div>
  )
}
