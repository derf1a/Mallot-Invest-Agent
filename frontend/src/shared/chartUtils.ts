/** 차트 공용 도우미 (컴포넌트가 아닌 것은 여기에 — Fast Refresh) */
import { useEffect, useRef, useState } from 'react'

export function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    if (!ref.current) return
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  return [ref, width] as const
}

export function niceTicks(min: number, max: number, count = 4): number[] {
  if (min === max) return [min]
  const step = (max - min) / count
  const mag = 10 ** Math.floor(Math.log10(step))
  const nice = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step) ?? step
  const start = Math.ceil(min / nice) * nice
  const ticks = []
  for (let v = start; v <= max + 1e-9; v += nice) ticks.push(Number(v.toPrecision(12)))
  return ticks
}
