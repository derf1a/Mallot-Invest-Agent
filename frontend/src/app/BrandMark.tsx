import { useId } from 'react'

/** Mallot 마크: 오른쪽 봉우리가 더 높은 'M' 차트 선 + 끝점 — 나눠 배정한(allot) 돈이 자라는 모양 */
export default function BrandMark({ size = 30 }: { size?: number }) {
  const id = useId().replace(/:/g, '')
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" role="img" aria-label="Mallot" className="brand-mark">
      <defs>
        <linearGradient id={`${id}-bg`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#1f8f72" />
          <stop offset="1" stopColor="#0d3b3a" />
        </linearGradient>
        <linearGradient id={`${id}-line`} x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0.75" />
          <stop offset="1" stopColor="#ffffff" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill={`url(#${id}-bg)`} />
      <rect x="0.5" y="0.5" width="31" height="31" rx="8.5" fill="none" stroke="#ffffff" strokeOpacity="0.12" />
      <path
        d="M8 23 L8 13 L15.2 19.5 L24 8.5 L24 23"
        fill="none"
        stroke={`url(#${id}-line)`}
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="24" cy="8.5" r="4.2" fill="#b8f5dd" fillOpacity="0.18" />
      <circle cx="24" cy="8.5" r="2.3" fill="#b8f5dd" />
    </svg>
  )
}
