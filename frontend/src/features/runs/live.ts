import { useEffect, useRef, useState } from 'react'
import { labApi, type Metrics, type Run, type RunLog, type RunStatus } from './api'

export const STATUS_LABEL: Record<RunStatus, string> = {
  queued: '대기',
  running: '실행 중',
  succeeded: '성공',
  failed: '실패',
  cancelled: '취소',
}

export const isActive = (s?: RunStatus) => s === 'queued' || s === 'running'

/**
 * 실행 하나를 실시간으로 따라간다: 실행 중이면 1.5초마다 상태·지표·새 로그를 가져온다.
 * 다른 실행으로 바꿀 때는 이 훅을 쓰는 컴포넌트를 key 로 다시 마운트한다 (상태 초기화).
 */
export function useRunLive(runId: number | null) {
  const [run, setRun] = useState<Run | null>(null)
  const [metrics, setMetrics] = useState<Metrics>({})
  const [logs, setLogs] = useState<RunLog[]>([])
  const lastLog = useRef(0)

  useEffect(() => {
    if (runId === null) return
    let alive = true
    let timer: number | undefined
    const tick = async () => {
      try {
        const [r, m, l] = await Promise.all([labApi.run(runId), labApi.metrics(runId), labApi.logs(runId, lastLog.current)])
        if (!alive) return
        setRun(r)
        setMetrics(m)
        if (l.length) {
          lastLog.current = l[l.length - 1].id
          setLogs((prev) => [...prev, ...l])
        }
        if (isActive(r.status)) timer = window.setTimeout(tick, 1500)
      } catch {
        if (alive) timer = window.setTimeout(tick, 4000)
      }
    }
    tick()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [runId])
  return { run, metrics, logs }
}


/** 실행이 끝날 때까지 기다린다 (패널 없이 시작만 하는 곳에서 사용) */
export async function waitForRun(runId: number, intervalMs = 1000): Promise<Run> {
  for (;;) {
    const run = await labApi.run(runId)
    if (!isActive(run.status)) return run
    await new Promise((resolve) => setTimeout(resolve, intervalMs))
  }
}
