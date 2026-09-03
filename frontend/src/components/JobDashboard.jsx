import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  bulkDeleteJobs,
  clearAllJobs,
  deleteJob,
  fetchCategories,
  fetchJobs,
  publishAllPending,
  publishJob,
} from '../api'
import { useActivity } from '../activity'
import { formatChannelList } from '../targetChannels'
import ChannelSelectModal from './ChannelSelectModal'
import JobCard, { isPublishedJob, jobKey } from './JobCard'

const SORT_OPTIONS = [
  { value: 'date_desc', label: 'Newest First' },
  { value: 'date_asc', label: 'Oldest First' },
  { value: 'category_asc', label: 'Category Name' },
  { value: 'title_asc', label: 'Job Title' },
]

const DATE_PRESETS = [
  { value: 'all_time', label: 'All Time' },
  { value: 'today', label: 'Today' },
  { value: 'this_week', label: 'This Week' },
  { value: 'this_month', label: 'This Month' },
  { value: 'custom', label: 'Custom' },
]

function sortJobsClient(jobs, sortBy) {
  const list = [...jobs]
  switch (sortBy) {
    case 'date_asc':
      return list.sort((a, b) => String(a.date || '').localeCompare(String(b.date || '')))
    case 'category_asc':
      return list.sort((a, b) =>
        String(a.category || '')
          .toLowerCase()
          .localeCompare(String(b.category || '').toLowerCase()),
      )
    case 'title_asc':
      return list.sort((a, b) =>
        String(a.title || '')
          .toLowerCase()
          .localeCompare(String(b.title || '').toLowerCase()),
      )
    case 'date_desc':
    default:
      return list.sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')))
  }
}

export default function JobDashboard({ refreshToken = 0 }) {
  const { pipelineBusy } = useActivity()
  const [jobs, setJobs] = useState([])
  const [categories, setCategories] = useState([])
  const [category, setCategory] = useState('')
  const [sortBy, setSortBy] = useState('date_desc')
  const [datePreset, setDatePreset] = useState('all_time')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [deletingId, setDeletingId] = useState(null)
  const [clearing, setClearing] = useState(false)
  const [selectedJobIds, setSelectedJobIds] = useState(() => new Set())
  const [bulkDeleting, setBulkDeleting] = useState(false)
  const [publishingId, setPublishingId] = useState(null)
  const [publishingAll, setPublishingAll] = useState(false)
  const [publishNote, setPublishNote] = useState('')
  const [channelModal, setChannelModal] = useState(null)

  const refreshAll = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [jobsData, catsData] = await Promise.all([
        fetchJobs({
          category: category || undefined,
          sortBy,
          preset: datePreset,
          startDate: datePreset === 'custom' ? startDate || undefined : undefined,
          endDate: datePreset === 'custom' ? endDate || undefined : undefined,
        }),
        fetchCategories(),
      ])
      const nextJobs = jobsData.jobs || []
      setJobs(nextJobs)
      setCategories(catsData.categories || [])
      const nextCats = catsData.categories || []
      if (category && !nextCats.includes(category)) {
        setCategory('')
      }
      const visibleKeys = new Set(nextJobs.map(jobKey).filter(Boolean))
      setSelectedJobIds((prev) => {
        const pruned = new Set([...prev].filter((id) => visibleKeys.has(id)))
        return pruned.size === prev.size ? prev : pruned
      })
    } catch (err) {
      setError(err.message || 'Failed to refresh feed')
    } finally {
      setLoading(false)
    }
  }, [category, sortBy, datePreset, startDate, endDate])

  useEffect(() => {
    refreshAll()
  }, [refreshAll, refreshToken])

  const visibleJobs = useMemo(() => sortJobsClient(jobs, sortBy), [jobs, sortBy])
  const visibleIds = useMemo(
    () => visibleJobs.map(jobKey).filter(Boolean),
    [visibleJobs],
  )
  const selectedCount = selectedJobIds.size
  const allVisibleSelected =
    visibleIds.length > 0 && visibleIds.every((id) => selectedJobIds.has(id))
  const someVisibleSelected =
    visibleIds.some((id) => selectedJobIds.has(id)) && !allVisibleSelected

  function toggleJobSelected(jobId) {
    if (!jobId) return
    setSelectedJobIds((prev) => {
      const next = new Set(prev)
      if (next.has(jobId)) next.delete(jobId)
      else next.add(jobId)
      return next
    })
  }

  function toggleSelectAllVisible() {
    setSelectedJobIds((prev) => {
      if (allVisibleSelected) {
        const next = new Set(prev)
        visibleIds.forEach((id) => next.delete(id))
        return next
      }
      const next = new Set(prev)
      visibleIds.forEach((id) => next.add(id))
      return next
    })
  }

  async function handleDelete(job) {
    const jobId = jobKey(job)
    if (!jobId) return
    const ok = window.confirm(`Delete job “${job.title || jobId}”?`)
    if (!ok) return

    setDeletingId(jobId)
    setJobs((prev) => prev.filter((j) => jobKey(j) !== jobId))
    setSelectedJobIds((prev) => {
      if (!prev.has(jobId)) return prev
      const next = new Set(prev)
      next.delete(jobId)
      return next
    })
    try {
      await deleteJob(jobId)
      const catsData = await fetchCategories()
      setCategories(catsData.categories || [])
      if (category && !(catsData.categories || []).includes(category)) {
        setCategory('')
      }
    } catch (err) {
      setError(err.message || 'Failed to delete job')
      await refreshAll()
    } finally {
      setDeletingId(null)
    }
  }

  async function handleBulkDelete() {
    const ids = [...selectedJobIds]
    if (!ids.length) return
    const ok = window.confirm(
      `Are you sure you want to delete ${ids.length} selected job${ids.length === 1 ? '' : 's'}?`,
    )
    if (!ok) return

    setBulkDeleting(true)
    setError('')
    const idSet = new Set(ids)
    setJobs((prev) => prev.filter((j) => !idSet.has(jobKey(j))))
    setSelectedJobIds(new Set())
    try {
      const result = await bulkDeleteJobs(ids)
      const catsData = await fetchCategories()
      setCategories(catsData.categories || [])
      if (category && !(catsData.categories || []).includes(category)) {
        setCategory('')
      }
      if (result.not_found?.length) {
        setError(`Deleted ${result.deleted_count}; ${result.not_found.length} id(s) were already gone.`)
      }
    } catch (err) {
      setError(err.message || 'Failed to delete selected jobs')
      await refreshAll()
    } finally {
      setBulkDeleting(false)
    }
  }

  async function handleClearAll() {
    if (jobs.length === 0 && categories.length === 0) return
    const ok = window.confirm('Are you sure you want to delete all extracted jobs?')
    if (!ok) return

    setClearing(true)
    setError('')
    setJobs([])
    setCategories([])
    setCategory('')
    setSelectedJobIds(new Set())
    try {
      await clearAllJobs()
    } catch (err) {
      setError(err.message || 'Failed to clear jobs')
      await refreshAll()
    } finally {
      setClearing(false)
    }
  }

  function openPublishModal(job, { republish = false } = {}) {
    const jobId = jobKey(job)
    if (!jobId) return
    if (republish) {
      setChannelModal({ mode: 'one', job, republish: true })
      return
    }
    if (isPublishedJob(job)) return
    setChannelModal({ mode: 'one', job, republish: false })
  }

  function openPublishAll() {
    const pending = jobs.filter((j) => !j.published_to_telegram)
    if (!pending.length) return
    setChannelModal({ mode: 'all', count: pending.length, republish: false })
  }

  async function confirmPublish({
    targetChannels,
    language = 'English',
    republish = false,
  } = {}) {
    if (!channelModal) return
    const channels = Array.isArray(targetChannels) ? targetChannels : [targetChannels]
    const dest = formatChannelList(channels)
    const isRepublish = Boolean(republish || channelModal.republish)
    setError('')
    setPublishNote('')
    if (channelModal.mode === 'one') {
      const job = channelModal.job
      const jobId = jobKey(job)
      setPublishingId(jobId)
      try {
        const result = await publishJob(jobId, channels, language, {
          republish: isRepublish,
        })
        const publishedAt = new Date().toISOString()
        const nextHistory =
          result.publication_history ||
          [
            ...(Array.isArray(job.publication_history) ? job.publication_history : []),
            ...channels.map((channel) => ({
              channel,
              language,
              published_at: publishedAt,
            })),
          ]
        setJobs((prev) =>
          prev.map((item) =>
            jobKey(item) === jobId
              ? {
                  ...item,
                  published_to_telegram: true,
                  status: 'PUBLISHED',
                  published_url: result.posted_url || item.published_url,
                  published_at: publishedAt,
                  published_channels: result.channels || channels,
                  published_language: result.language || language,
                  publication_history: nextHistory,
                }
              : item,
          ),
        )
        setPublishNote(
          result.message ||
            `${isRepublish ? 'Republished' : 'Published'} job ${jobId} to ${dest}.`,
        )
        setChannelModal(null)
        await refreshAll()
      } catch (err) {
        setError(err.message || `Failed to ${isRepublish ? 'republish' : 'publish'} job`)
      } finally {
        setPublishingId(null)
      }
      return
    }

    setPublishingAll(true)
    try {
      const result = await publishAllPending({ targetChannels: channels, language })
      setPublishNote(result.message || `Publishing to ${dest}…`)
      setChannelModal(null)
      await refreshAll()
    } catch (err) {
      setError(err.message || 'Failed to start publish-all')
    } finally {
      setPublishingAll(false)
    }
  }

  const pendingCount = jobs.filter((j) => !j.published_to_telegram).length
  const busy = loading || clearing || bulkDeleting
  const publishBusy = busy || publishingAll || Boolean(publishingId) || pipelineBusy

  return (
    <section className="panel job-dashboard">
      <header className="panel-header row">
        <div>
          <h2>Job Feed</h2>
          <p>Extracted roles with English summaries and apply links.</p>
        </div>
        <div className="toolbar">
          <label className="field inline">
            <span>Category</span>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              disabled={busy}
            >
              <option value="">All categories</option>
              {categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
          <label className="field inline">
            <span>Sort By</span>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
              disabled={busy}
            >
              {SORT_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn accent"
            onClick={openPublishAll}
            disabled={publishBusy || pendingCount === 0}
          >
            {publishingAll ? (
              <>
                <span className="spinner" aria-hidden />
                Publishing…
              </>
            ) : (
              `Publish All Unposted${pendingCount ? ` (${pendingCount})` : ''}`
            )}
          </button>
          <button type="button" className="btn" onClick={refreshAll} disabled={busy}>
            {loading ? (
              <>
                <span className="spinner" aria-hidden />
                Refreshing…
              </>
            ) : (
              'Refresh Feed'
            )}
          </button>
          <button
            type="button"
            className="btn danger-outline"
            onClick={handleClearAll}
            disabled={busy || (jobs.length === 0 && categories.length === 0)}
          >
            {clearing ? 'Clearing…' : 'Clear All Jobs'}
          </button>
        </div>
      </header>

      <div className="date-filter-bar">
        <span className="date-filter-label">Date</span>
        <div className="preset-pills" role="group" aria-label="Date presets">
          {DATE_PRESETS.map((p) => (
            <button
              key={p.value}
              type="button"
              className={`preset-pill ${datePreset === p.value ? 'active' : ''}`}
              onClick={() => setDatePreset(p.value)}
              disabled={busy}
            >
              {p.label}
            </button>
          ))}
        </div>
        {datePreset === 'custom' && (
          <div className="custom-range">
            <label className="field inline">
              <span>Start</span>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                disabled={busy}
              />
            </label>
            <label className="field inline">
              <span>End</span>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                disabled={busy}
              />
            </label>
          </div>
        )}
      </div>

      {visibleJobs.length > 0 && (
        <div className="selection-toolbar" role="toolbar" aria-label="Bulk job selection">
          <label className="select-all-control">
            <input
              type="checkbox"
              checked={allVisibleSelected}
              ref={(el) => {
                if (el) el.indeterminate = someVisibleSelected
              }}
              onChange={toggleSelectAllVisible}
              disabled={busy || visibleIds.length === 0}
              aria-label="Select all visible jobs"
            />
            <span>Select All</span>
          </label>
          {selectedCount > 0 && (
            <span className="selection-count">{selectedCount} selected</span>
          )}
          <button
            type="button"
            className="btn danger"
            onClick={handleBulkDelete}
            disabled={busy || selectedCount === 0}
          >
            {bulkDeleting ? (
              <>
                <span className="spinner" aria-hidden />
                Deleting…
              </>
            ) : (
              `Delete Selected${selectedCount ? ` (${selectedCount})` : ''}`
            )}
          </button>
        </div>
      )}

      {error && <p className="status err">{error}</p>}
      {publishNote && !error && <p className="status ok">{publishNote}</p>}
      {loading && jobs.length === 0 && <p className="muted">Loading jobs…</p>}

      {!loading && visibleJobs.length === 0 && (
        <p className="muted empty">
          No jobs match the current filters. Try another date range or run extraction.
        </p>
      )}

      <ul className="job-list">
        {visibleJobs.map((job) => {
          const jobId = jobKey(job)
          return (
            <JobCard
              key={jobId}
              job={job}
              selected={selectedJobIds.has(jobId)}
              busy={busy}
              deleting={deletingId === jobId}
              publishing={publishingId === jobId}
              publishBusy={publishBusy}
              onToggleSelected={toggleJobSelected}
              onDelete={handleDelete}
              onOpenPublishModal={openPublishModal}
              onPublish={openPublishModal}
            />
          )
        })}
      </ul>

      <ChannelSelectModal
        isOpen={Boolean(channelModal)}
        republishMode={Boolean(channelModal?.republish)}
        jobId={channelModal?.job ? jobKey(channelModal.job) : undefined}
        title={
          channelModal?.republish
            ? 'Republish to Telegram'
            : channelModal?.mode === 'all'
              ? 'Publish all unposted jobs'
              : 'Publish to Telegram'
        }
        description={
          channelModal?.republish
            ? `Re-send “${channelModal?.job?.title || 'this job'}” to boost visibility or reach new channels.`
            : channelModal?.mode === 'all'
              ? `Send ${channelModal.count} pending job${channelModal.count === 1 ? '' : 's'} to the selected channels.`
              : `Post “${channelModal?.job?.title || 'this job'}” to Telegram.`
        }
        confirmLabel={channelModal?.republish ? 'Republish Now' : 'Publish Now'}
        busy={publishingAll || Boolean(publishingId)}
        onCancel={() => {
          if (!publishingAll && !publishingId) setChannelModal(null)
        }}
        onConfirm={confirmPublish}
      />
    </section>
  )
}
