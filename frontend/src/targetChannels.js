const SELECTED_KEY = 'tg_publish_selected_channels'
const DEFAULT_KEY = 'tg_publish_default_channel'
const RECENT_KEY = 'tg_publish_recent_channels'
const MAX_RECENT = 12

export function normalizeTargetChannel(raw) {
  let name = String(raw || '').trim()
  if (!name) return ''
  if (name.startsWith('https://t.me/')) {
    name = name.replace(/\/$/, '').split('/').pop() || ''
  }
  if (/^-?\d+$/.test(name)) return name
  name = name.replace(/^@+/, '')
  if (!name) return ''
  return `@${name}`
}

export function channelKey(channel) {
  if (!channel) return ''
  if (typeof channel === 'string') return normalizeTargetChannel(channel)
  return normalizeTargetChannel(channel.username || String(channel.id || ''))
}

export function getSelectedTargetChannels() {
  try {
    const parsed = JSON.parse(localStorage.getItem(SELECTED_KEY) || '[]')
    if (!Array.isArray(parsed)) return []
    return dedupeChannels(parsed)
  } catch {
    return []
  }
}

export function rememberSelectedTargetChannels(channels) {
  const next = dedupeChannels(channels)
  try {
    localStorage.setItem(SELECTED_KEY, JSON.stringify(next))
    if (next[0]) localStorage.setItem(DEFAULT_KEY, next[0])
    const recents = dedupeChannels([...next, ...getRecentTargetChannels()])
    localStorage.setItem(RECENT_KEY, JSON.stringify(recents.slice(0, MAX_RECENT)))
  } catch {
    /* private mode / quota */
  }
  return next
}

export function getStoredDefaultChannel() {
  try {
    return normalizeTargetChannel(localStorage.getItem(DEFAULT_KEY) || '')
  } catch {
    return ''
  }
}

export function getRecentTargetChannels() {
  try {
    const parsed = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]')
    if (!Array.isArray(parsed)) return []
    return dedupeChannels(parsed)
  } catch {
    return []
  }
}

export function rememberTargetChannel(raw) {
  const channel = normalizeTargetChannel(raw)
  if (!channel) return ''
  rememberSelectedTargetChannels([channel, ...getSelectedTargetChannels()])
  return channel
}

function dedupeChannels(values) {
  const out = []
  const seen = new Set()
  for (const item of values || []) {
    const channel = normalizeTargetChannel(item)
    const key = channel.toLowerCase()
    if (!channel || seen.has(key)) continue
    seen.add(key)
    out.push(channel)
  }
  return out
}

export function formatChannelList(channels) {
  const list = dedupeChannels(channels)
  if (!list.length) return ''
  return `${list.length} channel${list.length === 1 ? '' : 's'} (${list.join(', ')})`
}
