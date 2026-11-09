/** 차트 위에서 마우스를 따라다니는 작은 설명 (차트 상자 기준 위치). 포트폴리오·소비 습관 그림이 같이 쓴다. */
import { useRef, useState, type MouseEvent, type ReactNode } from 'react'

interface Tip {
  x: number
  y: number
  body: ReactNode
  /** 상자 가장자리에 가까우면 그쪽으로 넘치지 않게 안쪽으로 펼친다 */
  edge: 'left' | 'right' | ''
}

const EDGE = 130

const edgeOf = (x: number, width: number): Tip['edge'] => (x < EDGE ? 'left' : x > width - EDGE ? 'right' : '')

export function useTip() {
  const box = useRef<HTMLDivElement>(null)
  const [tip, setTip] = useState<Tip | null>(null)
  const show = (e: MouseEvent, body: ReactNode) => {
    const r = box.current?.getBoundingClientRect()
    if (r) setTip({ x: e.clientX - r.left, y: e.clientY - r.top, body, edge: edgeOf(e.clientX - r.left, r.width) })
  }
  /** 키보드 포커스 — 요소 위쪽 가운데에 띄운다 */
  const showAt = (el: HTMLElement, body: ReactNode) => {
    const r = box.current?.getBoundingClientRect()
    const e = el.getBoundingClientRect()
    if (r) setTip({ x: e.left + e.width / 2 - r.left, y: e.top - r.top, body, edge: edgeOf(e.left + e.width / 2 - r.left, r.width) })
  }
  const node = tip && (
    <div className="viz-tip" data-edge={tip.edge || undefined} style={{ left: tip.x, top: tip.y }} role="status">
      {tip.body}
    </div>
  )
  return { box, show, showAt, hide: () => setTip(null), node }
}
