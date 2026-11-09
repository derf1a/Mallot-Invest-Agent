/**
 * 그래프 그리기 (SVG) — 노드의 col · row 칸에 놓고 선을 곡선으로 잇는다. 구조 그래프(흐름)와 관찰 그래프(트레이스)가 같이 쓴다.
 * 노드를 누르면 onNode — 에이전트 노드는 에이전트 화면으로.
 */
import { useMemo, useState } from 'react'

export interface GNode {
  id: string
  label: string
  sub?: string
  kind: string
  col: number
  row: number
  agent?: string | null
}

export interface GEdge {
  source: string
  target: string
  label?: string
  kind: string
  /** 선 굵기 (관찰 그래프: 호출 수) */
  weight?: number
}

const COL_W = 190
const ROW_H = 74
const NODE_W = 150
const NODE_H = 48
const PAD = 24

const NODE_KIND_LABEL: Record<string, string> = {
  human: '사람', agent: '에이전트', code: '코드 단계', store: '저장', data: '외부 자료',
  run: '실행', tool: '도구', model: '모델', event: '사건',
}
const EDGE_KIND_LABEL: Record<string, string> = {
  control: '차례', talk: '말 (동의 · 반박)', data: '자료', order: '주문', llm: 'LLM 호출', tool: '도구 호출', event: '사건',
}

export default function GraphView({ nodes, edges, onNode, selected }: {
  nodes: GNode[]
  edges: GEdge[]
  onNode?: (n: GNode) => void
  selected?: string | null
}) {
  const [hover, setHover] = useState<string | null>(null)
  const pos = useMemo(() => new Map(nodes.map((n) => [n.id, { x: PAD + n.col * COL_W, y: PAD + n.row * ROW_H }])), [nodes])
  const width = Math.max(...nodes.map((n) => PAD + n.col * COL_W + NODE_W), 300) + PAD
  const height = Math.max(...nodes.map((n) => PAD + n.row * ROW_H + NODE_H), 120) + PAD
  const focus = hover ?? selected ?? null
  const near = (e: GEdge) => !focus || e.source === focus || e.target === focus

  return (
    <div className="graph-scroll">
      <svg className="graph-svg" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img"
        aria-label={`노드 ${nodes.length}개, 선 ${edges.length}개`}>
        <defs>
          {Object.keys(EDGE_KIND_LABEL).map((k) => (
            <marker key={k} id={`arrow-${k}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0 0L10 5L0 10z" className={`g-arrow g-edge-${k}`} />
            </marker>
          ))}
        </defs>
        {edges.map((e, i) => {
          const a = pos.get(e.source)
          const b = pos.get(e.target)
          if (!a || !b) return null
          const forward = b.x > a.x + 1
          const x1 = forward ? a.x + NODE_W : a.x + NODE_W / 2
          const y1 = forward ? a.y + NODE_H / 2 : a.y + NODE_H
          const x2 = forward ? b.x : b.x + NODE_W / 2
          const y2 = forward ? b.y + NODE_H / 2 : b.y + (b.y >= a.y ? 0 : NODE_H)
          const bend = forward ? Math.max(40, (x2 - x1) / 2) : 0
          const d = forward
            ? `M${x1} ${y1} C${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`
            : `M${x1} ${y1} C${x1 + 60} ${(y1 + y2) / 2}, ${x2 + 60} ${(y1 + y2) / 2}, ${x2} ${y2}`
          const mx = forward ? (x1 + x2) / 2 : x1 + 48
          const my = (y1 + y2) / 2
          return (
            <g key={i} className={`g-edge g-edge-${e.kind}${near(e) ? '' : ' dim'}`}>
              <path d={d} strokeWidth={Math.min(1.2 + (e.weight ?? 1) * 0.35, 5)} markerEnd={`url(#arrow-${e.kind})`} fill="none" />
              {e.label && near(e) && (
                <text x={mx} y={my - 4} textAnchor="middle" className="g-edge-label">{e.label}</text>
              )}
            </g>
          )
        })}
        {nodes.map((n) => {
          const p = pos.get(n.id)!
          const clickable = !!onNode
          return (
            <g key={n.id} transform={`translate(${p.x} ${p.y})`}
              className={`g-node g-node-${n.kind}${selected === n.id ? ' sel' : ''}${clickable ? ' clickable' : ''}`}
              onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)}
              onClick={() => onNode?.(n)} tabIndex={clickable ? 0 : -1}
              onKeyDown={(ev) => (ev.key === 'Enter' || ev.key === ' ') && onNode?.(n)}>
              <title>{`${n.label}${n.sub ? ` — ${n.sub}` : ''} (${NODE_KIND_LABEL[n.kind] ?? n.kind})`}</title>
              <rect width={NODE_W} height={NODE_H} rx={n.kind === 'human' ? 24 : n.kind === 'store' ? 14 : 8} />
              <text x={10} y={n.sub ? 20 : 29} className="g-node-label">{clip(n.label, 17)}</text>
              {n.sub && <text x={10} y={37} className="g-node-sub">{clip(n.sub, 22)}</text>}
            </g>
          )
        })}
      </svg>
    </div>
  )
}

const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s)

export function Legend({ kinds, edgeKinds }: { kinds: string[]; edgeKinds: string[] }) {
  return (
    <div className="g-legend">
      {kinds.map((k) => (
        <span key={k} className="g-legend-item"><span className={`g-swatch g-node-${k}`} />{NODE_KIND_LABEL[k] ?? k}</span>
      ))}
      {edgeKinds.map((k) => (
        <span key={k} className="g-legend-item"><span className={`g-line g-edge-${k}`} />{EDGE_KIND_LABEL[k] ?? k}</span>
      ))}
    </div>
  )
}
