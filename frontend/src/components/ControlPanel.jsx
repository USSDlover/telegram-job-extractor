import { useEffect, useState } from 'react'
import { discoverChannel, startExtraction } from '../api'
import { useActivity } from '../activity'
import ActivityConsole from './ActivityConsole'

function toggleValue(list, value) {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
}

export default function ControlPanel({ onExtractionStarted }) {
  const { liveStatus, pipelineBusy, latestByStage } = useActivity()
  const [channel, setChannel] = useState('')
  const [categories, setCategories] = useState([])
  const [titles, setTitles] = useState([])
  const [selectedCategories, setSelectedCategories] = useState([])
  const [selectedTitles, setSelectedTitles] = useState([])
  const [sampleCount, setSampleCount] = useState(null)
  const [busy, setBusy] = useState(false)
  const [busyHint, setBusyHint] = useState('')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  // Apply discovery results as soon as SSE delivers them (before HTTP returns)
  useEffect(() => {
    const event = latestByStage.DISCOVERED_CATEGORIES
    if (!event?.data) return
    const cats = event.data.discovered_categories || []
    const suggested = event.data.suggested_titles || []
    if (cats.length || suggested.length) {
      setCategories(cats)
      setTitles(suggested)
      setSampleCount(event.data.sample_count ?? null)
    }
  }, [latestByStage.DISCOVERED_CATEGORIES])

  useEffect(() => {
    if (busy || pipelineBusy) {
      setBusyHint(liveStatus || 'Working…')
    }
  }, [busy, pipelineBusy, liveStatus])

  async function handleDiscover() {
    setError('')
    setStatus('')
    setBusy(true)
    setBusyHint('Joining channel and sampling posts…')
    try {
      const data = await discoverChannel(channel.trim())
      setCategories(data.discovered_categories || [])
      setTitles(data.suggested_titles || [])
      setSelectedCategories([])
      setSelectedTitles([])
      setSampleCount(data.sample_count ?? null)
      setStatus(
        `Sampled ${data.sample_count ?? 0} posts from ${data.channel}. Select filters, then start extraction.`,
      )
    } catch (err) {
      setError(err.message || 'Discovery failed')
    } finally {
      setBusy(false)
      setBusyHint('')
    }
  }

  async function handleExtract() {
    setError('')
    setStatus('')
    setBusy(true)
    setBusyHint('Starting extraction pipeline…')
    try {
      const data = await startExtraction(
        channel.trim(),
        selectedCategories,
        selectedTitles,
      )
      setStatus(data.message || 'Extraction started')
      onExtractionStarted?.()
    } catch (err) {
      setError(err.message || 'Extraction failed to start')
    } finally {
      setBusy(false)
      setBusyHint('')
    }
  }

  const hasDiscovery = categories.length > 0 || titles.length > 0
  const locked = busy || pipelineBusy
  const progress = latestByStage.EXTRACTION_PROGRESS?.data
  const progressLabel =
    progress?.current && progress?.total
      ? `Extracting post ${progress.current}/${progress.total}…`
      : busyHint || liveStatus

  return (
    <div className="control-stack">
      <section className="panel control-panel">
        <header className="panel-header">
          <h2>Control Board</h2>
          <p>Join a channel, sample posts, pick categories, then run extraction.</p>
        </header>

        <label className="field">
          <span>Channel</span>
          <input
            type="text"
            placeholder="@tech_jobs_channel"
            value={channel}
            onChange={(e) => setChannel(e.target.value)}
            disabled={locked}
          />
        </label>

        <div className="actions">
          <button
            type="button"
            className="btn primary"
            onClick={handleDiscover}
            disabled={locked || !channel.trim()}
          >
            {busy ? (
              <>
                <span className="spinner" aria-hidden />
                Sampling…
              </>
            ) : (
              'Join & Sample Channel'
            )}
          </button>
        </div>

        {locked && (
          <p className="status busy-line">
            <span className="spinner" aria-hidden />
            {progressLabel || 'Working…'}
          </p>
        )}

        {hasDiscovery && (
          <div className="discovery">
            {sampleCount != null && (
              <p className="muted">Samples analyzed: {sampleCount}</p>
            )}

            {categories.length > 0 && (
              <fieldset>
                <legend>Categories</legend>
                <div className="check-grid">
                  {categories.map((cat) => (
                    <label key={cat} className="check">
                      <input
                        type="checkbox"
                        checked={selectedCategories.includes(cat)}
                        onChange={() =>
                          setSelectedCategories((prev) => toggleValue(prev, cat))
                        }
                        disabled={locked}
                      />
                      <span>{cat}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
            )}

            {titles.length > 0 && (
              <fieldset>
                <legend>Suggested titles</legend>
                <div className="check-grid">
                  {titles.map((title) => (
                    <label key={title} className="check">
                      <input
                        type="checkbox"
                        checked={selectedTitles.includes(title)}
                        onChange={() =>
                          setSelectedTitles((prev) => toggleValue(prev, title))
                        }
                        disabled={locked}
                      />
                      <span>{title}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
            )}

            <div className="actions">
              <button
                type="button"
                className="btn accent"
                onClick={handleExtract}
                disabled={locked || !channel.trim()}
              >
                {pipelineBusy && latestByStage.EXTRACTION_STARTED ? (
                  <>
                    <span className="spinner" aria-hidden />
                    Extracting…
                  </>
                ) : (
                  'Start Extraction Pipeline'
                )}
              </button>
            </div>
          </div>
        )}

        {status && <p className="status ok">{status}</p>}
        {error && <p className="status err">{error}</p>}
      </section>

      <ActivityConsole />
    </div>
  )
}
