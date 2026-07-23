import { useEffect, useState } from 'react'
import { discoverChannels, startExtraction } from '../api'
import { useActivity } from '../activity'

function toggleValue(list, value) {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
}

function normalizeChannelInput(raw) {
  return raw
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => {
      let name = s
      if (name.startsWith('https://t.me/')) {
        name = name.replace(/\/$/, '').split('/').pop()
      }
      if (!name.startsWith('@')) name = `@${name}`
      return name
    })
}

export default function ControlPanel({ onExtractionStarted }) {
  const { liveStatus, pipelineBusy, latestByStage } = useActivity()
  const [channelInput, setChannelInput] = useState('')
  const [channels, setChannels] = useState([])
  const [categories, setCategories] = useState([])
  const [titles, setTitles] = useState([])
  const [selectedCategories, setSelectedCategories] = useState([])
  const [selectedTitles, setSelectedTitles] = useState([])
  const [sampleCount, setSampleCount] = useState(null)
  const [busy, setBusy] = useState(false)
  const [busyHint, setBusyHint] = useState('')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

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

  function addChannels() {
    const next = normalizeChannelInput(channelInput)
    if (!next.length) return
    setChannels((prev) => {
      const seen = new Set(prev.map((c) => c.toLowerCase()))
      const merged = [...prev]
      next.forEach((c) => {
        if (!seen.has(c.toLowerCase())) {
          seen.add(c.toLowerCase())
          merged.push(c)
        }
      })
      return merged
    })
    setChannelInput('')
  }

  function removeChannel(name) {
    setChannels((prev) => prev.filter((c) => c !== name))
  }

  async function handleDiscover() {
    setError('')
    setStatus('')
    setBusy(true)
    setBusyHint('Joining channels and sampling posts…')
    try {
      const data = await discoverChannels(channels)
      setCategories(data.discovered_categories || [])
      setTitles(data.suggested_titles || [])
      setSelectedCategories([])
      setSelectedTitles([])
      setSampleCount(data.sample_count ?? null)
      const count = (data.channels || channels).length
      setStatus(
        `Sampled ${data.sample_count ?? 0} posts across ${count} channel(s). Select filters, then start extraction.`,
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
    setBusyHint('Starting multi-channel extraction…')
    try {
      const data = await startExtraction(channels, selectedCategories, selectedTitles)
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
  const channelProgress = latestByStage.EXTRACTION_STARTED?.data
  const progressLabel =
    progress?.current && progress?.total
      ? `${progress.channel ? `[${progress.channel}] ` : ''}Extracting post ${progress.current}/${progress.total}…`
      : channelProgress?.index && channelProgress?.total
        ? `Processing channel ${channelProgress.index}/${channelProgress.total}: ${channelProgress.channel}…`
        : busyHint || liveStatus

  return (
    <section className="panel control-panel">
      <header className="panel-header">
        <h2>Control Board</h2>
        <p>Add channels, sample posts, pick categories, then run extraction.</p>
      </header>

      <label className="field">
        <span>Add channel</span>
        <div className="channel-add-row">
          <input
            type="text"
            placeholder="@tech_jobs, @remote_work"
            value={channelInput}
            onChange={(e) => setChannelInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                addChannels()
              }
            }}
            disabled={locked}
          />
          <button
            type="button"
            className="btn"
            onClick={addChannels}
            disabled={locked || !channelInput.trim()}
          >
            Add Channel
          </button>
        </div>
      </label>

      {channels.length > 0 ? (
        <div className="channel-chips">
          {channels.map((ch) => (
            <span key={ch} className="channel-chip">
              {ch}
              <button
                type="button"
                className="chip-remove"
                aria-label={`Remove ${ch}`}
                onClick={() => removeChannel(ch)}
                disabled={locked}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : (
        <p className="muted">No channels yet — add one or more usernames above.</p>
      )}

      <div className="actions">
        <button
          type="button"
          className="btn primary"
          onClick={handleDiscover}
          disabled={locked || channels.length === 0}
        >
          {busy ? (
            <>
              <span className="spinner" aria-hidden />
              Sampling…
            </>
          ) : (
            'Join & Sample Channels'
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
              disabled={locked || channels.length === 0}
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
  )
}
