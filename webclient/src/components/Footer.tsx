// Documentation/Book SVG icon
const DocsIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
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
  return (
    <footer className="app-footer">
      <div className="footer-left">
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
          title="View Documentation"
        >
          <DocsIcon />
        </a>
      </div>
    </footer>
  )
}
