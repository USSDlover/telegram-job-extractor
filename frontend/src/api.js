const JSON_HEADERS = { 'Content-Type': 'application/json' }

async function parseResponse(res) {
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = data.detail || data.message || res.statusText
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return data
}

export async function discoverChannel(channel) {
  const res = await fetch('/api/discover', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ channel }),
  })
  return parseResponse(res)
}

export async function startExtraction(channel, selectedCategories, selectedTitles) {
  const res = await fetch('/api/extract', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      channel,
      selected_categories: selectedCategories,
      selected_titles: selectedTitles,
    }),
  })
  return parseResponse(res)
}

export async function fetchJobs(category) {
  const qs = category ? `?category=${encodeURIComponent(category)}` : ''
  const res = await fetch(`/api/jobs${qs}`)
  return parseResponse(res)
}

export async function fetchCategories() {
  const res = await fetch('/api/categories')
  return parseResponse(res)
}
