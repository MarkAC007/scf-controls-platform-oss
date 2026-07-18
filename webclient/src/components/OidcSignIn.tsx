import { useState } from 'react'
import Footer from './Footer'

// Human-readable copy for the auth_error codes the backend can redirect with.
const AUTH_ERROR_MESSAGES: Record<string, string> = {
  account_not_provisioned:
    'Your account has not been provisioned for this platform. Please contact your administrator.',
}

export default function OidcSignIn() {
  const [isRedirecting, setIsRedirecting] = useState(false)

  // Configurable logo and title, matching GoogleSignIn.
  const appLogoEnv = import.meta.env.VITE_APP_LOGO
  const appLogo = appLogoEnv === '' ? null : (appLogoEnv || '/compliancegenie-logo.png')
  const appTitle = import.meta.env.VITE_APP_TITLE || 'SCF Controls Platform'

  // Surface the backend-provided auth_error (set on the callback failure redirect).
  const authError = new URLSearchParams(window.location.search).get('auth_error')
  const authErrorMessage = authError
    ? (AUTH_ERROR_MESSAGES[authError] || 'Sign-in failed. Please try again.')
    : null

  const startLogin = () => {
    setIsRedirecting(true)
    // Full browser navigation to the backend, which 302s to the IdP.
    window.location.href = '/api/auth/login'
  }

  return (
    <div className="auth-page">
      <div className="auth-page-container">
        <div className="auth-card">
          {appLogo && (
            <img
              src={appLogo}
              alt="Logo"
              className="auth-logo"
            />
          )}
          <h1 className="auth-title">{appTitle}</h1>
          <p className="auth-subtitle">
            Common Compliance Framework Management Platform
          </p>
          {authErrorMessage && (
            <p className="auth-help-text" role="alert" style={{ color: '#c0392b' }}>
              {authErrorMessage}
            </p>
          )}
          <p className="auth-help-text">
            Please sign in to continue
          </p>
          <button
            onClick={startLogin}
            disabled={isRedirecting}
            className="auth-google-btn"
          >
            {isRedirecting ? (
              <>
                <span>...</span> Redirecting...
              </>
            ) : (
              'Sign in'
            )}
          </button>
          <p className="auth-disclaimer">
            By signing in, you agree to use this application in accordance with your organization's policies.
          </p>
        </div>
      </div>
      <Footer />
    </div>
  )
}
