import { useState } from 'react'

export function jobKey(job) {
  return job?.id || String(job?.message_id || '')
}

export function isPublishedJob(job) {
  if (!job) return false
  const flag = job.published_to_telegram
  if (flag === true || flag === 1 || flag === 'true' || flag === 'True') return true
  return String(job.status || '').trim().toUpperCase() === 'PUBLISHED'
}

function formatDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

function formatRelative(iso) {
  if (!iso) return ''
  const then = new Date(iso)
  if (Number.isNaN(then.getTime())) return String(iso)
  const seconds = Math.round((Date.now() - then.getTime()) / 1000)
  const abs = Math.abs(seconds)
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  if (abs < 60) return rtf.format(-seconds, 'second')
  const minutes = Math.round(seconds / 60)
  if (Math.abs(minutes) < 60) return rtf.format(-minutes, 'minute')
  const hours = Math.round(minutes / 60)
  if (Math.abs(hours) < 24) return rtf.format(-hours, 'hour')
  const days = Math.round(hours / 24)
  if (Math.abs(days) < 30) return rtf.format(-days, 'day')
  return then.toLocaleDateString()
}

function isTelegramUrl(url) {
  try {
    const host = new URL(url).hostname.toLowerCase()
    return host === 't.me' || host.endsWith('.t.me') || host === 'telegram.me'
  } catch {
    return /t\.me\//i.test(url || '')
  }
}

function publicationHistory(job) {
  return Array.isArray(job?.publication_history) ? job.publication_history : []
}

function lastPublication(job) {
  const history = publicationHistory(job)
  if (history.length) return history[history.length - 1]
  const channels = Array.isArray(job?.published_channels) ? job.published_channels : []
  if (!job?.published_at && !channels.length) return null
  return {
    channel: channels[0] || '',
    language: job?.published_language || 'English',
    published_at: job?.published_at || '',
  }
}

function historyTooltip(job) {
  const history = publicationHistory(job)
  if (history.length) {
    return history
      .map((entry) => {
        const when = formatDate(entry.published_at)
        const channel = entry.channel || 'unknown channel'
        const language = entry.language || 'English'
        return `${channel} · ${language} · ${when}`
      })
      .join('\n')
  }
  const last = lastPublication(job)
  if (!last) return 'Published to Telegram'
  const dest = last.channel ? ` to ${last.channel}` : ''
  return `Last published${dest} ${formatDate(last.published_at)}`
}

function PublicationHistoryTag({ job }) {
  const [open, setOpen] = useState(false)
  const history = publicationHistory(job)
  const last = lastPublication(job)
  if (!last) return null

  const dest = last.channel ? ` to ${last.channel}` : ''
  const when = last.published_at ? formatRelative(last.published_at) : ''
  const count = history.length

  return (
    <div className="publish-history">
      <button
        type="button"
        className={`publish-history-tag ${open ? 'open' : ''}`}
        title={historyTooltip(job)}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        Last published{dest}
        {when ? ` · ${when}` : ''}
        {count > 1 ? ` · ${count} posts` : ''}
      </button>
      {open && count > 0 && (
        <ul className="publish-history-list">
          {[...history].reverse().map((entry, index) => (
            <li key={`${entry.channel}-${entry.published_at}-${index}`}>
              <strong>{entry.channel || 'Unknown channel'}</strong>
              <span>
                {entry.language || 'English'}
                {entry.published_at ? ` · ${formatDate(entry.published_at)}` : ''}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function JobCard({
  job,
  selected = false,
  busy = false,
  deleting = false,
  publishing = false,
  publishBusy = false,
  compact = false,
  onToggleSelected,
  onDelete,
  onOpenPublishModal,
  onPublish,
  onRepublish,
}) {
  const jobId = jobKey(job)
  const published = isPublishedJob(job)

  function openPublishModal(options = {}) {
    if (typeof onOpenPublishModal === 'function') {
      onOpenPublishModal(job, options)
      return
    }
    if (options.republish && typeof onRepublish === 'function') {
      onRepublish(job, options)
      return
    }
    onPublish?.(job, options)
  }

  return (
    <li className={`job-card ${compact ? 'compact' : ''} ${selected ? 'selected' : ''}`}>
      <div className="job-top">
        {!compact && (
          <label className="job-select">
            <input
              type="checkbox"
              checked={selected}
              onChange={() => onToggleSelected?.(jobId)}
              disabled={busy || !jobId}
              aria-label={`Select ${job.title || jobId}`}
            />
          </label>
        )}
        <h3>{job.title || 'Untitled'}</h3>
        {job.category && <span className="badge">{job.category}</span>}
        <span className={`badge ${published ? 'published' : 'pending'}`}>
          {published ? 'Published' : 'Pending'}
        </span>
        {!compact && (
          <button
            type="button"
            className="btn tiny danger-outline job-delete"
            onClick={() => onDelete?.(job)}
            disabled={busy || deleting}
            aria-label={`Delete ${job.title || jobId}`}
          >
            {deleting ? '…' : 'Delete'}
          </button>
        )}
      </div>
      <div className="job-meta">
        <time dateTime={job.published_at || job.date || undefined}>
          {formatDate(job.published_at || job.date)}
        </time>
        {job.company && <span>· {job.company}</span>}
        {job.channel && <span>· {job.channel}</span>}
      </div>
      {published && <PublicationHistoryTag job={job} />}
      <p className="summary">{job.translated_summary || 'No summary available.'}</p>
      <div className="links job-actions">
        {published ? (
          <>
            <button
              type="button"
              className="btn btn-republish"
              onClick={() => onOpenPublishModal(job, { republish: true })}
              disabled={publishBusy || publishing}
            >
              {publishing ? (
                <>
                  <span className="spinner tiny" aria-hidden />
                  Republishing…
                </>
              ) : (
                '🔄 Republish'
              )}
            </button>
            {(job.published_url || job.telegram_url) && (
              <a
                className="btn link tg"
                href={job.published_url || job.telegram_url}
                target="_blank"
                rel="noopener noreferrer"
              >
                View published post
              </a>
            )}
            {(job.apply_links || [])
              .filter((url) => url && url !== job.published_url)
              .map((url) => (
                <a
                  key={url}
                  className={`btn link ${isTelegramUrl(url) ? 'tg' : ''}`}
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Apply Here
                </a>
              ))}
          </>
        ) : (
          <>
            <button
              type="button"
              className="btn tiny accent"
              onClick={() => openPublishModal({ republish: false })}
              disabled={publishBusy || publishing}
            >
              {publishing ? (
                <>
                  <span className="spinner tiny" aria-hidden />
                  Publishing…
                </>
              ) : (
                'Publish to Telegram'
              )}
            </button>
            {(job.apply_links || []).map((url) => (
              <a
                key={url}
                className={`btn link ${isTelegramUrl(url) ? 'tg' : ''}`}
                href={url}
                target="_blank"
                rel="noopener noreferrer"
              >
                Apply Here
              </a>
            ))}
          </>
        )}
      </div>
    </li>
  )
}
