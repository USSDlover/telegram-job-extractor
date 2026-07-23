import { useCallback, useState } from 'react'
import { ActivityProvider } from './activity'
import ControlPanel from './components/ControlPanel'
import JobDashboard from './components/JobDashboard'
import './App.css'

export default function App() {
  const [refreshToken, setRefreshToken] = useState(0)

  const bumpFeed = useCallback(() => {
    setRefreshToken((n) => n + 1)
  }, [])

  const onExtractionStarted = useCallback(() => {
    bumpFeed()
  }, [bumpFeed])

  const onJobSaved = useCallback(() => {
    bumpFeed()
  }, [bumpFeed])

  return (
    <ActivityProvider onJobSaved={onJobSaved}>
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
    </ActivityProvider>
  )
}
