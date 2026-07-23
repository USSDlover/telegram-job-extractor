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
