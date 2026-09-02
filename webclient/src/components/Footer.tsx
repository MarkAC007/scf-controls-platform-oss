import { useState, useEffect, useCallback, useRef } from 'react'
import { toast } from 'react-hot-toast'
import { apiClient, type VersionInfo, type VersionUpdateInfo } from '../data/apiClient'

// Documentation/Book SVG icon — 15×15 per Explorer spec
const DocsIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
    <path d="M21 5c-1.11-.35-2.33-.5-3.5-.5-1.95 0-4.05.4-5.5 1.5-1.45-1.1-3.55-1.5-5.5-1.5S2.45 4.9 1 6v14.65c0 .25.25.5.5.5.1 0 .15-.05.25-.05C3.1 20.45 5.05 20 6.5 20c1.95 0 4.05.4 5.5 1.5 1.35-.85 3.8-1.5 5.5-1.5 1.65 0 3.35.3 4.75 1.05.1.05.15.05.25.05.25 0 .5-.25.5-.5V6c-.6-.45-1.25-.75-2-1zm0 13.5c-1.1-.35-2.3-.5-3.5-.5-1.7 0-4.15.65-5.5 1.5V8c1.35-.85 3.8-1.5 5.5-1.5 1.2 0 2.4.15 3.5.5v11.5z"/>
  </svg>
)

// Heart icon
const HeartIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="#ef4444" className="heart-icon">
    <path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/>
  </svg>
)

// Declare the global constant defined in vite.config.ts
declare const __APP_VERSION__: string

// Get app version from package.json (injected at build time via vite.config.ts)
const appVersion = __APP_VERSION__

// Documentation URL
const docsUrl = 'https://docs.scfcontrolsplatform.app/'

export default function Footer() {
  // The `update` object is returned to any authenticated user; only anonymous/
  // coarse responses omit it, so the badge simply never appears when logged out.
  // Any fetch failure degrades silently to no badge.
  const [update, setUpdate] = useState<VersionUpdateInfo | null>(null)
  const [platformVersion, setPlatformVersion] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const footerRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    let active = true
    apiClient
      .get<VersionInfo>('/version')
      .then(data => {
        if (!active) return
        setUpdate(data?.update ?? null)
        setPlatformVersion(data?.platform?.version ?? null)
      })
      .catch(() => { if (active) setUpdate(null) })
    return () => { active = false }
  }, [])

  // The footer is `position: fixed` at the bottom of the viewport and its height
  // changes as the version/credit/docs row wraps — 28px on a wide desktop, 56px
  // on a phone in landscape. Scroll containers therefore cannot hardcode the
  // clearance they need to keep their last row out from under it; they read this
  // property instead (styles.css, `--app-footer-height`).
  useEffect(() => {
    const el = footerRef.current
    if (!el) return

    const publishHeight = () => {
      document.documentElement.style.setProperty('--app-footer-height', `${el.offsetHeight}px`)
    }

    // Publish once up front: the observer's first callback is async, and until it
    // lands every consumer would fall back to the 28px default.
    publishHeight()

    const observer = new ResizeObserver(publishHeight)
    observer.observe(el)
    return () => {
      observer.disconnect()
      document.documentElement.style.removeProperty('--app-footer-height')
    }
  }, [])

  // The backend self-reports '0.0.0' when it cannot determine its version
  // (no /version/package.json mount, no PLATFORM_VERSION env) — treat that
  // sentinel as absent and fall back to the build-time package.json version.
  const realPlatformVersion = platformVersion && platformVersion !== '0.0.0' ? platformVersion : null
  const displayVersion = realPlatformVersion || appVersion

  // On-demand version check: re-reads /version (which serves the daily
  // update-check state) and says the answer out loud instead of relying on
  // the user noticing the badge.
  const checkVersion = useCallback(async () => {
    if (checking) return
    setChecking(true)
    try {
      const data = await apiClient.get<VersionInfo>('/version')
      const upd = data?.update ?? null
      setUpdate(upd)
      setPlatformVersion(data?.platform?.version ?? null)
      const reported = data?.platform?.version
      const current = reported && reported !== '0.0.0' ? reported : appVersion
      if (upd?.update_available) {
        toast(
          upd.skip_blocked
            ? `Update available, but upgrade to v${upd.min_upgradable_version} first.`
            : `Update available: v${upd.latest_version} (you are on v${current}).`,
          { icon: '⬆️', duration: 6000 },
        )
      } else if (upd && upd.check_enabled !== false && upd.update_available === false) {
        toast.success(`You're up to date — v${current}.`)
      } else if (upd && upd.check_enabled === false) {
        toast(`Running v${current}. Update checking is disabled on this instance.`)
      } else {
        toast(`Running v${current}. No update information available from this server.`)
      }
    } catch {
      toast.error('Version check failed — could not reach the server.')
    } finally {
      setChecking(false)
    }
  }, [checking])

  const showBadge = update?.update_available === true
  const amber = Boolean(update?.breaking || update?.skip_blocked)

  return (
    <footer className="app-footer" ref={footerRef}>
      <div className="footer-left">
        <button
          type="button"
          className="footer-version"
          onClick={() => void checkVersion()}
          disabled={checking}
          aria-busy={checking}
          title="Check for updates"
        >
          {checking ? 'Checking…' : `v${displayVersion}`}
        </button>
        {showBadge && (
          <a
            className={`footer-update-badge ${amber ? 'footer-update-badge--breaking' : 'footer-update-badge--available'}`}
            href={update?.release_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            {amber && <span aria-hidden="true">⚠</span>}
            {update?.skip_blocked
              ? `Upgrade to v${update?.min_upgradable_version} first`
              : `Update available → v${update?.latest_version}`}
          </a>
        )}
      </div>

      <div className="footer-center">
        <span className="footer-credit">
          Built and maintained by{' '}
          <a href="https://compliancegenie.io" target="_blank" rel="noopener noreferrer">
            compliancegenie.io
          </a>
        </span>
      </div>

      <div className="footer-right">
        <a
          href={docsUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="footer-docs-link"
          aria-label="Documentation"
          title="View Documentation"
        >
          <DocsIcon />
        </a>
      </div>
    </footer>
  )
}
