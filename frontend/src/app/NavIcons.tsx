/** 사이드바 아이콘 (선 아이콘, currentColor) */
import type { ReactNode } from 'react'

const Svg = ({ children }: { children: ReactNode }) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {children}
  </svg>
)

/** 예산 배정: 길이가 다른 막대 셋 */
export const InvestIcon = () => (
  <Svg>
    <path d="M3 17l6-6 4 4 8-8" />
    <path d="M14 7h7v7" />
  </Svg>
)

export const SettingsIcon = () => (
  <Svg>
    <path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1" />
    <circle cx="15" cy="6" r="2" />
    <circle cx="9" cy="12" r="2" />
    <circle cx="17" cy="18" r="2" />
  </Svg>
)

export const PortfolioIcon = () => (
  <Svg>
    <path d="M12 3a9 9 0 1 0 9 9h-9z" />
    <path d="M15 3.5A9 9 0 0 1 20.5 9H15z" />
  </Svg>
)

/** 거래 기록 (영수증에 위아래 화살표) */
export const TradesIcon = () => (
  <Svg>
    <path d="M6 3h12v18l-3-2-3 2-3-2-3 2z" />
    <path d="M10 8l-1.5-1.5L7 8M8.5 6.5V12M14 11l1.5 1.5L17 11M15.5 12.5V7" />
  </Svg>
)

export const AiIcon = () => (
  <Svg>
    <path d="M4 5h16v11H9l-5 4z" />
    <path d="M12 8.2l.8 1.8 1.8.8-1.8.8-.8 1.8-.8-1.8-1.8-.8 1.8-.8z" />
  </Svg>
)

/** 소비 습관 (전구) */

export const TeamIcon = () => (
  <Svg>
    <circle cx="9" cy="8" r="3" />
    <circle cx="17" cy="9" r="2.4" />
    <path d="M3.5 19c.6-3.2 2.9-5 5.5-5s4.9 1.8 5.5 5M14.5 14.6c.8-.4 1.6-.6 2.5-.6 2.1 0 3.8 1.4 4.3 4" />
  </Svg>
)

export const AgentsIcon = () => (
  <Svg>
    <rect x="4" y="7" width="16" height="12" rx="3" />
    <path d="M12 3v4M9 12h.01M15 12h.01M9.5 15.5h5" />
  </Svg>
)

export const GraphIcon = () => (
  <Svg>
    <circle cx="5" cy="6" r="2" />
    <circle cx="19" cy="6" r="2" />
    <circle cx="12" cy="18" r="2" />
    <path d="M7 6h10M6.2 7.7 10.9 16.3M17.8 7.7 13.1 16.3" />
  </Svg>
)

export const TraceIcon = () => (
  <Svg>
    <path d="M4 5h9M7 10h11M10 15h6M6 20h8" />
    <path d="M4 3v18" />
  </Svg>
)

