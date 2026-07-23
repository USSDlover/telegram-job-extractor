import { useCallback, useEffect, useMemo, useState } from 'react'
import { clearAllJobs, deleteJob, fetchCategories, fetchJobs } from '../api'

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

function formatDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

function isTelegramUrl(url) {
  try {
    const host = new URL(url).hostname.toLowerCase()
    return host === 't.me' || host.endsWith('.t.me') || host === 'telegram.me'
  } catch {
    return /t\.me\//i.test(url || '')
  }
}

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
      setJobs(jobsData.jobs || [])
      setCategories(catsData.categories || [])
      const nextCats = catsData.categories || []
      if (category && !nextCats.includes(category)) {
        setCategory('')
      }
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

  async function handleDelete(job) {
    const jobId = job.id || String(job.message_id || '')
    if (!jobId) return
    const ok = window.confirm(`Delete job “${job.title || jobId}”?`)
    if (!ok) return

    setDeletingId(jobId)
    setJobs((prev) => prev.filter((j) => (j.id || String(j.message_id)) !== jobId))
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

  async function handleClearAll() {
    if (jobs.length === 0 && categories.length === 0) return
    const ok = window.confirm('Are you sure you want to delete all extracted jobs?')
    if (!ok) return

    setClearing(true)
    setError('')
    setJobs([])
    setCategories([])
    setCategory('')
    try {
      await clearAllJobs()
    } catch (err) {
      setError(err.message || 'Failed to clear jobs')
      await refreshAll()
    } finally {
      setClearing(false)
    }
  }

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
              disabled={loading}
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
              disabled={loading}
            >
              {SORT_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="btn" onClick={refreshAll} disabled={loading || clearing}>
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
            disabled={loading || clearing || (jobs.length === 0 && categories.length === 0)}
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
              disabled={loading}
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
                disabled={loading}
              />
            </label>
            <label className="field inline">
              <span>End</span>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                disabled={loading}
              />
            </label>
          </div>
        )}
      </div>

      {error && <p className="status err">{error}</p>}
      {loading && jobs.length === 0 && <p className="muted">Loading jobs…</p>}

      {!loading && visibleJobs.length === 0 && (
        <p className="muted empty">
          No jobs match the current filters. Try another date range or run extraction.
        </p>
      )}

      <ul className="job-list">
        {visibleJobs.map((job) => {
          const jobId = job.id || String(job.message_id || '')
          return (
            <li key={jobId} className="job-card">
              <div className="job-top">
                <h3>{job.title || 'Untitled'}</h3>
                {job.category && <span className="badge">{job.category}</span>}
                <button
                  type="button"
                  className="btn tiny danger-outline job-delete"
                  onClick={() => handleDelete(job)}
                  disabled={deletingId === jobId}
                  aria-label={`Delete ${job.title || jobId}`}
                >
                  {deletingId === jobId ? '…' : 'Delete'}
                </button>
              </div>
              <div className="job-meta">
                <time dateTime={job.date || undefined}>{formatDate(job.date)}</time>
                {job.company && <span>· {job.company}</span>}
                {job.channel && <span>· {job.channel}</span>}
              </div>
              <p className="summary">{job.translated_summary || 'No summary available.'}</p>
              <div className="links">
                {(job.apply_links || []).map((url) => {
                  const tg = isTelegramUrl(url)
                  return (
                    <a
                      key={url}
                      className={`btn link ${tg ? 'tg' : ''}`}
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {tg ? 'View Telegram Post' : 'Apply Here'}
                    </a>
                  )
                })}
              </div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
