/** 긴 글(프롬프트 · mermaid) 칸 + 복사 버튼 */
import { useState } from 'react'

export default function CodeBlock({ text, note }: { text: string; note?: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="code-block">
      <div className="code-block-head">
        {note && <span className="muted small">{note}</span>}
        <button className="small" onClick={() => navigator.clipboard?.writeText(text).then(() => setCopied(true))}>
          {copied ? '복사했어요' : '복사'}
        </button>
      </div>
      <pre>{text}</pre>
    </div>
  )
}
