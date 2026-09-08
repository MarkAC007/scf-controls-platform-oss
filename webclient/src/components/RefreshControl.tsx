import { useEffect, useState, type ReactElement } from 'react'
import { useDataRefresh } from '../contexts/DataRefreshContext'

function formatUpdatedCaption(lastUpdatedAt: number | null, now: number): string {
  if (lastUpdatedAt === null) {
    // Empty caption: without a timestamp there is no truthful update age to show.
    return ''
  }

  const elapsedMs = now - lastUpdatedAt
  if (elapsedMs < 10000) {
    return 'Updated just now'
  }

  if (elapsedMs < 60000) {
    return `Updated ${Math.floor(elapsedMs / 1000)}s ago`
  }

  if (elapsedMs < 3600000) {
    return `Updated ${Math.floor(elapsedMs / 60000)}m ago`
  }

  return `Updated ${Math.floor(elapsedMs / 3600000)}h ago`
}

export default function RefreshControl(): ReactElement {
  const { lastUpdatedAt, updatesAvailable, isRefreshing, refresh } = useDataRefresh()
  const [currentTime, setCurrentTime] = useState(() => Date.now())

  useEffect(() => {
    const intervalId = setInterval(() => {
      setCurrentTime(Date.now())
    }, 10000)

    return () => {
      clearInterval(intervalId)
    }
  }, [])

  const captionText = updatesAvailable
    ? 'Updates available'
    : formatUpdatedCaption(lastUpdatedAt, currentTime)

  return (
    <div className={`refresh-control${updatesAvailable ? ' refresh-control--updates' : ''}`}>
      {updatesAvailable && <span className="refresh-control-dot" aria-hidden="true" />}
      <span
        className="refresh-control-caption"
        data-state={updatesAvailable ? 'updates' : 'fresh'}
      >
        {captionText}
      </span>
      <button
        type="button"
        className="notification-bell-button refresh-control-button"
        aria-label="Refresh data"
        title="Refresh data (R)"
        onClick={refresh}
        // Deliberately not disabled while fetching. With refetchOnWindowFocus
        // on, useIsFetching() is frequently non-zero the moment a user returns
        // to the tab — exactly when they reach for this button. The spin is
        // the feedback; a second click just bumps the epoch again.
        aria-busy={isRefreshing}
        data-refreshing={isRefreshing}
      >
        <svg
          className="refresh-control-icon"
          width="24"
          height="24"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M21 12a9 9 0 1 1-2.64-6.36" />
          <path d="M21 3v6h-6" />
        </svg>
      </button>
    </div>
  )
}
