import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchCategories, fetchJobs } from '../api'

const SORT_OPTIONS = [
  { value: 'date_desc', label: 'Newest First' },
  { value: 'date_asc', label: 'Oldest First' },
  { value: 'category_asc', label: 'Category Name' },
  { value: 'title_asc', label: 'Job Title' },
]

function formatDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
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
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const refreshAll = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [jobsData, catsData] = await Promise.all([
        fetchJobs(category || undefined, sortBy),
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
  }, [category, sortBy])

  useEffect(() => {
    refreshAll()
  }, [refreshAll, refreshToken])

  const visibleJobs = useMemo(() => sortJobsClient(jobs, sortBy), [jobs, sortBy])

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
          <button type="button" className="btn" onClick={refreshAll} disabled={loading}>
            {loading ? (
              <>
                <span className="spinner" aria-hidden />
                Refreshing…
              </>
            ) : (
              'Refresh Feed'
            )}
          </button>
        </div>
      </header>

      {error && <p className="status err">{error}</p>}
      {loading && jobs.length === 0 && <p className="muted">Loading jobs…</p>}

      {!loading && visibleJobs.length === 0 && (
        <p className="muted empty">
          No jobs yet. Run discovery and extraction from the control board.
        </p>
      )}

      <ul className="job-list">
        {visibleJobs.map((job) => (
          <li key={job.id || `${job.channel}-${job.message_id}`} className="job-card">
            <div className="job-top">
              <h3>{job.title || 'Untitled'}</h3>
              {job.category && <span className="badge">{job.category}</span>}
            </div>
            <div className="job-meta">
              <time dateTime={job.date || undefined}>{formatDate(job.date)}</time>
              {job.company && <span>· {job.company}</span>}
              {job.channel && <span>· {job.channel}</span>}
            </div>
            <p className="summary">{job.translated_summary || 'No summary available.'}</p>
            <div className="links">
              {(job.apply_links || []).map((url) => (
                <a
                  key={url}
                  className="btn link"
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Apply
                </a>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
