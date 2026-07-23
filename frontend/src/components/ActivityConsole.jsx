import { useEffect, useRef, useState } from 'react'
import { useActivity } from '../activity'

const STAGE_META = {
  CONNECTED: { label: 'System', tone: 'system' },
  DISCOVER_STARTED: { label: 'Pipeline', tone: 'info' },
  JOINING_TELEGRAM: { label: 'Telegram', tone: 'telegram' },
  FETCHING_POSTS: { label: 'Telegram', tone: 'telegram' },
  LINK_SCRAPER: { label: 'Link Scraper', tone: 'info' },
  CALLING_OLLAMA: { label: 'Ollama AI', tone: 'ollama' },
  FALLBACK_ENGINE: { label: 'Fallback', tone: 'warn' },
  DISCOVERED_CATEGORIES: { label: 'Success', tone: 'success' },
  EXTRACTION_QUEUED: { label: 'Pipeline', tone: 'info' },
  EXTRACTION_STARTED: { label: 'Pipeline', tone: 'info' },
  EXTRACTION_PROGRESS: { label: 'Ollama AI', tone: 'ollama' },
  JOB_SAVED: { label: 'Success', tone: 'success' },
  EXTRACTION_DONE: { label: 'Success', tone: 'success' },
  EXTRACTION_STOP_REQUESTED: { label: 'Stop', tone: 'warn' },
  EXTRACTION_STOPPED: { label: 'Stopped', tone: 'error' },
  ERROR: { label: 'Error', tone: 'error' },
}

const CHANNEL_RE = /@[A-Za-z0-9_]+/g

function formatTime(ts) {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleTimeString()
  } catch {
    return ''
  }
}

function MessageWithChannelChips({ message }) {
  if (!message) return null
  const parts = []
  let last = 0
  let match
  const re = new RegExp(CHANNEL_RE)
  while ((match = re.exec(message)) !== null) {
    if (match.index > last) {
      parts.push({ type: 'text', value: message.slice(last, match.index) })
    }
    parts.push({ type: 'channel', value: match[0] })
    last = match.index + match[0].length
  }
  if (last < message.length) {
    parts.push({ type: 'text', value: message.slice(last) })
  }
  if (parts.length === 0) {
    return <span className="activity-msg">{message}</span>
  }
  return (
    <span className="activity-msg">
      {parts.map((part, i) =>
        part.type === 'channel' ? (
          <span key={`${part.value}-${i}`} className="log-channel-chip">
            {part.value}
          </span>
        ) : (
          <span key={`t-${i}`}>{part.value}</span>
        ),
      )}
    </span>
  )
}

function isExpandable(entry) {
  const data = entry?.data || {}
  if (data.expandable) return true
  if (entry.stage === 'ERROR' && (data.channels_in_chunk?.length || data.channels?.length)) {
    return true
  }
  if (entry.stage === 'CALLING_OLLAMA' && data.empty_result) return true
  return false
}

function ChunkDetail({ data }) {
  const channels = data.channels_in_chunk || data.channels || []
  const snippets = data.sample_snippets || []
  const categories = data.categories || data.extracted_categories || []
  const titles = data.titles || data.extracted_titles || []

  return (
    <div className="activity-detail">
      {data.chunk_id && (
        <p>
          <strong>Chunk</strong> {data.chunk_id}
          {typeof data.post_count === 'number' ? ` · ${data.post_count} posts` : ''}
        </p>
      )}
      {channels.length > 0 && (
        <p className="detail-channels">
          <strong>Channels</strong>{' '}
          {channels.map((ch) => (
            <span key={ch} className="log-channel-chip">
              {ch}
            </span>
          ))}
        </p>
      )}
      {data.error && <p className="detail-error">{data.error}</p>}
      {categories.length > 0 && (
        <p>
          <strong>Categories</strong> [{categories.join(', ')}]
        </p>
      )}
      {titles.length > 0 && (
        <p>
          <strong>Titles</strong> [{titles.join(', ')}]
        </p>
      )}
      {snippets.length > 0 && (
        <ul className="detail-snippets">
          {snippets.map((s, i) => (
            <li key={`${s.channel}-${i}`}>
              <span className="log-channel-chip">{s.channel}</span>
              <span className="snippet-text">{s.preview}</span>
            </li>
          ))}
        </ul>
      )}
      {channels.length === 0 && snippets.length === 0 && !data.error && (
        <p className="muted">No extra chunk payload on this event.</p>
      )}
    </div>
  )
}

function ActivityRow({ entry, idx, isLatest }) {
  const meta = STAGE_META[entry.stage] || { label: entry.stage, tone: 'info' }
  const spinning =
    entry.stage === 'CALLING_OLLAMA' ||
    entry.stage === 'EXTRACTION_PROGRESS' ||
    entry.stage === 'JOINING_TELEGRAM' ||
    entry.stage === 'FETCHING_POSTS' ||
    entry.stage === 'LINK_SCRAPER'
  const expandable = isExpandable(entry)
  const [expanded, setExpanded] = useState(false)
  const emptyTone = entry.data?.empty_result ? 'empty' : ''

  return (
    <li className={`activity-row tone-${meta.tone} ${emptyTone}`}>
      <span className="activity-time">{formatTime(entry.ts)}</span>
      <span className={`badge tone-${meta.tone}`}>
        {spinning && isLatest ? <span className="spinner tiny" aria-hidden /> : null}
        {meta.label}
      </span>
      <div className="activity-msg-wrap">
        <div className="activity-msg-line">
          <MessageWithChannelChips message={entry.message} />
          {expandable && (
            <button
              type="button"
              className="btn tiny activity-expand"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
            >
              {expanded ? 'Hide' : 'Inspect'}
            </button>
          )}
        </div>
        {expanded && <ChunkDetail data={entry.data || {}} />}
      </div>
    </li>
  )
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
            Clear Logs
          </button>
        )}
      </header>

      {open && (
        <div className="activity-body" ref={scrollerRef}>
          {logs.length === 0 && (
            <p className="muted empty-log">Waiting for pipeline events…</p>
          )}
          <ul className="activity-list">
            {logs.map((entry, idx) => (
              <ActivityRow
                key={`${entry.ts}-${idx}`}
                entry={entry}
                idx={idx}
                isLatest={idx === logs.length - 1}
              />
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
