import { useCallback, useEffect, useState } from 'react'
import { fetchTelegramStats, publishAllPending, publishJob, sendBroadcast } from '../api'
import { useActivity } from '../activity'
import { formatChannelList } from '../targetChannels'
import ChannelManager from './ChannelManager'
import ChannelSelectModal from './ChannelSelectModal'
import JobCard, { jobKey } from './JobCard'

function formatStat(value) {
  if (value == null || value === '') return '—'
  const num = Number(value)
  if (Number.isFinite(num)) return num.toLocaleString()
  return String(value)
}

export default function TelegramHub({ refreshToken = 0 }) {
  const { pipelineBusy } = useActivity()
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [sendStatus, setSendStatus] = useState('')
  const [publishingAll, setPublishingAll] = useState(false)
  const [publishingId, setPublishingId] = useState(null)
  const [channelModal, setChannelModal] = useState(null)

  const loadStats = useCallback(async (force = false) => {
    setLoading(true)
    setError('')
    try {
      const data = await fetchTelegramStats({ force })
      setStats(data)
    } catch (err) {
      setError(err.message || 'Failed to load channel stats')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadStats(false)
  }, [loadStats, refreshToken])

  function openBroadcastPicker(e) {
    e.preventDefault()
    if (!draft.trim()) return
    setChannelModal({ mode: 'broadcast' })
  }

  function openPublishAll() {
    const pending = stats?.pending_jobs || 0
    if (!pending) return
    setChannelModal({ mode: 'all', count: pending, republish: false })
  }

  function openPublishModal(job, { republish = false } = {}) {
    const jobId = jobKey(job)
    if (!jobId || !republish) return
    setChannelModal({ mode: 'one', job, republish: true })
  }

  async function confirmChannel({
    targetChannels,
    language = 'English',
    republish = false,
  } = {}) {
    if (!channelModal) return
    const channels = Array.isArray(targetChannels) ? targetChannels : [targetChannels]
    const dest = formatChannelList(channels)
    setError('')
    if (channelModal.mode === 'broadcast') {
      setSending(true)
      setSendStatus('')
      try {
        const result = await sendBroadcast(draft.trim(), channels)
        setDraft('')
        setSendStatus(result.message || `Posted to ${dest}.`)
        setChannelModal(null)
        await loadStats(true)
      } catch (err) {
        setError(err.message || 'Broadcast failed')
      } finally {
        setSending(false)
      }
      return
    }

    if (channelModal.mode === 'one') {
      const job = channelModal.job
      const jobId = jobKey(job)
      setPublishingId(jobId)
      setSendStatus('')
      try {
        const result = await publishJob(jobId, channels, language, {
          republish: true,
        })
        setStats((prev) => {
          if (!prev) return prev
          const nextRecent = (prev.recent_published || []).map((item) =>
            jobKey(item) === jobId
              ? {
                  ...item,
                  published_to_telegram: true,
                  status: 'PUBLISHED',
                  published_url: result.posted_url || item.published_url,
                  published_at: new Date().toISOString(),
                  published_channels: result.channels || channels,
                  published_language: result.language || language,
                  publication_history:
                    result.publication_history || item.publication_history,
                }
              : item,
          )
          return { ...prev, recent_published: nextRecent }
        })
        setSendStatus(result.message || `Republished job ${jobId} to ${dest}.`)
        setChannelModal(null)
        await loadStats(true)
      } catch (err) {
        setError(err.message || 'Failed to republish job')
      } finally {
        setPublishingId(null)
      }
      return
    }

    setPublishingAll(true)
    setSendStatus('')
    try {
      const result = await publishAllPending({ targetChannels: channels, language })
      setSendStatus(result.message || `Publishing to ${dest}…`)
      setChannelModal(null)
      await loadStats(true)
    } catch (err) {
      setError(err.message || 'Failed to start publish-all')
    } finally {
      setPublishingAll(false)
    }
  }

  const recent = stats?.recent_published || []
  const widgets = [
    { label: 'Subscribers', value: formatStat(stats?.subscribers), hint: 'Channel members' },
    {
      label: 'Avg. post views',
      value: formatStat(stats?.average_post_views),
      hint: stats?.sampled_posts ? `From last ${stats.sampled_posts} posts` : 'Recent posts',
    },
    {
      label: 'Engagement',
      value: formatStat(stats?.engagement_count),
      hint: 'Reactions on sampled posts',
    },
    {
      label: 'Jobs published',
      value: formatStat(stats?.total_jobs_published),
      hint: stats?.pending_jobs ? `${stats.pending_jobs} still pending` : 'From this extractor',
    },
  ]

  return (
    <div className="hub-stack">
      <ChannelManager refreshToken={refreshToken} />

      <section className="panel telegram-hub">
        <header className="panel-header row">
          <div>
            <h2>Telegram Channel Hub</h2>
            <p>
              Analytics and outbound posts for{' '}
              <strong>{stats?.channel || stats?.target_channel || 'your target channel'}</strong>
              {stats?.channel_title ? ` · ${stats.channel_title}` : ''}.
            </p>
          </div>
          <div className="toolbar">
            <button
              type="button"
              className="btn accent"
              onClick={openPublishAll}
              disabled={loading || sending || publishingAll || pipelineBusy || !(stats?.pending_jobs)}
            >
              {publishingAll ? (
                <>
                  <span className="spinner" aria-hidden />
                  Publishing…
                </>
              ) : (
                `Publish All Unposted${stats?.pending_jobs ? ` (${stats.pending_jobs})` : ''}`
              )}
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => loadStats(true)}
              disabled={loading || sending || publishingAll}
            >
              {loading ? (
                <>
                  <span className="spinner" aria-hidden />
                  Refreshing…
                </>
              ) : (
                'Refresh stats'
              )}
            </button>
          </div>
        </header>

        {stats?.source === 'fallback' && stats?.fallback_reason && (
          <p className="status warn">
            Live Telegram insights unavailable — showing stored job counts. {stats.fallback_reason}
          </p>
        )}
        {error && <p className="status err">{error}</p>}

        <div className="stat-grid">
          {widgets.map((card) => (
            <article key={card.label} className="stat-card">
              <span className="stat-label">{card.label}</span>
              <strong className="stat-value">{loading && !stats ? '…' : card.value}</strong>
              <span className="stat-hint">{card.hint}</span>
            </article>
          ))}
        </div>
      </section>

      <div className="hub-split">
        <section className="panel hub-feed">
          <header className="panel-header">
            <h2>Recent published feed</h2>
            <p>Jobs already posted to the destination channel.</p>
          </header>
          {recent.length === 0 && (
            <p className="muted empty">No published jobs yet. Publish from the Extractor Dashboard.</p>
          )}
          <ul className="published-list">
            {recent.map((job) => {
              const jobId = jobKey(job)
              return (
                <JobCard
                  key={jobId}
                  job={job}
                  compact
                  publishing={publishingId === jobId}
                  publishBusy={sending || publishingAll || pipelineBusy || Boolean(publishingId)}
                  onOpenPublishModal={openPublishModal}
                  onPublish={openPublishModal}
                />
              )
            })}
          </ul>
        </section>

        <section className="panel broadcast-panel">
          <header className="panel-header">
            <h2>Manual broadcast</h2>
            <p>Send a custom Markdown announcement to test the channel.</p>
          </header>
          <form className="broadcast-form" onSubmit={openBroadcastPicker}>
            <label className="field">
              <span>Draft</span>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder={'*Hiring this week*\nNew roles are live on the board.\n\n#Jobs #Armenia'}
                rows={8}
                disabled={sending || pipelineBusy}
                maxLength={4000}
              />
            </label>
            <div className="actions">
              <button
                type="submit"
                className="btn accent"
                disabled={sending || pipelineBusy || !draft.trim()}
              >
                {sending ? (
                  <>
                    <span className="spinner" aria-hidden />
                    Sending…
                  </>
                ) : (
                  'Send to Telegram'
                )}
              </button>
            </div>
          </form>
          {sendStatus && <p className="status ok">{sendStatus}</p>}
        </section>
      </div>

      <ChannelSelectModal
        isOpen={Boolean(channelModal)}
        republishMode={Boolean(channelModal?.republish)}
        jobId={channelModal?.job ? jobKey(channelModal.job) : undefined}
        title={
          channelModal?.republish
            ? 'Republish to Telegram'
            : channelModal?.mode === 'broadcast'
              ? 'Send announcement'
              : 'Publish all unposted jobs'
        }
        description={
          channelModal?.republish
            ? `Re-send “${channelModal?.job?.title || 'this job'}” to boost visibility or reach new channels.`
            : channelModal?.mode === 'broadcast'
              ? 'Choose the destination channels that should receive this Markdown announcement.'
              : `Send ${channelModal?.count || 0} pending job${channelModal?.count === 1 ? '' : 's'} to Telegram.`
        }
        confirmLabel={
          channelModal?.republish
            ? 'Republish Now'
            : channelModal?.mode === 'broadcast'
              ? 'Send Now'
              : 'Publish Now'
        }
        busy={sending || publishingAll || Boolean(publishingId)}
        onCancel={() => {
          if (!sending && !publishingAll && !publishingId) setChannelModal(null)
        }}
        onConfirm={confirmChannel}
      />
    </div>
  )
}
