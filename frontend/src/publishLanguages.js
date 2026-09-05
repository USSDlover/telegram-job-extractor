export const PUBLISH_LANGUAGES = [
  { id: 'English', flag: '🇬🇧', label: 'English', hint: 'Default' },
  { id: 'Persian', flag: '🇮🇷', label: 'Persian (فارسی)' },
  { id: 'Arabic', flag: '🇸🇦', label: 'Arabic (العربية)' },
]

export function normalizePublishLanguage(raw) {
  const key = String(raw || '').trim().toLowerCase()
  if (['fa', 'fas', 'farsi', 'persian', 'فارسی'].includes(key)) return 'Persian'
  if (['ar', 'ara', 'arabic', 'العربية'].includes(key)) return 'Arabic'
  return 'English'
}
