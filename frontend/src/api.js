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

export async function startExtraction(channels, selectedCategories, selectedTitles) {
  const list = Array.isArray(channels) ? channels : [channels]
  const res = await fetch('/api/extract', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      channels: list,
      selected_categories: selectedCategories,
      selected_titles: selectedTitles,
    }),
  })
  return parseResponse(res)
}

export async function fetchJobs(category, sortBy = 'date_desc') {
  const params = new URLSearchParams()
  if (category) params.set('category', category)
  if (sortBy) params.set('sort_by', sortBy)
  const qs = params.toString() ? `?${params.toString()}` : ''
  const res = await fetch(`/api/jobs${qs}`)
  return parseResponse(res)
}

export async function fetchCategories() {
  const res = await fetch('/api/categories')
  return parseResponse(res)
}
