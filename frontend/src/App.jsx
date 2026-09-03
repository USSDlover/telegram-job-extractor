import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchChannels, fetchTelegramStats, fetchTelegramStatus } from './api'
import { ActivityProvider } from './activity'
import ActivityConsole from './components/ActivityConsole'
import ControlPanel from './components/ControlPanel'
import JobDashboard from './components/JobDashboard'
import TelegramHub from './components/TelegramHub'
import './App.css'

function formatSubscribers(value) {
  if (value == null || value === '') return null
  const num = Number(value)
  if (!Number.isFinite(num)) return String(value)
  return num.toLocaleString()
}

function liveByHandle(stats) {
  const map = new Map()
  for (const row of stats?.channels || []) {
    const handle = String(row?.handle || '').toLowerCase()
    if (handle) map.set(handle, row)
  }
  const primary = String(stats?.channel || stats?.target_channel || '').toLowerCase()
  if (primary && !map.has(primary) && (stats?.subscribers != null || stats?.channel)) {
    map.set(primary, {
      handle: stats.channel || stats.target_channel,
      online: Boolean(stats.authorized && stats.connected),
      subscribers: stats.subscribers,
      title: stats.channel_title,
      error: stats.fallback_reason || stats.error,
    })
  }
  return map
}

function channelTooltip(channel, live, sessionOnline) {
  const handle = channel.handle || 'Unknown channel'
  const name = channel.name && channel.name !== handle ? channel.name : live?.title
  const online = live ? Boolean(live.online) : sessionOnline
  const subscribers = formatSubscribers(live?.subscribers)
  const lines = [
    handle,
    name && name !== handle ? name : null,
    `Status: ${online ? 'Live' : 'Offline'}`,
    subscribers ? `Subscribers: ${subscribers}` : 'Subscribers: unavailable',
    live?.error && !online ? live.error : null,
  ]
  return lines.filter(Boolean).join('\n')
}

function ChannelStatusBar({ status, channels, stats }) {
  const sessionOnline = Boolean(status.authorized && status.connected)
  const liveMap = useMemo(() => liveByHandle(stats), [stats])
  const label = status.loading
    ? 'Checking session…'
    : sessionOnline
      ? 'Session live'
      : 'Session offline'

  return (
    <div className="channel-status-bar">
      <div
        className={`session-badge ${status.loading ? 'checking' : sessionOnline ? 'online' : 'offline'}`}
        title={status.error || status.user || 'Telegram session'}
      >
        <span className={`pulse ${status.loading ? '' : sessionOnline ? 'on' : 'off'}`} aria-hidden />
        <span className="session-copy">
          <strong>{label}</strong>
          {status.user ? <span className="session-channel">{status.user}</span> : null}
        </span>
      </div>
      <div className="channel-status-pills" role="list" aria-label="Destination channel status">
        {status.loading && channels.length === 0 && (
          <span className="channel-status-pill checking" role="listitem">
            Loading channels…
          </span>
        )}
        {!status.loading && channels.length === 0 && (
          <span className="channel-status-pill empty" role="listitem" title="Add destinations in Telegram Channel Hub">
            No destination channels
          </span>
        )}
        {channels.map((channel) => {
          const live = liveMap.get(String(channel.handle || '').toLowerCase())
          const online = live ? Boolean(live.online) : sessionOnline
          const checking = status.loading || (sessionOnline && !stats)
          const tone = checking ? 'checking' : online ? 'online' : 'offline'
          const subscribers = formatSubscribers(live?.subscribers)
          return (
            <span
              key={channel.id || channel.handle}
              role="listitem"
              className={`channel-status-pill ${tone}`}
              title={channelTooltip(channel, live, sessionOnline)}
            >
              <span className="channel-status-dot" aria-hidden>
                {checking ? '🟡' : online ? '🟢' : '🔴'}
              </span>
              <span className="channel-status-handle">{channel.handle}</span>
              {subscribers ? <span className="channel-status-subs">{subscribers}</span> : null}
            </span>
          )
        })}
      </div>
    </div>
  )
}

export default function App() {
  const [page, setPage] = useState('extractor')
  const [refreshToken, setRefreshToken] = useState(0)
  const [hubToken, setHubToken] = useState(0)
  const [channels, setChannels] = useState([])
  const [channelStats, setChannelStats] = useState(null)
  const [tgStatus, setTgStatus] = useState({
    authorized: false,
    connected: false,
    target_channel: '',
    user: null,
    error: null,
    loading: true,
  })

  const bumpFeed = useCallback(() => {
    setRefreshToken((n) => n + 1)
  }, [])

  const bumpHub = useCallback(() => {
    setHubToken((n) => n + 1)
  }, [])

  const onExtractionStarted = useCallback(() => {
    bumpFeed()
  }, [bumpFeed])

  const onJobSaved = useCallback(() => {
    bumpFeed()
  }, [bumpFeed])

  const onJobPublished = useCallback(() => {
    bumpFeed()
    bumpHub()
  }, [bumpFeed, bumpHub])

  useEffect(() => {
    let cancelled = false

    async function loadHeader() {
      try {
        const [channelData, status] = await Promise.all([fetchChannels(), fetchTelegramStatus()])
        if (cancelled) return
        setChannels(channelData.channels || [])
        setTgStatus({ ...status, loading: false })
      } catch (err) {
        if (!cancelled) {
          setTgStatus((prev) => ({
            ...prev,
            loading: false,
            connected: false,
            authorized: false,
            error: err.message || 'Status check failed',
          }))
        }
      }

      try {
        const stats = await fetchTelegramStats()
        if (!cancelled) setChannelStats(stats)
      } catch {
        if (!cancelled) setChannelStats(null)
      }
    }

    loadHeader()
    const timer = setInterval(loadHeader, 60000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [hubToken, refreshToken])

  return (
    <ActivityProvider onJobSaved={onJobSaved} onJobPublished={onJobPublished}>
      <div className="app">
        <nav className="app-nav" aria-label="Primary">
          <div className="nav-brand">
            <p className="brand">Telegram Job Extractor</p>
          </div>
          <div className="nav-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={page === 'extractor'}
              className={`nav-tab ${page === 'extractor' ? 'active' : ''}`}
              onClick={() => setPage('extractor')}
            >
              📥 Extractor Dashboard
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={page === 'hub'}
              className={`nav-tab ${page === 'hub' ? 'active' : ''}`}
              onClick={() => setPage('hub')}
            >
              📢 Telegram Channel Hub
            </button>
          </div>
          <ChannelStatusBar status={tgStatus} channels={channels} stats={channelStats} />
        </nav>

        {page === 'extractor' && (
          <header className="app-hero">
            <h1>Sample channels. Extract roles. Apply faster.</h1>
            <p className="lede">
              Local Gemma 2 pipelines turn Telegram posts into filtered, English job cards.
            </p>
          </header>
        )}

        <div className={`layout-top ${page === 'hub' ? 'hub-layout' : ''}`}>
          {page === 'extractor' ? (
            <ControlPanel onExtractionStarted={onExtractionStarted} />
          ) : (
            <TelegramHub refreshToken={hubToken} />
          )}
          <aside className="activity-rail">
            <ActivityConsole />
          </aside>
        </div>

        {page === 'extractor' && <JobDashboard refreshToken={refreshToken} />}
      </div>
    </ActivityProvider>
  )
}
