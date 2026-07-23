import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  discoverChannels,
  fetchCategories,
  startExtraction,
  stopExtraction,
} from '../api'
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

function mergeLabels(...groups) {
  return dedupeLabels(groups.flat())
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
  const [storedCategories, setStoredCategories] = useState([])
  const [discoveredCategories, setDiscoveredCategories] = useState([])
  const [suggestedTitles, setSuggestedTitles] = useState([])
  const [selectedCategories, setSelectedCategories] = useState([])
  const [selectedTitles, setSelectedTitles] = useState([])
  const [titleInput, setTitleInput] = useState('')
  const [categoryQuery, setCategoryQuery] = useState('')
  const [titleQuery, setTitleQuery] = useState('')
  const [extractPreset, setExtractPreset] = useState('today')
  const [extractStartDate, setExtractStartDate] = useState('')
  const [extractEndDate, setExtractEndDate] = useState('')
  const [sampleCount, setSampleCount] = useState(null)
  const [discoveryDone, setDiscoveryDone] = useState(false)
  const [discovering, setDiscovering] = useState(false)
  const [busy, setBusy] = useState(false)
  const [busyHint, setBusyHint] = useState('')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [stopping, setStopping] = useState(false)
  const [aborted, setAborted] = useState(false)
  const [isExtracting, setIsExtracting] = useState(false)

  const loadStoredCategories = useCallback(async () => {
    try {
      const data = await fetchCategories()
      setStoredCategories(data.categories || [])
    } catch {
      /* non-fatal */
    }
  }, [])

  useEffect(() => {
    loadStoredCategories()
  }, [loadStoredCategories])

  const categoryOptions = useMemo(
    () => mergeLabels(discoveredCategories, storedCategories),
    [discoveredCategories, storedCategories],
  )

  const filteredCategories = useMemo(() => {
    const q = categoryQuery.trim().toLowerCase()
    if (!q) return categoryOptions
    return categoryOptions.filter((c) => c.toLowerCase().includes(q))
  }, [categoryOptions, categoryQuery])

  const filteredTitles = useMemo(() => {
    const q = titleQuery.trim().toLowerCase()
    if (!q) return suggestedTitles
    return suggestedTitles.filter((t) => t.toLowerCase().includes(q))
  }, [suggestedTitles, titleQuery])

  useEffect(() => {
    if (busy || discovering || pipelineBusy || isExtracting) {
      setBusyHint(liveStatus || 'Working…')
    }
  }, [busy, discovering, pipelineBusy, isExtracting, liveStatus])

  // Apply discovery results as soon as SSE delivers them
  useEffect(() => {
    const event = latestByStage.DISCOVERED_CATEGORIES
    if (!event?.data) return
    const cats = dedupeLabels(event.data.discovered_categories || [])
    const titles = dedupeLabels(event.data.suggested_titles || [])
    if (cats.length || titles.length) {
      setDiscoveredCategories(cats)
      setSuggestedTitles(titles)
      setSampleCount(event.data.sample_count ?? null)
      setDiscoveryDone(true)
    }
  }, [latestByStage.DISCOVERED_CATEGORIES])

  useEffect(() => {
    if (latestByStage.EXTRACTION_STOPPED?.ts) {
      setStopping(false)
      setIsExtracting(false)
      setAborted(true)
      setStatus('Extraction pipeline stopped.')
      loadStoredCategories()
    }
  }, [latestByStage.EXTRACTION_STOPPED, loadStoredCategories])

  useEffect(() => {
    const done = latestByStage.EXTRACTION_DONE
    if (done?.data?.stopped) return
    if (done?.ts && isExtracting) {
      setIsExtracting(false)
      setStopping(false)
      if (!aborted) setStatus((prev) => prev || 'Extraction finished.')
      loadStoredCategories()
    }
  }, [latestByStage.EXTRACTION_DONE, isExtracting, aborted, loadStoredCategories])

  useEffect(() => {
    if (latestByStage.JOB_SAVED?.ts) loadStoredCategories()
  }, [latestByStage.JOB_SAVED, loadStoredCategories])

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

  function addTitleChip() {
    const parts = titleInput
      .split(',')
      .map((t) => normalizeLabel(t))
      .filter(Boolean)
    if (!parts.length) return
    setSelectedTitles((prev) => mergeLabels(prev, parts))
    setSuggestedTitles((prev) => mergeLabels(prev, parts))
    setTitleInput('')
  }

  function removeTitle(title) {
    setSelectedTitles((prev) => prev.filter((t) => t !== title))
  }

  async function handleDiscover() {
    setError('')
    setStatus('')
    setDiscovering(true)
    setBusy(true)
    setBusyHint('Joining channels and sampling posts…')
    setDiscoveryDone(false)
    try {
      const data = await discoverChannels(channels)
      const cats = dedupeLabels(data.discovered_categories || [])
      const titles = dedupeLabels(data.suggested_titles || [])
      setDiscoveredCategories(cats)
      setSuggestedTitles(titles)
      setSelectedCategories([])
      setSelectedTitles([])
      setCategoryQuery('')
      setTitleQuery('')
      setSampleCount(data.sample_count ?? null)
      setDiscoveryDone(true)
      setStatus(
        `Discovered ${cats.length} categories and ${titles.length} titles from ${data.sample_count ?? 0} sample posts.`,
      )
      await loadStoredCategories()
    } catch (err) {
      setError(err.message || 'Category discovery failed')
      setDiscoveryDone(false)
    } finally {
      setDiscovering(false)
      setBusy(false)
      setBusyHint('')
    }
  }

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

  const locked = busy || discovering || (pipelineBusy && !isExtracting)
  const startDisabled = busy || discovering || isExtracting || channels.length === 0
  const progress = latestByStage.EXTRACTION_PROGRESS?.data
  const channelProgress = latestByStage.EXTRACTION_STARTED?.data
  const progressLabel = stopping
    ? 'Stopping extraction…'
    : discovering
      ? busyHint || liveStatus || 'Discovering categories…'
      : progress?.current && progress?.total
        ? `${progress.channel ? `[${progress.channel}] ` : ''}Extracting post ${progress.current}/${progress.total}…`
        : channelProgress?.index && channelProgress?.total
          ? `Processing channel ${channelProgress.index}/${channelProgress.total}: ${channelProgress.channel}…`
          : busyHint || liveStatus

  return (
    <section className="panel control-panel">
      <header className="panel-header">
        <h2>Control Board</h2>
        <p>Discover categories from channels, select targets, then extract by date range.</p>
      </header>

      <div className="workflow-step">
        <h3 className="step-title">1. Channels & discovery</h3>
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
              disabled={locked || isExtracting}
            />
            <button
              type="button"
              className="btn"
              onClick={addChannels}
              disabled={locked || isExtracting || !channelInput.trim()}
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
                  disabled={locked || isExtracting}
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
            disabled={locked || isExtracting || channels.length === 0}
          >
            {discovering ? (
              <>
                <span className="spinner" aria-hidden />
                Discovering…
              </>
            ) : (
              'Join & Discover Categories'
            )}
          </button>
        </div>
      </div>

      {(busy || discovering || isExtracting || pipelineBusy) && (
        <p className="status busy-line">
          <span className="spinner" aria-hidden />
          {progressLabel || 'Working…'}
        </p>
      )}

      {discoveryDone && (
        <div className="workflow-step">
          <h3 className="step-title">2. Categories & titles</h3>
          {sampleCount != null && (
            <p className="muted">Samples analyzed: {sampleCount}</p>
          )}

          <fieldset className="option-box">
            <legend>Categories ({categoryOptions.length})</legend>
            <p className="muted option-hint">
              Discovered categories merged with stored ones. Leave unchecked to match all.
            </p>
            <div className="option-toolbar">
              <button
                type="button"
                className="btn tiny"
                disabled={locked || isExtracting || categoryOptions.length === 0}
                onClick={() => setSelectedCategories([...categoryOptions])}
              >
                Select All
              </button>
              <button
                type="button"
                className="btn tiny"
                disabled={locked || isExtracting}
                onClick={() => setSelectedCategories([])}
              >
                Deselect All
              </button>
              <span className="muted tiny-count">{selectedCategories.length} selected</span>
            </div>
            {categoryOptions.length > 15 && (
              <input
                className="option-search"
                type="search"
                placeholder="Filter categories…"
                value={categoryQuery}
                onChange={(e) => setCategoryQuery(e.target.value)}
                disabled={locked || isExtracting}
              />
            )}
            <div className="check-scroll">
              {categoryOptions.length === 0 ? (
                <p className="muted">No categories discovered. Try other channels.</p>
              ) : (
                <div className="check-grid">
                  {filteredCategories.map((cat) => (
                    <label key={cat} className="check">
                      <input
                        type="checkbox"
                        checked={selectedCategories.includes(cat)}
                        onChange={() =>
                          setSelectedCategories((prev) => toggleValue(prev, cat))
                        }
                        disabled={locked || isExtracting}
                      />
                      <span>{cat}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>
          </fieldset>

          <fieldset className="option-box">
            <legend>Suggested titles ({suggestedTitles.length})</legend>
            <p className="muted option-hint">
              Check discovered titles or add your own chips.
            </p>
            <div className="option-toolbar">
              <button
                type="button"
                className="btn tiny"
                disabled={locked || isExtracting || suggestedTitles.length === 0}
                onClick={() => setSelectedTitles([...suggestedTitles])}
              >
                Select All
              </button>
              <button
                type="button"
                className="btn tiny"
                disabled={locked || isExtracting}
                onClick={() => setSelectedTitles([])}
              >
                Deselect All
              </button>
            </div>
            {suggestedTitles.length > 15 && (
              <input
                className="option-search"
                type="search"
                placeholder="Filter titles…"
                value={titleQuery}
                onChange={(e) => setTitleQuery(e.target.value)}
                disabled={locked || isExtracting}
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
                      disabled={locked || isExtracting}
                    />
                    <span>{title}</span>
                  </label>
                ))}
              </div>
            </div>
            <div className="channel-add-row" style={{ marginTop: '0.65rem' }}>
              <input
                type="text"
                placeholder="Add custom title…"
                value={titleInput}
                onChange={(e) => setTitleInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    addTitleChip()
                  }
                }}
                disabled={locked || isExtracting}
              />
              <button
                type="button"
                className="btn"
                onClick={addTitleChip}
                disabled={locked || isExtracting || !titleInput.trim()}
              >
                Add Title
              </button>
            </div>
            {selectedTitles.length > 0 && (
              <div className="channel-chips">
                {selectedTitles.map((title) => (
                  <span key={title} className="channel-chip">
                    {title}
                    <button
                      type="button"
                      className="chip-remove"
                      aria-label={`Remove ${title}`}
                      onClick={() => removeTitle(title)}
                      disabled={locked || isExtracting}
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
          </fieldset>
        </div>
      )}

      <div className="workflow-step">
        <h3 className="step-title">3. Date-bounded extraction</h3>
        <div className="extract-date-block">
          <span className="date-filter-label">Target scrape range</span>
          <div className="preset-pills" role="group" aria-label="Extraction date range">
            {EXTRACT_DATE_PRESETS.map((p) => (
              <button
                key={p.value}
                type="button"
                className={`preset-pill ${extractPreset === p.value ? 'active' : ''}`}
                onClick={() => setExtractPreset(p.value)}
                disabled={locked || isExtracting}
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
                  disabled={locked || isExtracting}
                />
              </label>
              <label className="field inline">
                <span>End</span>
                <input
                  type="date"
                  value={extractEndDate}
                  onChange={(e) => setExtractEndDate(e.target.value)}
                  disabled={locked || isExtracting}
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

      {aborted && <p className="status warn">Extraction was manually aborted.</p>}
      {status && <p className="status ok">{status}</p>}
      {error && <p className="status err">{error}</p>}
    </section>
  )
}
