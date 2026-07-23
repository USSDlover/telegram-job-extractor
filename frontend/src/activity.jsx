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

export function ActivityProvider({ children, onJobSaved, onDiscovered }) {
  const [logs, setLogs] = useState([])
  const [connected, setConnected] = useState(false)
  const [latestByStage, setLatestByStage] = useState({})
  const [liveStatus, setLiveStatus] = useState('')
  const [pipelineBusy, setPipelineBusy] = useState(false)

  const onJobSavedRef = useRef(onJobSaved)
  const onDiscoveredRef = useRef(onDiscovered)
  const jobSavedTimer = useRef(null)

  useEffect(() => {
    onJobSavedRef.current = onJobSaved
  }, [onJobSaved])

  useEffect(() => {
    onDiscoveredRef.current = onDiscovered
  }, [onDiscovered])

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
      stage === 'EXTRACTION_PROGRESS'
    ) {
      setPipelineBusy(true)
    }
    if (
      stage === 'DISCOVERED_CATEGORIES' ||
      stage === 'EXTRACTION_DONE' ||
      stage === 'ERROR'
    ) {
      // Keep busy briefly on discovery so UI can settle; extraction done clears
      if (stage !== 'DISCOVERED_CATEGORIES') {
        setPipelineBusy(false)
      }
      if (stage === 'DISCOVERED_CATEGORIES') {
        setPipelineBusy(false)
      }
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
      'CALLING_OLLAMA',
      'DISCOVERED_CATEGORIES',
      'EXTRACTION_QUEUED',
      'EXTRACTION_STARTED',
      'EXTRACTION_PROGRESS',
      'JOB_SAVED',
      'EXTRACTION_DONE',
      'ERROR',
    ]
    stages.forEach((name) => es.addEventListener(name, handlePayload))
    es.onmessage = handlePayload

    return () => {
      stages.forEach((name) => es.removeEventListener(name, handlePayload))
      es.close()
      if (jobSavedTimer.current) clearTimeout(jobSavedTimer.current)
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
