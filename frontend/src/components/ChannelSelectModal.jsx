import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { fetchChannels } from '../api'
import { PUBLISH_LANGUAGES, normalizePublishLanguage } from '../publishLanguages'
import { formatChannelList, normalizeTargetChannel } from '../targetChannels'

function optionValue(channel) {
  return normalizeTargetChannel(channel?.handle || channel?.username || '')
}

function languageForChannels(list, handles) {
  const selected = new Set((handles || []).map((item) => normalizeTargetChannel(item)))
  const match = (list || []).find((channel) => selected.has(optionValue(channel)) && channel.default_language)
  return normalizePublishLanguage(match?.default_language || 'English')
}

export default function ChannelSelectModal({
  isOpen,
  open,
  title = 'Publish to Telegram',
  description = 'Select one or more destination channels to receive this post.',
  confirmLabel = 'Publish Now',
  busy = false,
  republishMode = false,
  jobId,
  onCancel,
  onConfirm,
}) {
  const visible = Boolean(isOpen ?? open)
  const titleId = useId()
  const languageLegendId = useId()
  const onCancelRef = useRef(onCancel)
  const busyRef = useRef(busy)
  const [channels, setChannels] = useState([])
  const [selected, setSelected] = useState([])
  const [language, setLanguage] = useState('English')
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    onCancelRef.current = onCancel
  }, [onCancel])

  useEffect(() => {
    busyRef.current = busy
  }, [busy])

  useEffect(() => {
    if (!visible) {
      setChannels([])
      setSelected([])
      setLanguage('English')
      setLoading(false)
      setLoadError('')
      setError('')
      return undefined
    }

    let cancelled = false
    setLoading(true)
    setLoadError('')
    setError('')
    setSelected([])
    setLanguage('English')

    fetchChannels()
      .then((data) => {
        if (cancelled) return
        const list = data.channels || []
        setChannels(list)
        const defaults = list
          .filter((channel) => channel.is_default)
          .map(optionValue)
          .filter(Boolean)
        setSelected(defaults)
        setLanguage(languageForChannels(list, defaults))
      })
      .catch((err) => {
        if (!cancelled) {
          setLoadError(err.message || 'Could not load destination channels')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    function onKey(event) {
      if (event.key === 'Escape' && !busyRef.current) onCancelRef.current?.()
    }
    window.addEventListener('keydown', onKey)

    return () => {
      cancelled = true
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', onKey)
    }
  }, [visible])

  const values = useMemo(() => channels.map(optionValue).filter(Boolean), [channels])
  const allSelected = values.length > 0 && values.every((value) => selected.includes(value))
  const someSelected = values.some((value) => selected.includes(value)) && !allSelected
  const translating = busy && language !== 'English'

  function toggleChannel(channel) {
    const key = optionValue(channel)
    if (!key) return
    setSelected((prev) => {
      const checked = prev.includes(key)
      const next = checked ? prev.filter((item) => item !== key) : [...prev, key]
      if (!checked && channel.default_language) {
        setLanguage(normalizePublishLanguage(channel.default_language))
      }
      return next
    })
    setError('')
  }

  function toggleAll() {
    const next = allSelected ? [] : values
    setSelected(next)
    if (!allSelected) {
      setLanguage(languageForChannels(channels, next))
    }
    setError('')
  }

  function handleSubmit(event) {
    event.preventDefault()
    if (busy || loading) return
    if (!selected.length) {
      setError('Select at least one destination channel.')
      return
    }
    onConfirm?.({
      targetChannels: selected,
      language,
      republish: Boolean(republishMode),
    })
  }

  function handleCancel() {
    if (!busy) onCancel?.()
  }

  if (!visible || typeof document === 'undefined') return null

  return createPortal(
    <div className="modal-root" role="presentation">
      <button
        type="button"
        className="modal-backdrop"
        aria-label="Close channel picker"
        disabled={busy}
        onClick={handleCancel}
      />
      <div
        className="modal-card modal-card-wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-job-id={jobId || undefined}
      >
        <header className="modal-header">
          <h2 id={titleId}>{title}</h2>
          {republishMode && (
            <span className="badge republish-mode">Republishing Job to Selected Channels</span>
          )}
          <p>{description}</p>
        </header>
        <form className="modal-form" onSubmit={handleSubmit}>
          <fieldset className="language-picker" disabled={busy}>
            <legend id={languageLegendId}>Post language</legend>
            <div className="language-options" role="radiogroup" aria-labelledby={languageLegendId}>
              {PUBLISH_LANGUAGES.map((option) => {
                const checked = language === option.id
                return (
                  <label key={option.id} className={`language-option ${checked ? 'on' : ''}`}>
                    <input
                      type="radio"
                      name="publish-language"
                      value={option.id}
                      checked={checked}
                      onChange={() => setLanguage(option.id)}
                      disabled={busy}
                    />
                    <span>
                      {option.flag} <strong>{option.label}</strong>
                      {option.hint ? <em> · {option.hint}</em> : null}
                    </span>
                  </label>
                )
              })}
            </div>
          </fieldset>

          <div className="channel-picker-toolbar">
            <label className="select-all-control">
              <input
                type="checkbox"
                checked={allSelected}
                ref={(el) => {
                  if (el) el.indeterminate = someSelected
                }}
                onChange={toggleAll}
                disabled={busy || loading || values.length === 0}
              />
              <span>{allSelected ? 'Deselect All' : 'Select All'}</span>
            </label>
            <span className="tiny-count">
              {selected.length} selected
              {channels.length ? ` · ${channels.length} saved` : ''}
            </span>
          </div>

          {loading && (
            <p className="status busy-line">
              <span className="spinner" aria-hidden />
              Loading destination channels…
            </p>
          )}
          {translating && (
            <p className="status busy-line">
              <span className="spinner" aria-hidden />
              {republishMode
                ? `Translating job copy to ${language} for republication with Gemma 2…`
                : `Translating job copy to ${language} with Gemma 2, then posting to Telegram…`}
            </p>
          )}
          {busy && !translating && (
            <p className="status busy-line">
              <span className="spinner" aria-hidden />
              {republishMode ? 'Republishing to Telegram…' : 'Publishing to Telegram…'}
            </p>
          )}
          {loadError && <p className="status warn">{loadError}</p>}

          <ul className="channel-check-list" aria-label="Destination channels">
            {channels.map((channel) => {
              const value = optionValue(channel)
              const checked = selected.includes(value)
              const defaultLang = normalizePublishLanguage(channel.default_language)
              return (
                <li key={channel.id || value}>
                  <label className={`channel-check ${checked ? 'on' : ''}`}>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleChannel(channel)}
                      disabled={busy || !value}
                    />
                    <span className="channel-check-copy">
                      <strong>{channel.name || value}</strong>
                      <span>
                        {channel.handle || value}
                        {channel.is_default ? ' · default' : ''}
                        {defaultLang !== 'English' ? ` · ${defaultLang}` : ''}
                      </span>
                    </span>
                  </label>
                </li>
              )
            })}
          </ul>

          {!loading && channels.length === 0 && !loadError && (
            <p className="muted">
              No destination channels yet. Add them under Telegram Channel Hub → Manage destination
              channels.
            </p>
          )}

          {error && <p className="status err">{error}</p>}
          {selected.length > 0 && (
            <p className="muted">
              Will {republishMode ? 'republish' : 'publish'} in {language} to{' '}
              {formatChannelList(selected)}.
            </p>
          )}
          <div className="modal-actions">
            <button type="button" className="btn" onClick={handleCancel} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="btn accent" disabled={busy || loading || selected.length === 0}>
              {busy ? (
                <>
                  <span className="spinner" aria-hidden />
                  {translating ? 'Translating…' : republishMode ? 'Republishing…' : 'Publishing…'}
                </>
              ) : (
                confirmLabel
              )}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}
