import { useGoogleLogin } from '@react-oauth/google'
import { useAuth } from '../contexts/AuthContext'
import { useState } from 'react'
import toast from 'react-hot-toast'
import Footer from './Footer'

export default function GoogleSignIn() {
  const { login } = useAuth()
  const [isLoading, setIsLoading] = useState(false)

  // Get configurable logo and title from environment variables
  const appLogoEnv = import.meta.env.VITE_APP_LOGO
  const appLogo = appLogoEnv === '' ? null : (appLogoEnv || '/compliancegenie-logo.png')
  const appTitle = import.meta.env.VITE_APP_TITLE || 'SCF Controls Platform'

  const googleLogin = useGoogleLogin({
    onSuccess: async (tokenResponse) => {
      console.log('Google OAuth successful')
      console.log('Token response type:', tokenResponse)

      // The implicit flow gives us access_token but NOT id_token
      // We need to store the access_token - backend will validate it
      if (tokenResponse.access_token) {
        console.log('Got access token, length:', tokenResponse.access_token.length)
        login(tokenResponse.access_token)
        setIsLoading(false)
      }
    },
    onError: (error) => {
      console.error('Google sign-in failed:', error)
      toast.error(`Sign-in failed: ${error.error || 'Unknown error'}. Please try again.`, {
        duration: 5000,
      })
      setIsLoading(false)
    },
  })

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
          <p className="auth-help-text">
            Please sign in with your Google account to continue
          </p>
          <button
            onClick={() => googleLogin()}
            disabled={isLoading}
            className="auth-google-btn"
          >
            {isLoading ? (
              <>
                <span>...</span> Authenticating...
              </>
            ) : (
              <>
                <svg width="18" height="18" viewBox="0 0 48 48">
                  <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
                  <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
                  <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
                  <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
                  <path fill="none" d="M0 0h48v48H0z"/>
                </svg>
                Sign in with Google
              </>
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
