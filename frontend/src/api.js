const JSON_HEADERS = { 'Content-Type': 'application/json' }

async function parseResponse(res) {
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = data.detail || data.message || res.statusText
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return data
}

export async function discoverChannels(channels) {
  const res = await fetch('/api/discover', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ channels }),
  })
  return parseResponse(res)
}

/** @deprecated use discoverChannels */
export async function discoverChannel(channel) {
  return discoverChannels([channel])
}

export async function startExtraction(
  channels,
  selectedCategories,
  { datePreset = 'today', startDate, endDate } = {},
) {
  const list = Array.isArray(channels) ? channels : [channels]
  const body = {
    channels: list,
    selected_categories: selectedCategories,
    selected_titles: [],
    date_preset: datePreset,
  }
  if (datePreset === 'custom') {
    if (startDate) body.start_date = startDate
    if (endDate) body.end_date = endDate
  }
  const res = await fetch('/api/extract', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  })
  return parseResponse(res)
}

export async function fetchJobs({
  category,
  sortBy = 'date_desc',
  preset = 'all_time',
  startDate,
  endDate,
} = {}) {
  const params = new URLSearchParams()
  if (category) params.set('category', category)
  if (sortBy) params.set('sort_by', sortBy)
  if (preset) params.set('preset', preset)
  if (preset === 'custom') {
    if (startDate) params.set('start_date', startDate)
    if (endDate) params.set('end_date', endDate)
  }
  const qs = params.toString() ? `?${params.toString()}` : ''
  const res = await fetch(`/api/jobs${qs}`)
  return parseResponse(res)
}

export async function fetchCategories() {
  const res = await fetch('/api/categories')
  return parseResponse(res)
}

export async function deleteJob(jobId) {
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, {
    method: 'DELETE',
  })
  return parseResponse(res)
}

export async function clearAllJobs() {
  const res = await fetch('/api/jobs/clear-all', {
    method: 'DELETE',
  })
  return parseResponse(res)
}

export async function bulkDeleteJobs(jobIds) {
  const res = await fetch('/api/jobs/bulk-delete', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ job_ids: jobIds }),
  })
  return parseResponse(res)
}

export async function stopExtraction() {
  const res = await fetch('/api/stop-extraction', {
    method: 'POST',
    headers: JSON_HEADERS,
  })
  return parseResponse(res)
}

function asChannelList(value) {
  if (Array.isArray(value)) return value.filter(Boolean)
  if (value) return [value]
  return []
}

export async function publishJob(
  jobId,
  targetChannels,
  language = 'English',
  { republish = false } = {},
) {
  const channels = asChannelList(targetChannels)
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/publish`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      target_channels: channels,
      language,
      republish: Boolean(republish),
    }),
  })
  return parseResponse(res)
}

export async function publishAllPending({
  targetChannels,
  targetChannel,
  delaySeconds,
  language = 'English',
} = {}) {
  const channels = asChannelList(targetChannels?.length ? targetChannels : targetChannel)
  const res = await fetch('/api/jobs/publish-all-pending', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      target_channels: channels,
      language,
      ...(delaySeconds != null ? { delay_seconds: delaySeconds } : {}),
    }),
  })
  return parseResponse(res)
}

export async function publishSelectedJobs({
  jobIds,
  targetChannels,
  language = 'English',
  republish = false,
  delaySeconds,
} = {}) {
  const channels = asChannelList(targetChannels)
  const res = await fetch('/api/jobs/publish-selected', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      job_ids: (jobIds || []).filter(Boolean),
      target_channels: channels,
      language,
      republish: Boolean(republish),
      ...(delaySeconds != null ? { delay_seconds: delaySeconds } : {}),
    }),
  })
  return parseResponse(res)
}

export async function fetchTelegramStatus() {
  const res = await fetch('/api/telegram/status')
  return parseResponse(res)
}

export async function fetchAdminChannels({ force = false } = {}) {
  const qs = force ? '?force=true' : ''
  const res = await fetch(`/api/telegram/admin-channels${qs}`)
  return parseResponse(res)
}

export async function fetchChannels() {
  const res = await fetch('/api/channels')
  return parseResponse(res)
}

export async function fetchScraperChannels() {
  const res = await fetch('/api/scraper-channels')
  return parseResponse(res)
}

export async function addScraperChannel({ handle, name } = {}) {
  const res = await fetch('/api/scraper-channels', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      handle,
      ...(name ? { name } : {}),
    }),
  })
  return parseResponse(res)
}

export async function deleteScraperChannel(channelId) {
  const res = await fetch(`/api/scraper-channels/${encodeURIComponent(channelId)}`, {
    method: 'DELETE',
  })
  return parseResponse(res)
}

export async function addChannel({ name, handle, defaultLanguage = 'English' }) {
  const res = await fetch('/api/channels', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ name, handle, default_language: defaultLanguage }),
  })
  return parseResponse(res)
}

export async function deleteChannel(channelId) {
  const res = await fetch(`/api/channels/${encodeURIComponent(channelId)}`, {
    method: 'DELETE',
  })
  return parseResponse(res)
}

export async function toggleDefaultChannel(channelId) {
  const res = await fetch(`/api/channels/${encodeURIComponent(channelId)}/default`, {
    method: 'PATCH',
  })
  return parseResponse(res)
}

export async function updateChannelLanguage(channelId, defaultLanguage) {
  const res = await fetch(`/api/channels/${encodeURIComponent(channelId)}/language`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify({ default_language: defaultLanguage }),
  })
  return parseResponse(res)
}

export async function fetchTelegramStats({ force = false } = {}) {
  const qs = force ? '?force=true' : ''
  const res = await fetch(`/api/telegram/stats${qs}`)
  return parseResponse(res)
}

export async function sendBroadcast(message, targetChannels) {
  const channels = asChannelList(targetChannels)
  const res = await fetch('/api/telegram/broadcast', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      message,
      target_channels: channels,
    }),
  })
  return parseResponse(res)
}
