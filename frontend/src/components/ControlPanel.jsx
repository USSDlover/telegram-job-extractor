import { useEffect, useMemo, useState } from 'react'
import { discoverChannels, startExtraction, stopExtraction } from '../api'
import { useActivity } from '../activity'

const EXTRACT_DATE_PRESETS = [
  { value: 'today', label: 'Today' },
  { value: 'this_week', label: 'This Week' },
  { value: 'this_month', label: 'This Month' },
  { value: 'all_time', label: 'All Time' },
  { value: 'custom', label: 'Custom' },
]

function toggleValue(list, value) {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
}

function normalizeLabel(value) {
  return String(value || '')
    .trim()
    .replace(/^[\s\-–—|:;,.]+|[\s\-–—|:;,.]+$/g, '')
    .replace(/\s+/g, ' ')
}

function dedupeLabels(values) {
  const out = []
  const seen = new Set()
  for (const raw of values || []) {
    let label = normalizeLabel(raw)
    if (!label) continue
    const key = label.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    if (label === label.toLowerCase() && label.includes(' ')) {
      label = label.replace(/\b\w/g, (c) => c.toUpperCase())
    }
    out.push(label)
  }
  return out
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
  const [categoryQuery, setCategoryQuery] = useState('')
  const [titleQuery, setTitleQuery] = useState('')
  const [extractPreset, setExtractPreset] = useState('today')
  const [extractStartDate, setExtractStartDate] = useState('')
  const [extractEndDate, setExtractEndDate] = useState('')
  const [sampleCount, setSampleCount] = useState(null)
  const [busy, setBusy] = useState(false)
  const [busyHint, setBusyHint] = useState('')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [stopping, setStopping] = useState(false)
  const [aborted, setAborted] = useState(false)
  const [isExtracting, setIsExtracting] = useState(false)

  useEffect(() => {
    const event = latestByStage.DISCOVERED_CATEGORIES
    if (!event?.data) return
    const cats = dedupeLabels(event.data.discovered_categories || [])
    const suggested = dedupeLabels(event.data.suggested_titles || [])
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

  useEffect(() => {
    const stage = latestByStage.EXTRACTION_STOPPED?.ts
    if (stage) {
      setStopping(false)
      setIsExtracting(false)
      setAborted(true)
      setStatus('Extraction pipeline stopped.')
    }
  }, [latestByStage.EXTRACTION_STOPPED])

  useEffect(() => {
    const done = latestByStage.EXTRACTION_DONE
    if (done?.data?.stopped) return
    if (done?.ts && isExtracting) {
      setIsExtracting(false)
      setStopping(false)
      if (!aborted) {
        setStatus((prev) => prev || 'Extraction finished.')
      }
    }
  }, [latestByStage.EXTRACTION_DONE, isExtracting, aborted])

  async function handleStopExtraction() {
    setStopping(true)
    setError('')
    try {
      const data = await stopExtraction()
      setStatus(data.message || 'Stop signal sent — waiting for scraper to halt…')
      setAborted(true)
    } catch (err) {
      setError(err.message || 'Failed to stop extraction')
      setStopping(false)
    }
  }
  const filteredCategories = useMemo(() => {
    const q = categoryQuery.trim().toLowerCase()
    if (!q) return categories
    return categories.filter((c) => c.toLowerCase().includes(q))
  }, [categories, categoryQuery])

  const filteredTitles = useMemo(() => {
    const q = titleQuery.trim().toLowerCase()
    if (!q) return titles
    return titles.filter((t) => t.toLowerCase().includes(q))
  }, [titles, titleQuery])

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
      const cats = dedupeLabels(data.discovered_categories || [])
      const suggested = dedupeLabels(data.suggested_titles || [])
      setCategories(cats)
      setTitles(suggested)
      setSelectedCategories([])
      setSelectedTitles([])
      setCategoryQuery('')
      setTitleQuery('')
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
    if (extractPreset === 'custom' && !extractStartDate && !extractEndDate) {
      setError('Choose a custom start and/or end date before extracting.')
      return
    }
    setBusy(true)
    setBusyHint('Starting multi-channel extraction…')
    setAborted(false)
    setStopping(false)
    setIsExtracting(true)
    try {
      const data = await startExtraction(channels, selectedCategories, selectedTitles, {
        datePreset: extractPreset,
        startDate: extractPreset === 'custom' ? extractStartDate || undefined : undefined,
        endDate: extractPreset === 'custom' ? extractEndDate || undefined : undefined,
      })
      setStatus(data.message || 'Extraction started')
      onExtractionStarted?.()
    } catch (err) {
      setError(err.message || 'Extraction failed to start')
      setIsExtracting(false)
    } finally {
      setBusy(false)
      setBusyHint('')
    }
  }

  const hasDiscovery = categories.length > 0 || titles.length > 0
  const locked = busy || (pipelineBusy && !isExtracting)
  const startDisabled = busy || isExtracting || channels.length === 0
  const progress = latestByStage.EXTRACTION_PROGRESS?.data
  const channelProgress = latestByStage.EXTRACTION_STARTED?.data
  const progressLabel = stopping
    ? 'Stopping extraction…'
    : progress?.current && progress?.total
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

      {(busy || isExtracting || pipelineBusy) && (
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
            <fieldset className="option-box">
              <legend>Categories ({categories.length})</legend>
              <div className="option-toolbar">
                <button
                  type="button"
                  className="btn tiny"
                  disabled={locked}
                  onClick={() => setSelectedCategories([...categories])}
                >
                  Select All
                </button>
                <button
                  type="button"
                  className="btn tiny"
                  disabled={locked}
                  onClick={() => setSelectedCategories([])}
                >
                  Deselect All
                </button>
                <span className="muted tiny-count">
                  {selectedCategories.length} selected
                </span>
              </div>
              {categories.length > 15 && (
                <input
                  className="option-search"
                  type="search"
                  placeholder="Filter categories…"
                  value={categoryQuery}
                  onChange={(e) => setCategoryQuery(e.target.value)}
                  disabled={locked}
                />
              )}
              <div className="check-scroll">
                <div className="check-grid">
                  {filteredCategories.map((cat) => (
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
                  {filteredCategories.length === 0 && (
                    <p className="muted">No categories match “{categoryQuery}”.</p>
                  )}
                </div>
              </div>
            </fieldset>
          )}

          {titles.length > 0 && (
            <fieldset className="option-box">
              <legend>Suggested titles ({titles.length})</legend>
              <div className="option-toolbar">
                <button
                  type="button"
                  className="btn tiny"
                  disabled={locked}
                  onClick={() => setSelectedTitles([...titles])}
                >
                  Select All
                </button>
                <button
                  type="button"
                  className="btn tiny"
                  disabled={locked}
                  onClick={() => setSelectedTitles([])}
                >
                  Deselect All
                </button>
              </div>
              {titles.length > 15 && (
                <input
                  className="option-search"
                  type="search"
                  placeholder="Filter titles…"
                  value={titleQuery}
                  onChange={(e) => setTitleQuery(e.target.value)}
                  disabled={locked}
                />
              )}
              <div className="check-scroll">
                <div className="check-grid">
                  {filteredTitles.map((title) => (
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
              </div>
            </fieldset>
          )}

          <div className="extract-date-block">
            <span className="date-filter-label">Target scrape range</span>
            <div className="preset-pills" role="group" aria-label="Extraction date range">
              {EXTRACT_DATE_PRESETS.map((p) => (
                <button
                  key={p.value}
                  type="button"
                  className={`preset-pill ${extractPreset === p.value ? 'active' : ''}`}
                  onClick={() => setExtractPreset(p.value)}
                  disabled={locked}
                >
                  {p.label}
                </button>
              ))}
            </div>
            {extractPreset === 'custom' && (
              <div className="custom-range">
                <label className="field inline">
                  <span>Start</span>
                  <input
                    type="date"
                    value={extractStartDate}
                    onChange={(e) => setExtractStartDate(e.target.value)}
                    disabled={locked}
                  />
                </label>
                <label className="field inline">
                  <span>End</span>
                  <input
                    type="date"
                    value={extractEndDate}
                    onChange={(e) => setExtractEndDate(e.target.value)}
                    disabled={locked}
                  />
                </label>
              </div>
            )}
          </div>

          <div className="actions">
            {!isExtracting ? (
              <button
                type="button"
                className="btn accent"
                onClick={handleExtract}
                disabled={startDisabled}
              >
                Start Extraction Pipeline
              </button>
            ) : (
              <button
                type="button"
                className="btn danger"
                onClick={handleStopExtraction}
                disabled={stopping}
              >
                {stopping ? (
                  <>
                    <span className="spinner" aria-hidden />
                    Stopping…
                  </>
                ) : (
                  'Stop Extraction'
                )}
              </button>
            )}
          </div>
        </div>
      )}

      {aborted && <p className="status warn">Extraction was manually aborted.</p>}
      {status && <p className="status ok">{status}</p>}
      {error && <p className="status err">{error}</p>}
    </section>
  )
}
