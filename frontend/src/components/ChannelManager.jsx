import { useCallback, useEffect, useState } from 'react'
import { addChannel, deleteChannel, fetchChannels, toggleDefaultChannel, updateChannelLanguage } from '../api'
import { PUBLISH_LANGUAGES, normalizePublishLanguage } from '../publishLanguages'
import { normalizeTargetChannel } from '../targetChannels'

export default function ChannelManager({ refreshToken = 0 }) {
  const [channels, setChannels] = useState([])
  const [name, setName] = useState('')
  const [handle, setHandle] = useState('')
  const [defaultLanguage, setDefaultLanguage] = useState('English')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [busyId, setBusyId] = useState('')
  const [error, setError] = useState('')
  const [note, setNote] = useState('')

  const loadChannels = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await fetchChannels()
      setChannels(data.channels || [])
    } catch (err) {
      setError(err.message || 'Failed to load destination channels')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadChannels()
  }, [loadChannels, refreshToken])

  async function handleAdd(event) {
    event.preventDefault()
    const cleanedHandle = normalizeTargetChannel(handle)
    const cleanedName = name.trim()
    if (!cleanedName) {
      setError('Enter a channel name.')
      return
    }
    if (!cleanedHandle) {
      setError('Enter a channel handle, for example @huntjobarmenia.')
      return
    }

    setSaving(true)
    setError('')
    setNote('')
    try {
      const result = await addChannel({
        name: cleanedName,
        handle: cleanedHandle,
        defaultLanguage,
      })
      setChannels(result.channels || [])
      setName('')
      setHandle('')
      setDefaultLanguage('English')
      setNote(`Added ${result.channel?.handle || cleanedHandle}.`)
    } catch (err) {
      setError(err.message || 'Failed to add channel')
    } finally {
      setSaving(false)
    }
  }

  async function handleToggleDefault(channel) {
    if (!channel?.id) return
    setBusyId(channel.id)
    setError('')
    setNote('')
    try {
      const result = await toggleDefaultChannel(channel.id)
      setChannels(result.channels || [])
      const next = result.channel
      if (next) {
        setNote(
          next.is_default
            ? `${next.handle} is now a default publish target.`
            : `${next.handle} is no longer a default target.`,
        )
      }
    } catch (err) {
      setError(err.message || 'Failed to update default flag')
    } finally {
      setBusyId('')
    }
  }

  async function handleLanguageChange(channel, nextLanguage) {
    if (!channel?.id) return
    const language = normalizePublishLanguage(nextLanguage)
    if (normalizePublishLanguage(channel.default_language) === language) return
    setBusyId(channel.id)
    setError('')
    setNote('')
    try {
      const result = await updateChannelLanguage(channel.id, language)
      setChannels(result.channels || [])
      setNote(`${channel.handle} default language set to ${language}.`)
    } catch (err) {
      setError(err.message || 'Failed to update channel language')
    } finally {
      setBusyId('')
    }
  }

  async function handleDelete(channel) {
    if (!channel?.id) return
    const ok = window.confirm(`Remove destination channel “${channel.name || channel.handle}”?`)
    if (!ok) return

    setBusyId(channel.id)
    setError('')
    setNote('')
    try {
      await deleteChannel(channel.id)
      setChannels((prev) => prev.filter((item) => item.id !== channel.id))
      setNote(`Removed ${channel.handle}.`)
    } catch (err) {
      setError(err.message || 'Failed to delete channel')
      await loadChannels()
    } finally {
      setBusyId('')
    }
  }

  const busy = loading || saving || Boolean(busyId)

  return (
    <section className="panel channel-manager">
      <header className="panel-header row">
        <div>
          <h2>Manage destination channels</h2>
          <p>
            Saved admin channels used as publish targets. Defaults are pre-checked when you publish,
            and a channel language pre-selects the post language.
          </p>
        </div>
        <div className="toolbar">
          <button type="button" className="btn" onClick={loadChannels} disabled={busy}>
            {loading ? (
              <>
                <span className="spinner" aria-hidden />
                Loading…
              </>
            ) : (
              'Refresh'
            )}
          </button>
        </div>
      </header>

      <form className="channel-add-form" onSubmit={handleAdd}>
        <label className="field">
          <span>Channel name</span>
          <input
            value={name}
            onChange={(e) => {
              setName(e.target.value)
              setError('')
            }}
            placeholder="Main Channel"
            autoComplete="off"
            disabled={saving}
          />
        </label>
        <label className="field">
          <span>Handle</span>
          <input
            value={handle}
            onChange={(e) => {
              setHandle(e.target.value)
              setError('')
            }}
            placeholder="@huntjobarmenia"
            autoComplete="off"
            spellCheck={false}
            disabled={saving}
          />
        </label>
        <label className="field">
          <span>Default language</span>
          <select
            value={defaultLanguage}
            onChange={(e) => setDefaultLanguage(e.target.value)}
            disabled={saving}
          >
            {PUBLISH_LANGUAGES.map((option) => (
              <option key={option.id} value={option.id}>
                {option.flag} {option.label}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="btn accent" disabled={saving || !name.trim() || !handle.trim()}>
          {saving ? (
            <>
              <span className="spinner" aria-hidden />
              Adding…
            </>
          ) : (
            'Add channel'
          )}
        </button>
      </form>

      {error && <p className="status err">{error}</p>}
      {note && !error && <p className="status ok">{note}</p>}

      {loading && channels.length === 0 && <p className="muted">Loading saved channels…</p>}
      {!loading && channels.length === 0 && (
        <p className="muted empty">No destination channels yet. Add a name and @handle above.</p>
      )}

      {channels.length > 0 && (
        <div className="channel-table-wrap">
          <table className="channel-table">
            <thead>
              <tr>
                <th>Default</th>
                <th>Name</th>
                <th>Handle</th>
                <th>Language</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {channels.map((channel) => {
                const rowBusy = busyId === channel.id
                return (
                  <tr key={channel.id}>
                    <td>
                      <label className="default-toggle">
                        <input
                          type="checkbox"
                          checked={Boolean(channel.is_default)}
                          onChange={() => handleToggleDefault(channel)}
                          disabled={busy}
                          aria-label={`Toggle default for ${channel.handle}`}
                        />
                        <span>{channel.is_default ? 'On' : 'Off'}</span>
                      </label>
                    </td>
                    <td>
                      <strong>{channel.name}</strong>
                    </td>
                    <td>
                      <span className="channel-handle">{channel.handle}</span>
                    </td>
                    <td>
                      <select
                        className="channel-language-select"
                        value={normalizePublishLanguage(channel.default_language)}
                        onChange={(e) => handleLanguageChange(channel, e.target.value)}
                        disabled={busy}
                        aria-label={`Default language for ${channel.handle}`}
                      >
                        {PUBLISH_LANGUAGES.map((option) => (
                          <option key={option.id} value={option.id}>
                            {option.flag} {option.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn tiny danger-outline"
                        onClick={() => handleDelete(channel)}
                        disabled={busy}
                      >
                        {rowBusy ? '…' : 'Delete'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
