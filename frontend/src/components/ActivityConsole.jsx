import { useEffect, useRef, useState } from 'react'
import { useActivity } from '../activity'

const STAGE_META = {
  CONNECTED: { label: 'System', tone: 'system' },
  DISCOVER_STARTED: { label: 'Pipeline', tone: 'info' },
  JOINING_TELEGRAM: { label: 'Telegram', tone: 'telegram' },
  FETCHING_POSTS: { label: 'Telegram', tone: 'telegram' },
  CALLING_OLLAMA: { label: 'Ollama AI', tone: 'ollama' },
  DISCOVERED_CATEGORIES: { label: 'Success', tone: 'success' },
  EXTRACTION_QUEUED: { label: 'Pipeline', tone: 'info' },
  EXTRACTION_STARTED: { label: 'Pipeline', tone: 'info' },
  EXTRACTION_PROGRESS: { label: 'Ollama AI', tone: 'ollama' },
  JOB_SAVED: { label: 'Success', tone: 'success' },
  EXTRACTION_DONE: { label: 'Success', tone: 'success' },
  ERROR: { label: 'Error', tone: 'error' },
}

function formatTime(ts) {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleTimeString()
  } catch {
    return ''
  }
}

export default function ActivityConsole() {
  const { logs, connected, clearLogs } = useActivity()
  const [open, setOpen] = useState(true)
  const scrollerRef = useRef(null)

  useEffect(() => {
    if (!open) return
    const el = scrollerRef.current
    if (el) {
      el.scrollTop = el.scrollHeight
    }
  }, [logs, open])

  return (
    <section className={`activity-console ${open ? 'open' : 'collapsed'}`}>
      <header className="activity-header">
        <button type="button" className="activity-toggle" onClick={() => setOpen((v) => !v)}>
          <span className={`pulse ${connected ? 'on' : 'off'}`} aria-hidden />
          <strong>Live Activity Log</strong>
          <span className="muted">{connected ? 'connected' : 'reconnecting…'}</span>
          <span className="chevron">{open ? '▾' : '▸'}</span>
        </button>
        {open && (
          <button type="button" className="btn tiny" onClick={clearLogs}>
            Clear
          </button>
        )}
      </header>

      {open && (
        <div className="activity-body" ref={scrollerRef}>
          {logs.length === 0 && (
            <p className="muted empty-log">Waiting for pipeline events…</p>
          )}
          <ul className="activity-list">
            {logs.map((entry, idx) => {
              const meta = STAGE_META[entry.stage] || { label: entry.stage, tone: 'info' }
              const spinning =
                entry.stage === 'CALLING_OLLAMA' ||
                entry.stage === 'EXTRACTION_PROGRESS' ||
                entry.stage === 'JOINING_TELEGRAM' ||
                entry.stage === 'FETCHING_POSTS'
              return (
                <li key={`${entry.ts}-${idx}`} className={`activity-row tone-${meta.tone}`}>
                  <span className="activity-time">{formatTime(entry.ts)}</span>
                  <span className={`badge tone-${meta.tone}`}>
                    {spinning && idx === logs.length - 1 ? (
                      <span className="spinner tiny" aria-hidden />
                    ) : null}
                    {meta.label}
                  </span>
                  <span className="activity-msg">{entry.message}</span>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </section>
  )
}
