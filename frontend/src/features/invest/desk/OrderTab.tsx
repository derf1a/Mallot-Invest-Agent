import { useState } from 'react'
import Modal from '../../../shared/ui/Modal'
import { useLoad } from '../../../shared/useLoad'
import { ApiError, investApi, type Order, type Side, type StockSnapshot } from '../api'
import { digits, grouped, price, tickSize, won } from '../format'

/** 호가에서 고른 가격 — 고를 때마다 주문 폼을 새로 마운트(key)해 그 가격으로 시작한다 */
export interface OrderDraft {
  symbol: string
  price: number
  nonce: number
}

/** 주문 — 매수·매도, 지정가·시장가. 토스증권 실계좌로 나가므로 확인 창을 한 번 더 띄운다. */
export default function OrderTab({ snap, initialPrice, version, onPlaced }: {
  snap: StockSnapshot
  initialPrice?: number
  version: number
  onPlaced: (order: Order) => void
}) {
  const [side, setSide] = useState<Side>('buy')
  const [orderType, setOrderType] = useState<'limit' | 'market'>('limit')
  // 종목이 바뀌거나 호가에서 가격을 고르면 key 로 다시 마운트된다
  const [priceText, setPriceText] = useState(String(initialPrice ?? snap.price ?? ''))
  const [qtyText, setQtyText] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; message: string; checks: string[] } | null>(null)
  const [confirm, setConfirm] = useState(false)
  const [agreed, setAgreed] = useState(false)
  const { data: info, error } = useLoad(() => investApi.orderInfo(snap.symbol), [snap.symbol, version])
  const currency = snap.currency

  const limitPrice = Number(priceText)
  const unit = orderType === 'market' ? (info?.price ?? snap.price ?? 0) : limitPrice
  const qty = Number(qtyText)
  const amount = qty > 0 && unit > 0 ? qty * unit : 0
  const tick = tickSize(limitPrice || snap.price || 0, currency, snap.security_type)
  const maxQty = !info || !unit
    ? 0
    : side === 'buy'
      ? Math.floor(Math.min(info.buying_power, info.limits.max_order_krw) / unit)
      : Math.floor(info.sellable)
  const foreign = currency !== 'KRW'

  const nudge = (steps: number) => {
    const base = limitPrice || snap.price || 0
    const step = tickSize(base, currency, snap.security_type)
    const next = Math.max(step, Math.round((base + steps * step) / step) * step)
    setPriceText(String(next))
  }

  const submit = async (confirmLive: boolean) => {
    setBusy(true)
    setResult(null)
    try {
      const order = await investApi.placeOrder({
        symbol: snap.symbol, side, order_type: orderType, quantity: qty,
        price: orderType === 'limit' ? limitPrice : null, confirm_live: confirmLive,
      })
      const filled = order.status === 'filled'
      setResult({
        ok: !['rejected', 'failed'].includes(order.status),
        message: order.status === 'failed' || order.status === 'rejected'
          ? `주문 실패: ${order.error ?? order.status}`
          : filled
            ? `체결: ${order.quantity}주 @ ${price(order.avg_fill_price, currency)}`
            : `주문 접수 (미체결) — 주문내역에서 확인하세요`,
        checks: order.checks,
      })
      setQtyText('')
      onPlaced(order)
    } catch (e) {
      setResult({ ok: false, message: e instanceof Error ? e.message : String(e), checks: e instanceof ApiError ? e.checks : [] })
    } finally {
      setBusy(false)
      setConfirm(false)
      setAgreed(false)
    }
  }

  const disabled = busy || !info || !qty || qty <= 0 || !Number.isInteger(qty) || (orderType === 'limit' && !(limitPrice > 0)) || info.halted || foreign

  return (
    <div className="order-tab">
      <div className={`side-switch ${side}`} role="tablist" aria-label="주문 방향">
        <button className={side === 'buy' ? 'on buy' : ''} onClick={() => setSide('buy')}>매수</button>
        <button className={side === 'sell' ? 'on sell' : ''} onClick={() => setSide('sell')}>매도</button>
      </div>
      {foreign && <p className="badge warn">해외 주식 주문은 아직 지원하지 않아요 (시세만 볼 수 있어요)</p>}
      {info?.halted && <p className="badge warn">전체 주문 중지 중 — 설정 &gt; 투자에서 해제</p>}
      {error && <p className="error small">{error}</p>}

      <div className="segmented small order-type">
        <button className={orderType === 'limit' ? 'on' : ''} onClick={() => setOrderType('limit')}>지정가</button>
        <button className={orderType === 'market' ? 'on' : ''} onClick={() => setOrderType('market')}>시장가</button>
      </div>

      <label className="field">
        <span>가격</span>
        {orderType === 'market' ? (
          <div className="stepper disabled">
            <span>시장가 (현재 {price(info?.price ?? snap.price, currency)})</span>
          </div>
        ) : (
          <div className="stepper">
            <button type="button" onClick={() => nudge(-1)} aria-label="한 호가 내리기">−</button>
            <input inputMode="numeric" value={grouped(priceText)} onChange={(e) => setPriceText(digits(e.target.value))} aria-label="주문 가격" />
            <button type="button" onClick={() => nudge(1)} aria-label="한 호가 올리기">+</button>
          </div>
        )}
        {orderType === 'limit' && (
          <span className="muted small">
            호가 단위 {price(tick, currency)} ·{' '}
            <button className="link" type="button" onClick={() => setPriceText(String(snap.price ?? ''))}>현재가</button>
          </span>
        )}
      </label>

      <label className="field">
        <span>수량</span>
        <div className="stepper">
          <button type="button" onClick={() => setQtyText(String(Math.max(0, (qty || 0) - 1)))} aria-label="1주 빼기">−</button>
          <input inputMode="numeric" value={grouped(qtyText)} placeholder="0" onChange={(e) => setQtyText(digits(e.target.value))} aria-label="주문 수량" />
          <button type="button" onClick={() => setQtyText(String((qty || 0) + 1))} aria-label="1주 더하기">+</button>
        </div>
        <div className="pct-buttons">
          {[0.1, 0.25, 0.5, 1].map((r) => (
            <button key={r} type="button" disabled={!maxQty} onClick={() => setQtyText(String(Math.max(1, Math.floor(maxQty * r))))}>
              {r === 1 ? '최대' : `${r * 100}%`}
            </button>
          ))}
        </div>
      </label>

      <dl className="kv order-summary">
        <dt>{side === 'buy' ? '매수 가능' : '판매 가능'}</dt>
        <dd>{info ? (side === 'buy' ? won(info.buying_power) : `${info.sellable.toLocaleString('ko-KR')}주`) : '–'}</dd>
        <dt>최대 수량</dt>
        <dd>{maxQty.toLocaleString('ko-KR')}주{side === 'buy' && info ? ` (건당 한도 ${won(info.limits.max_order_krw)})` : ''}</dd>
        <dt>주문 금액</dt>
        <dd className="strong">{amount ? won(amount) : '–'}</dd>
      </dl>

      <button className={`order-submit ${side}`} disabled={disabled} onClick={() => setConfirm(true)}>
        {busy ? '주문 중…' : side === 'buy' ? '매수' : '매도'}
      </button>
      <p className="muted small">토스증권 실계좌로 실제 주문이 나가요. 누르면 확인 창이 한 번 더 떠요.</p>

      {result && (
        <div className={`order-result ${result.ok ? 'ok' : 'fail'}`} role="status">
          <strong>{result.message}</strong>
          {result.checks.length > 0 && (
            <ul className="small">
              {result.checks.map((c) => (
                <li key={c} className={c.startsWith('✗') ? 'neg' : 'muted'}>{c}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {confirm && (
        <Modal title="주문 확인" onClose={() => setConfirm(false)}>
          <p className="badge mode-live">실제 돈으로 주문됩니다</p>
          <dl className="kv">
            <dt>종목</dt>
            <dd>{snap.name} ({snap.symbol})</dd>
            <dt>주문</dt>
            <dd className={side === 'buy' ? 'up' : 'down'}>{side === 'buy' ? '매수' : '매도'} · {orderType === 'limit' ? `지정가 ${price(limitPrice, currency)}` : '시장가'}</dd>
            <dt>수량</dt>
            <dd>{qty.toLocaleString('ko-KR')}주</dd>
            <dt>예상 금액</dt>
            <dd className="strong">{won(amount)}</dd>
          </dl>
          <label className="check">
            <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
            토스증권 계좌로 실제 주문이 나가는 것을 확인했습니다
          </label>
          <div className="form-actions">
            <button onClick={() => setConfirm(false)}>취소</button>
            <button className={`order-submit ${side}`} disabled={!agreed || busy} onClick={() => submit(true)}>
              주문 보내기
            </button>
          </div>
        </Modal>
      )}
    </div>
  )
}
