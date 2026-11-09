import { useState } from 'react'

const PRESETS = [
  '💰', '🏦', '🐷', '📈', '🏠', '👪', '🛡️', '🏥',
  '📺', '🎵', '☁️', '🎮', '🚇', '🚗', '⛽', '📱',
  '💡', '🔥', '💧', '🎓', '📚', '🏋️', '💊', '🐶',
  '🍚', '☕', '🛒', '🧺', '🎉', '🍺', '✈️', '📌',
]

interface Props {
  value: string
  onChange: (icon: string) => void
  /** 비어 있을 때 보여줄 아이콘 (예: 그룹 아이콘) */
  fallback?: string
}

export default function IconPicker({ value, onChange, fallback = '📌' }: Props) {
  const [open, setOpen] = useState(false)
  const pick = (icon: string) => {
    onChange(icon)
    setOpen(false)
  }
  return (
    <div className="icon-picker">
      <button type="button" aria-label="아이콘 선택" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {value || fallback}
      </button>
      {open && (
        <div className="icon-grid">
          {PRESETS.map((icon) => (
            <button key={icon} type="button" className={icon === value ? 'on' : ''} onClick={() => pick(icon)}>
              {icon}
            </button>
          ))}
          <input
            placeholder="직접 입력 (이모지)"
            maxLength={4}
            onKeyDown={(e) => e.key === 'Enter' && pick(e.currentTarget.value.trim())}
            onBlur={(e) => e.currentTarget.value.trim() && pick(e.currentTarget.value.trim())}
          />
        </div>
      )}
    </div>
  )
}
