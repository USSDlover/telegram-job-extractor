import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  addScraperChannel,
  deleteScraperChannel,
  discoverChannels,
  fetchCategories,
  fetchScraperChannels,
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
  const [channelNameInput, setChannelNameInput] = useState('')
  const [scraperChannels, setScraperChannels] = useState([])
  const [selectedChannelIds, setSelectedChannelIds] = useState([])
  const [channelsLoading, setChannelsLoading] = useState(true)
  const [addingChannel, setAddingChannel] = useState(false)
  const [removingChannelId, setRemovingChannelId] = useState('')
  const [storedCategories, setStoredCategories] = useState([])
  const [discoveredCategories, setDiscoveredCategories] = useState([])
  const [selectedCategories, setSelectedCategories] = useState([])
  const [categoryQuery, setCategoryQuery] = useState('')
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

  const loadScraperChannels = useCallback(async () => {
    setChannelsLoading(true)
    try {
      const data = await fetchScraperChannels()
      const list = data.channels || []
      setScraperChannels(list)
      setSelectedChannelIds((prev) => {
        const valid = new Set(list.map((item) => item.id))
        const kept = prev.filter((id) => valid.has(id))
        if (kept.length) return kept
        return list.map((item) => item.id)
      })
    } catch (err) {
      setError(err.message || 'Failed to load source channels')
    } finally {
      setChannelsLoading(false)
    }
  }, [])

  useEffect(() => {
    loadStoredCategories()
    loadScraperChannels()
  }, [loadStoredCategories, loadScraperChannels])

  const categoryOptions = useMemo(
    () => mergeLabels(discoveredCategories, storedCategories),
    [discoveredCategories, storedCategories],
  )

  const filteredCategories = useMemo(() => {
    const q = categoryQuery.trim().toLowerCase()
    if (!q) return categoryOptions
    return categoryOptions.filter((c) => c.toLowerCase().includes(q))
  }, [categoryOptions, categoryQuery])

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
    if (cats.length) {
      setDiscoveredCategories(cats)
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

  const selectedHandles = useMemo(
    () =>
      scraperChannels
        .filter((channel) => selectedChannelIds.includes(channel.id))
        .map((channel) => channel.handle)
        .filter(Boolean),
    [scraperChannels, selectedChannelIds],
  )

  function toggleChannelSelected(channelId) {
    setSelectedChannelIds((prev) => toggleValue(prev, channelId))
  }

  function selectAllChannels() {
    setSelectedChannelIds(scraperChannels.map((channel) => channel.id))
  }

  function deselectAllChannels() {
    setSelectedChannelIds([])
  }

  async function addChannels() {
    const next = normalizeChannelInput(channelInput)
    if (!next.length) return
    const sharedName = channelNameInput.trim()
    setAddingChannel(true)
    setError('')
    try {
      let latest = scraperChannels
      const addedIds = []
      for (const handle of next) {
        const result = await addScraperChannel({
          handle,
          name: next.length === 1 ? sharedName || undefined : undefined,
        })
        latest = result.channels || latest
        if (result.channel?.id) addedIds.push(result.channel.id)
      }
      setScraperChannels(latest)
      setSelectedChannelIds((prev) => {
        const merged = new Set(prev)
        addedIds.forEach((id) => merged.add(id))
        if (!merged.size) latest.forEach((item) => merged.add(item.id))
        return [...merged]
      })
      setChannelInput('')
      setChannelNameInput('')
    } catch (err) {
      setError(err.message || 'Failed to save source channel')
    } finally {
      setAddingChannel(false)
    }
  }

  async function removeChannel(channel) {
    if (!channel?.id) return
    const ok = window.confirm(`Remove source channel “${channel.name || channel.handle}”?`)
    if (!ok) return
    setRemovingChannelId(channel.id)
    setError('')
    try {
      await deleteScraperChannel(channel.id)
      setScraperChannels((prev) => prev.filter((item) => item.id !== channel.id))
      setSelectedChannelIds((prev) => prev.filter((id) => id !== channel.id))
    } catch (err) {
      setError(err.message || 'Failed to remove source channel')
    } finally {
      setRemovingChannelId('')
    }
  }

  async function handleDiscover() {
    setError('')
    setStatus('')
    if (!selectedHandles.length) {
      setError('Select at least one saved source channel.')
      return
    }
    setDiscovering(true)
    setBusy(true)
    setBusyHint('Joining channels and sampling posts…')
    setDiscoveryDone(false)
    try {
      const data = await discoverChannels(selectedHandles)
      const cats = dedupeLabels(data.discovered_categories || [])
      setDiscoveredCategories(cats)
      setSelectedCategories([])
      setCategoryQuery('')
      setSampleCount(data.sample_count ?? null)
      setDiscoveryDone(true)
      setStatus(
        `Discovered ${cats.length} categories from ${data.sample_count ?? 0} sample posts.`,
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
    if (!selectedHandles.length) {
      setError('Select at least one saved source channel.')
      return
    }
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
      const data = await startExtraction(selectedHandles, selectedCategories, {
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
  const startDisabled = busy || discovering || isExtracting || selectedHandles.length === 0
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
          <span>Save source channel</span>
          <div className="channel-add-row scraper-add-row">
            <input
              type="text"
              placeholder="Name (optional)"
              value={channelNameInput}
              onChange={(e) => setChannelNameInput(e.target.value)}
              disabled={locked || isExtracting || addingChannel}
            />
            <input
              type="text"
              placeholder="@job_am or https://t.me/job_am"
              value={channelInput}
              onChange={(e) => setChannelInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  addChannels()
                }
              }}
              disabled={locked || isExtracting || addingChannel}
            />
            <button
              type="button"
              className="btn"
              onClick={addChannels}
              disabled={locked || isExtracting || addingChannel || !channelInput.trim()}
            >
              {addingChannel ? 'Saving…' : 'Add Channel'}
            </button>
          </div>
        </label>

        <fieldset className="option-box scraper-channel-box">
          <legend>Source channels ({scraperChannels.length})</legend>
          <p className="muted option-hint">
            Saved scrape sources persist on the server. Check the channels to use for discovery and
            extraction.
          </p>
          <div className="option-toolbar">
            <button
              type="button"
              className="btn tiny"
              disabled={locked || isExtracting || scraperChannels.length === 0}
              onClick={selectAllChannels}
            >
              Select All
            </button>
            <button
              type="button"
              className="btn tiny"
              disabled={locked || isExtracting || selectedChannelIds.length === 0}
              onClick={deselectAllChannels}
            >
              Deselect All
            </button>
            <span className="muted tiny-count">
              {selectedHandles.length} selected
              {scraperChannels.length ? ` · ${scraperChannels.length} saved` : ''}
            </span>
          </div>
          {channelsLoading ? (
            <p className="muted">Loading saved source channels…</p>
          ) : scraperChannels.length === 0 ? (
            <p className="muted">No source channels yet — add a handle above to persist it.</p>
          ) : (
            <ul className="scraper-channel-list" aria-label="Source scrape channels">
              {scraperChannels.map((channel) => {
                const checked = selectedChannelIds.includes(channel.id)
                return (
                  <li key={channel.id}>
                    <label className={`scraper-channel-row ${checked ? 'on' : ''}`}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleChannelSelected(channel.id)}
                        disabled={locked || isExtracting}
                      />
                      <span className="scraper-channel-copy">
                        <strong>{channel.name || channel.handle}</strong>
                        <span>{channel.handle}</span>
                      </span>
                    </label>
                    <button
                      type="button"
                      className="btn tiny danger-outline"
                      onClick={() => removeChannel(channel)}
                      disabled={locked || isExtracting || removingChannelId === channel.id}
                      aria-label={`Remove ${channel.handle}`}
                    >
                      {removingChannelId === channel.id ? '…' : 'Remove'}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </fieldset>

        <div className="actions">
          <button
            type="button"
            className="btn primary"
            onClick={handleDiscover}
            disabled={locked || isExtracting || selectedHandles.length === 0}
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
          <h3 className="step-title">2. Categories</h3>
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
