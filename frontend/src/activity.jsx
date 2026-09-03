import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

const ActivityContext = createContext(null)

const MAX_LOGS = 200

export function ActivityProvider({ children, onJobSaved, onDiscovered, onJobPublished }) {
  const [logs, setLogs] = useState([])
  const [connected, setConnected] = useState(false)
  const [latestByStage, setLatestByStage] = useState({})
  const [liveStatus, setLiveStatus] = useState('')
  const [pipelineBusy, setPipelineBusy] = useState(false)

  const onJobSavedRef = useRef(onJobSaved)
  const onDiscoveredRef = useRef(onDiscovered)
  const onJobPublishedRef = useRef(onJobPublished)
  const jobSavedTimer = useRef(null)
  const jobPublishedTimer = useRef(null)
  const publishBatchRef = useRef(false)

  useEffect(() => {
    onJobSavedRef.current = onJobSaved
  }, [onJobSaved])

  useEffect(() => {
    onDiscoveredRef.current = onDiscovered
  }, [onDiscovered])

  useEffect(() => {
    onJobPublishedRef.current = onJobPublished
  }, [onJobPublished])

  const pushLog = useCallback((event) => {
    setLogs((prev) => {
      const next = [...prev, event]
      return next.length > MAX_LOGS ? next.slice(-MAX_LOGS) : next
    })
    setLatestByStage((prev) => ({ ...prev, [event.stage]: event }))
    if (event.message) {
      setLiveStatus(event.message)
    }

    const stage = event.stage
    if (
      stage === 'DISCOVER_STARTED' ||
      stage === 'EXTRACTION_QUEUED' ||
      stage === 'EXTRACTION_STARTED' ||
      stage === 'JOINING_TELEGRAM' ||
      stage === 'CALLING_OLLAMA' ||
      stage === 'EXTRACTION_PROGRESS' ||
      stage === 'TRANSLATING' ||
      stage === 'PUBLISH_STARTED' ||
      stage === 'REPUBLISH_STARTED' ||
      stage === 'PUBLISH_PROGRESS' ||
      stage === 'PUBLISH_BATCH_QUEUED' ||
      stage === 'PUBLISH_BATCH_STARTED' ||
      stage === 'PUBLISH_FLOOD_WAIT' ||
      stage === 'BROADCAST_STARTED'
    ) {
      setPipelineBusy(true)
    }
    if (
      stage === 'PUBLISH_BATCH_QUEUED' ||
      stage === 'PUBLISH_BATCH_STARTED'
    ) {
      publishBatchRef.current = true
    }
    if (
      stage === 'DISCOVERED_CATEGORIES' ||
      stage === 'EXTRACTION_DONE' ||
      stage === 'EXTRACTION_STOPPED' ||
      stage === 'PUBLISH_BATCH_DONE' ||
      stage === 'BROADCAST_SENT' ||
      stage === 'ERROR'
    ) {
      if (stage === 'PUBLISH_BATCH_DONE') {
        publishBatchRef.current = false
      }
      setPipelineBusy(false)
    }
    if (stage === 'JOB_PUBLISHED' && !publishBatchRef.current) {
      setPipelineBusy(false)
    }

    if (stage === 'DISCOVERED_CATEGORIES') {
      onDiscoveredRef.current?.(event)
    }
    if (stage === 'JOB_SAVED') {
      if (jobSavedTimer.current) {
        clearTimeout(jobSavedTimer.current)
      }
      // Debounce burst saves into one feed refresh
      jobSavedTimer.current = setTimeout(() => {
        onJobSavedRef.current?.(event)
      }, 400)
    }
    if (stage === 'JOB_PUBLISHED' || stage === 'PUBLISH_BATCH_DONE' || stage === 'BROADCAST_SENT') {
      if (jobPublishedTimer.current) {
        clearTimeout(jobPublishedTimer.current)
      }
      jobPublishedTimer.current = setTimeout(() => {
        onJobPublishedRef.current?.(event)
      }, 350)
    }
  }, [])

  useEffect(() => {
    const es = new EventSource('/api/stream-logs')
    setConnected(false)

    const handlePayload = (raw) => {
      try {
        const event = JSON.parse(raw.data)
        pushLog(event)
      } catch {
        /* ignore malformed */
      }
    }

    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    // Named SSE events + default message fallback
    const stages = [
      'CONNECTED',
      'DISCOVER_STARTED',
      'JOINING_TELEGRAM',
      'FETCHING_POSTS',
      'LINK_SCRAPER',
      'CALLING_OLLAMA',
      'FALLBACK_ENGINE',
      'DISCOVERED_CATEGORIES',
      'EXTRACTION_QUEUED',
      'EXTRACTION_STARTED',
      'EXTRACTION_PROGRESS',
      'JOB_SAVED',
      'EXTRACTION_DONE',
      'EXTRACTION_STOP_REQUESTED',
      'EXTRACTION_STOPPED',
      'TRANSLATING',
      'TRANSLATION_DONE',
      'PUBLISH_STARTED',
      'REPUBLISH_STARTED',
      'PUBLISH_PROGRESS',
      'PUBLISH_BATCH_QUEUED',
      'PUBLISH_BATCH_STARTED',
      'PUBLISH_BATCH_DONE',
      'PUBLISH_FLOOD_WAIT',
      'JOB_PUBLISHED',
      'BROADCAST_STARTED',
      'BROADCAST_SENT',
      'STATS_FALLBACK',
      'ERROR',
    ]
    stages.forEach((name) => es.addEventListener(name, handlePayload))
    es.onmessage = handlePayload

    return () => {
      stages.forEach((name) => es.removeEventListener(name, handlePayload))
      es.close()
      if (jobSavedTimer.current) clearTimeout(jobSavedTimer.current)
      if (jobPublishedTimer.current) clearTimeout(jobPublishedTimer.current)
    }
  }, [pushLog])

  const clearLogs = useCallback(() => setLogs([]), [])

  const value = useMemo(
    () => ({
      logs,
      connected,
      latestByStage,
      liveStatus,
      pipelineBusy,
      clearLogs,
    }),
    [logs, connected, latestByStage, liveStatus, pipelineBusy, clearLogs],
  )

  return <ActivityContext.Provider value={value}>{children}</ActivityContext.Provider>
}

export function useActivity() {
  const ctx = useContext(ActivityContext)
  if (!ctx) {
    throw new Error('useActivity must be used within ActivityProvider')
  }
  return ctx
}
