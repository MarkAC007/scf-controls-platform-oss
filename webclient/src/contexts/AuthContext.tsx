import { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import { GoogleOAuthProvider } from '@react-oauth/google'

interface User {
  id: string
  email: string
  name: string
  picture?: string
  db_id?: string
  role?: string
  is_platform_admin?: boolean
}

interface AuthContextType {
  user: User | null
  token: string | null
  isAuthenticated: boolean
  authReady: boolean
  login: (credential: string) => void
  logout: () => void
  refreshUserProfile: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

const GOOGLE_AUTH_ENABLED = import.meta.env.VITE_GOOGLE_AUTH_ENABLED === 'true'
const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID
// API key used as Bearer token when Google auth is disabled
const API_KEY = import.meta.env.VITE_API_KEY || ''

// Marketing website URL for signup redirects
const MARKETING_WEBSITE_URL = import.meta.env.VITE_MARKETING_WEBSITE_URL || 'https://scfcontrolsplatform.com'

// Error type for account not provisioned
interface AccountNotProvisionedError {
  error: 'account_not_provisioned'
  message: string
  redirect: string
}

function isAccountNotProvisionedError(data: unknown): data is AccountNotProvisionedError {
  if (typeof data !== 'object' || data === null) return false

  // FastAPI wraps HTTPException detail in a 'detail' field
  // Check both { error: ... } and { detail: { error: ... } } formats
  const errorObj = 'detail' in data && typeof (data as { detail: unknown }).detail === 'object'
    ? (data as { detail: Record<string, unknown> }).detail
    : data as Record<string, unknown>

  return (
    typeof errorObj === 'object' &&
    errorObj !== null &&
    'error' in errorObj &&
    (errorObj as { error: string }).error === 'account_not_provisioned'
  )
}

// Fetch user profile from backend
async function fetchUserProfile(token: string): Promise<User | null> {
  try {
    const response = await fetch('/api/users/me', {
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      }
    })

    // Handle 403 - account not provisioned (user hasn't signed up via website)
    if (response.status === 403) {
      try {
        const errorData = await response.json()
        if (isAccountNotProvisionedError(errorData)) {
          console.warn('⚠️ Account not provisioned. Redirecting to website signup...')
          // Clear the invalid token
          localStorage.removeItem('google_token')
          // Redirect to marketing website signup, preserving invite params if present
          const detail = (errorData as { detail?: { redirect?: string } }).detail
          let redirectUrl = detail?.redirect || (errorData as { redirect?: string }).redirect || `${MARKETING_WEBSITE_URL}/signup`
          // Preserve invite params so the user can accept after signup
          const currentParams = new URLSearchParams(window.location.search)
          const inviteParam = currentParams.get('invite')
          const inviteTypeParam = currentParams.get('invite_type')
          if (inviteParam) {
            const url = new URL(redirectUrl)
            url.searchParams.set('invite', inviteParam)
            if (inviteTypeParam) url.searchParams.set('invite_type', inviteTypeParam)
            redirectUrl = url.toString()
          }
          window.location.href = redirectUrl
          throw new Error('REDIRECT_IN_PROGRESS')
        }
      } catch (e) {
        // Re-throw REDIRECT_IN_PROGRESS
        if (e instanceof Error && e.message === 'REDIRECT_IN_PROGRESS') throw e
        // If we can't parse the error, still redirect to signup
        console.warn('⚠️ 403 Forbidden - redirecting to signup')
        localStorage.removeItem('google_token')
        let redirectUrl = `${MARKETING_WEBSITE_URL}/signup`
        const currentParams = new URLSearchParams(window.location.search)
        const inviteParam = currentParams.get('invite')
        const inviteTypeParam = currentParams.get('invite_type')
        if (inviteParam) {
          const url = new URL(redirectUrl)
          url.searchParams.set('invite', inviteParam)
          if (inviteTypeParam) url.searchParams.set('invite_type', inviteTypeParam)
          redirectUrl = url.toString()
        }
        window.location.href = redirectUrl
        throw new Error('REDIRECT_IN_PROGRESS')
      }
    }

    if (!response.ok) {
      console.warn('⚠️ Failed to fetch user profile:', response.status)
      return null
    }

    const data = await response.json()
    console.log('✅ Fetched user profile from backend:', data.email)

    return {
      id: data.google_sub || data.id,
      email: data.email,
      name: data.display_name || data.email?.split('@')[0] || 'User',
      db_id: data.id,
      role: data.role,
      is_platform_admin: data.is_platform_admin === true,
    }
  } catch (error) {
    console.error('❌ Error fetching user profile:', error)
    return null
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  // If Google auth is disabled, bypass all authentication UI
  if (!GOOGLE_AUTH_ENABLED) {
    console.log('ℹ️  Google auth disabled, using API key mode')
    const noAuthContextValue: AuthContextType = {
      user: null,
      token: API_KEY || null, // Provide API key so OrganizationContext and other callers can auth
      isAuthenticated: true, // Allow immediate access
      authReady: true,
      login: () => {},
      logout: () => {},
      refreshUserProfile: async () => {}
    }
    return (
      <AuthContext.Provider value={noAuthContextValue}>
        {children}
      </AuthContext.Provider>
    )
  }

  // Google auth enabled - proceed with normal auth flow
  const [user, setUser] = useState<User | null>(null)
  const [token, setToken] = useState<string | null>(null)
  const [authReady, setAuthReady] = useState(false)

  // Function to refresh user profile from backend
  const refreshUserProfile = async () => {
    const currentToken = token || localStorage.getItem('google_token')
    if (!currentToken) return

    const profile = await fetchUserProfile(currentToken)
    if (profile) {
      setUser(profile)
    }
  }

  useEffect(() => {
    console.log('🚀 AuthContext initializing...')
    // Check for existing token on mount
    const savedToken = localStorage.getItem('google_token')
    console.log('🔍 Checking localStorage for existing token:', savedToken ? 'FOUND' : 'NOT FOUND')

    if (savedToken) {
      console.log('📦 Found saved token, length:', savedToken.length)
      setToken(savedToken)

      // Set temporary user info while fetching from backend
      setUser({
        id: 'loading',
        email: 'Loading...',
        name: 'Loading...'
      })

      // Fetch actual user profile from backend
      fetchUserProfile(savedToken).then(profile => {
        if (profile) {
          setUser(profile)
        } else {
          // Fallback: Try to decode JWT if possible
          if (savedToken.split('.').length === 3) {
            try {
              const payload = JSON.parse(atob(savedToken.split('.')[1]))
              setUser({
                id: payload.sub,
                email: payload.email || 'Google User',
                name: payload.name || 'Google User'
              })
              console.log('✅ Restored user session from JWT:', payload.email)
            } catch (e) {
              setUser({
                id: 'google_user',
                email: 'Google User',
                name: 'Google User'
              })
            }
          } else {
            setUser({
              id: 'google_user',
              email: 'Google User',
              name: 'Google User'
            })
          }
        }
        setAuthReady(true)
        console.log('✅ AuthContext ready')
      }).catch(e => {
        // If redirect is in progress, browser is navigating - halt execution
        if (e instanceof Error && e.message === 'REDIRECT_IN_PROGRESS') {
          console.log('🔄 Redirect in progress, halting auth initialization')
          return
        }
        console.error('Error during profile fetch:', e)
        setAuthReady(true)
      })
    } else {
      console.log('ℹ️  No saved token found, user must sign in')
      setAuthReady(true)
      console.log('✅ AuthContext ready')
    }
  }, [])

  const login = async (credential: string) => {
    console.log('🔐 Login function called with credential length:', credential.length)

    try {
      // Save token to localStorage
      localStorage.setItem('google_token', credential)
      console.log('✅ Token saved to localStorage')

      // Verify it was saved correctly
      const savedToken = localStorage.getItem('google_token')
      if (savedToken !== credential) {
        console.error('❌ Token save verification failed! Token mismatch.')
        throw new Error('Failed to save authentication token')
      }
      console.log('✅ Token save verified successfully')

      setToken(credential)
      console.log('✅ Token state updated')

      // Set temporary user while fetching profile
      setUser({
        id: 'loading',
        email: 'Loading...',
        name: 'Loading...'
      })

      // Fetch user profile from backend (backend validates token and returns user info)
      const profile = await fetchUserProfile(credential)
      if (profile) {
        setUser(profile)
        console.log('✅ User authenticated:', profile.email)
      } else {
        // Fallback: Try to decode if it's a JWT
        if (credential.split('.').length === 3) {
          try {
            const payload = JSON.parse(atob(credential.split('.')[1]))
            setUser({
              id: payload.sub,
              email: payload.email || 'unknown',
              name: payload.name || 'Google User'
            })
            console.log('✅ User authenticated via Google (JWT):', payload.email)
          } catch (decodeError) {
            console.warn('Token is not a valid JWT, using fallback')
            setUser({
              id: 'google_user',
              email: 'Google User',
              name: 'Google User'
            })
          }
        } else {
          setUser({
            id: 'google_user',
            email: 'Google User',
            name: 'Google User'
          })
        }
      }

      console.log('✅ isAuthenticated should now be true')
    } catch (e) {
      // If redirect is in progress, browser is navigating - halt execution
      if (e instanceof Error && e.message === 'REDIRECT_IN_PROGRESS') {
        console.log('🔄 Redirect in progress, halting login flow')
        return
      }
      console.error('❌ Failed during login:', e)
    }
  }

  const logout = () => {
    localStorage.removeItem('google_token')
    setToken(null)
    setUser(null)
    console.log('👋 User signed out')
  }

  const contextValue: AuthContextType = {
    user,
    token,
    isAuthenticated: !!token,
    authReady,
    login,
    logout,
    refreshUserProfile
  }

  // Only require Client ID if Google auth is enabled
  if (GOOGLE_AUTH_ENABLED && !GOOGLE_CLIENT_ID) {
    console.error('❌ VITE_GOOGLE_CLIENT_ID not configured but GOOGLE_AUTH_ENABLED=true')
    return (
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100vh',
        flexDirection: 'column',
        gap: '20px',
        padding: '20px',
        textAlign: 'center'
      }}>
        <h1>Configuration Error</h1>
        <p>Google authentication is enabled but VITE_GOOGLE_CLIENT_ID is not configured.</p>
        <p>Please configure VITE_GOOGLE_CLIENT_ID in webclient/.env or set VITE_GOOGLE_AUTH_ENABLED=false</p>
        <p>See planning/GOOGLE_AUTH_QUICKSTART.md for setup instructions.</p>
      </div>
    )
  }

  return (
    <GoogleOAuthProvider
      clientId={GOOGLE_CLIENT_ID}
      onScriptLoadError={() => console.error('Failed to load Google Script')}
      onScriptLoadSuccess={() => console.log('✅ Google Script loaded successfully')}
      nonce={undefined}
    >
      <AuthContext.Provider value={contextValue}>
        {children}
      </AuthContext.Provider>
    </GoogleOAuthProvider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return context
}
