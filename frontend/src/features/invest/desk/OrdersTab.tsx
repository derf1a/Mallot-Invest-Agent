import { useState } from 'react'
import { useLoad } from '../../../shared/useLoad'
import { investApi, type Order } from '../api'
import { ORDER_STATUS, price, useTicker } from '../format'

/** 주문내역 — 미체결 취소, 에이전트 주문 승인·거절 (토스증권 실계좌) */
export default function OrdersTab({ version, onChanged }: { version: number; onChanged: () => void }) {
  const [filter, setFilter] = useState<'all' | 'open' | 'closed'>('all')
  const tick = useTicker(10_000)
  const { data: orders, error } = useLoad(() => investApi.orders(filter), [filter, version, tick])

  return (
    <div className="orders-tab">
      <div className="segmented small">
        {(['all', 'open', 'closed'] as const).map((f) => (
          <button key={f} className={filter === f ? 'on' : ''} onClick={() => setFilter(f)}>
            {{ all: '전체', open: '진행 중', closed: '종료' }[f]}
          </button>
        ))}
      </div>
      {error && <p className="error small">{error}</p>}
      {orders && orders.length === 0 && <p className="muted small">주문이 없어요.</p>}
      <ul className="order-list">
        {orders?.map((o) => <OrderRow key={o.id} order={o} onChanged={onChanged} />)}
      </ul>
    </div>
  )
}

export function OrderRow({ order: o, onChanged }: { order: Order; onChanged: () => void }) {
  const [open, setOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const act = async (fn: () => Promise<Order>) => {
    setError(null)
    try {
      await fn()
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }
  const approve = () => {
    if (!window.confirm(`실계좌 주문입니다. ${o.name} ${o.side === 'buy' ? '매수' : '매도'} ${o.quantity}주를 토스증권으로 보낼까요?`)) return
    act(() => investApi.approve(o.id, true))
  }
  return (
    <li className={`order-item status-${o.status}`}>
      <button className="order-line" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className={`side-chip ${o.side}`}>{o.side === 'buy' ? '매수' : '매도'}</span>
        <span className="o-name">
          <strong>{o.name || o.symbol}</strong>
          <span className="muted small">
            {o.quantity}주 · {o.order_type === 'limit' ? `지정가 ${price(o.price)}` : '시장가'}
            {o.avg_fill_price ? ` · 체결 ${price(o.avg_fill_price)}` : ''}
          </span>
        </span>
        <span className="o-meta">
          <span className={`chip order-${o.status}`}>{ORDER_STATUS[o.status]}</span>
          <span className="muted small">
            {o.source === 'agent' ? `에이전트 #${o.run_id ?? '?'}` : '수동'} · {o.created_at.slice(5, 16)}
          </span>
        </span>
      </button>
      {(o.status === 'pending_approval' || o.status === 'open' || o.status === 'partial') && (
        <div className="order-actions">
          {o.status === 'pending_approval' ? (
            <>
              <button className="primary" onClick={approve}>승인</button>
              <button onClick={() => act(() => investApi.reject(o.id))}>거절</button>
            </>
          ) : (
            <button onClick={() => act(() => investApi.cancel(o.id))}>취소</button>
          )}
        </div>
      )}
      {error && <p className="error small">{error}</p>}
      {open && (
        <div className="order-detail small">
          {o.error && <p className="neg">{o.error}</p>}
          {o.reason && <p>근거: {o.reason}</p>}
          {o.checks.length > 0 && (
            <ul>
              {o.checks.map((c) => (
                <li key={c} className={c.startsWith('✗') ? 'neg' : 'muted'}>{c}</li>
              ))}
            </ul>
          )}
          {o.broker_order_id && <p className="muted mono">토스 주문번호 {o.broker_order_id}</p>}
        </div>
      )}
    </li>
  )
}
