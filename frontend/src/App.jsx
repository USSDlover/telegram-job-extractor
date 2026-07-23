import { useCallback, useState } from 'react'
import ControlPanel from './components/ControlPanel'
import JobDashboard from './components/JobDashboard'
import './App.css'

export default function App() {
  const [refreshToken, setRefreshToken] = useState(0)

  const onExtractionStarted = useCallback(() => {
    // Immediate refresh + a delayed one so background writes appear
    setRefreshToken((n) => n + 1)
    setTimeout(() => setRefreshToken((n) => n + 1), 8000)
  }, [])

  return (
    <div className="app">
      <header className="app-hero">
        <p className="brand">Telegram Job Extractor</p>
        <h1>Sample channels. Extract roles. Apply faster.</h1>
        <p className="lede">
          Local Gemma 2 pipelines turn Telegram posts into filtered, English job cards.
        </p>
      </header>
      <main className="layout">
        <ControlPanel onExtractionStarted={onExtractionStarted} />
        <JobDashboard refreshToken={refreshToken} />
      </main>
    </div>
  )
}
