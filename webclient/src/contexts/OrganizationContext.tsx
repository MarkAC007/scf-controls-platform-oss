/**
 * OrganizationContext - Multi-tenancy context for organization management
 *
 * Provides:
 * - Current organization state with localStorage persistence
 * - List of accessible organizations (membership + consultant relationships)
 * - Organization switching functionality
 * - Recent organizations tracking for quick access
 *
 * SECURITY: Only shows organisations the user has access to via:
 * - Direct membership (OrganizationMember)
 * - Active consultant relationships (ConsultantClientRelationship)
 */
import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react'
import { useAuth } from './AuthContext'
import { getAuthToken } from '../data/authToken'

// Storage keys for localStorage persistence
const ORG_STORAGE_KEY = 'scf_current_org_id'
const RECENT_ORGS_KEY = 'scf_recent_org_ids'
const MAX_RECENT_ORGS = 5

export interface Organization {
  id: string
  name: string
  slug: string
  created_at: string
  updated_at: string
}

interface OrganizationContextType {
  /** Currently selected organization */
  currentOrg: Organization | null
  /** All organizations the user can access */
  availableOrgs: Organization[]
  /** True while loading organization data */
  isLoading: boolean
  /** Error message if org loading failed */
  error: string | null
  /** Recently accessed organization IDs (for quick switching) */
  recentOrgIds: string[]
  /** Switch to a different organization */
  switchOrganization: (orgId: string) => Promise<void>
  /** Refresh the list of available organizations */
  refreshOrganizations: () => Promise<void>
  /** Set current org ID directly (used by invite acceptance) */
  setCurrentOrgId: (orgId: string) => void
  /** Clear organization context (used on logout) */
  clearOrgContext: () => void
}

const OrganizationContext = createContext<OrganizationContextType | undefined>(undefined)

// API base URL from environment
const API_BASE_URL = '/api'

/**
 * Fetch organizations from the backend
 * Backend filters to only return orgs the user can access
 */
async function fetchOrganizations(token: string | null): Promise<Organization[]> {
  const headers: HeadersInit = {
    'Content-Type': 'application/json'
  }

  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const response = await fetch(`${API_BASE_URL}/organizations`, { headers })

  if (!response.ok) {
    const errorText = await response.text()
    let errorMessage = `Failed to fetch organizations: ${response.status}`
    try {
      const errorJson = JSON.parse(errorText)
      errorMessage = errorJson.detail || errorMessage
    } catch {
      // Use default error message
    }
    throw new Error(errorMessage)
  }

  return response.json()
}

/**
 * Fetch a single organization by ID
 */
async function fetchOrganization(orgId: string, token: string | null): Promise<Organization> {
  const headers: HeadersInit = {
    'Content-Type': 'application/json'
  }

  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const response = await fetch(`${API_BASE_URL}/organizations/${orgId}`, { headers })

  if (!response.ok) {
    const errorText = await response.text()
    let errorMessage = `Failed to fetch organization: ${response.status}`
    try {
      const errorJson = JSON.parse(errorText)
      errorMessage = errorJson.detail || errorMessage
    } catch {
      // Use default error message
    }
    throw new Error(errorMessage)
  }

  return response.json()
}

export function OrganizationProvider({ children }: { children: ReactNode }) {
  const { isAuthenticated, authReady, token } = useAuth()

  const [currentOrg, setCurrentOrg] = useState<Organization | null>(null)
  const [availableOrgs, setAvailableOrgs] = useState<Organization[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [recentOrgIds, setRecentOrgIds] = useState<string[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(RECENT_ORGS_KEY) || '[]')
    } catch {
      return []
    }
  })

  /**
   * Add an org ID to the recent list
   */
  const addToRecentOrgs = useCallback((orgId: string) => {
    setRecentOrgIds(prev => {
      // Remove if already exists, then add to front
      const filtered = prev.filter(id => id !== orgId)
      const updated = [orgId, ...filtered].slice(0, MAX_RECENT_ORGS)
      localStorage.setItem(RECENT_ORGS_KEY, JSON.stringify(updated))
      return updated
    })
  }, [])

  /**
   * Set current org ID directly (used by invite acceptance)
   */
  const setCurrentOrgId = useCallback((orgId: string) => {
    localStorage.setItem(ORG_STORAGE_KEY, orgId)
    addToRecentOrgs(orgId)
    console.log(`📍 Organization ID set to: ${orgId}`)
  }, [addToRecentOrgs])

  /**
   * Clear organization context (used on logout)
   */
  const clearOrgContext = useCallback(() => {
    localStorage.removeItem(ORG_STORAGE_KEY)
    localStorage.removeItem(RECENT_ORGS_KEY)
    setCurrentOrg(null)
    setAvailableOrgs([])
    setRecentOrgIds([])
    setError(null)
    console.log('🧹 Organization context cleared')
  }, [])

  /**
   * Refresh the list of available organizations
   */
  const refreshOrganizations = useCallback(async () => {
    if (!isAuthenticated || !authReady) {
      console.log('⏳ Skipping org refresh - not authenticated')
      return
    }

    console.log('🔄 Refreshing organizations...')
    setIsLoading(true)
    setError(null)

    try {
      const authToken = token || getAuthToken()
      const orgs = await fetchOrganizations(authToken)
      setAvailableOrgs(orgs)
      console.log(`✅ Loaded ${orgs.length} accessible organizations`)

      // Check if we have a saved org ID
      const savedOrgId = localStorage.getItem(ORG_STORAGE_KEY)

      if (savedOrgId) {
        // Verify the saved org is still accessible
        const savedOrg = orgs.find(o => o.id === savedOrgId)
        if (savedOrg) {
          setCurrentOrg(savedOrg)
          console.log(`📍 Restored current org: ${savedOrg.name}`)
        } else {
          // Saved org is no longer accessible, clear it
          console.warn(`⚠️ Saved org ${savedOrgId} no longer accessible, selecting first available`)
          localStorage.removeItem(ORG_STORAGE_KEY)
          if (orgs.length > 0) {
            setCurrentOrg(orgs[0])
            localStorage.setItem(ORG_STORAGE_KEY, orgs[0].id)
            addToRecentOrgs(orgs[0].id)
          }
        }
      } else if (orgs.length > 0) {
        // No saved org, select first one
        setCurrentOrg(orgs[0])
        localStorage.setItem(ORG_STORAGE_KEY, orgs[0].id)
        addToRecentOrgs(orgs[0].id)
        console.log(`📍 Selected first org: ${orgs[0].name}`)
      } else {
        // No orgs available
        setCurrentOrg(null)
        console.log('ℹ️ No organizations available')
      }
    } catch (err: any) {
      console.error('❌ Failed to load organizations:', err)
      setError(err.message || 'Failed to load organizations')
    } finally {
      setIsLoading(false)
    }
  }, [isAuthenticated, authReady, token, addToRecentOrgs])

  /**
   * Switch to a different organization
   */
  const switchOrganization = useCallback(async (orgId: string) => {
    // Check if org is in available list
    let org = availableOrgs.find(o => o.id === orgId)

    if (!org) {
      // Try to fetch it (might be newly accessible)
      try {
        const authToken = token || getAuthToken()
        org = await fetchOrganization(orgId, authToken)
        // Add to available orgs
        setAvailableOrgs(prev => [...prev, org!])
      } catch (err: any) {
        console.error(`❌ Cannot switch to org ${orgId}:`, err)
        throw new Error('You do not have access to this organization')
      }
    }

    // Update state and storage
    setCurrentOrg(org)
    localStorage.setItem(ORG_STORAGE_KEY, orgId)
    addToRecentOrgs(orgId)
    console.log(`🔀 Switched to organization: ${org.name}`)
  }, [availableOrgs, token, addToRecentOrgs])

  // Load organizations when authentication is ready
  useEffect(() => {
    if (authReady && isAuthenticated) {
      refreshOrganizations()
    } else if (authReady && !isAuthenticated) {
      // User logged out, clear org context
      clearOrgContext()
      setIsLoading(false)
    }
  }, [authReady, isAuthenticated, refreshOrganizations, clearOrgContext])

  const contextValue: OrganizationContextType = {
    currentOrg,
    availableOrgs,
    isLoading,
    error,
    recentOrgIds,
    switchOrganization,
    refreshOrganizations,
    setCurrentOrgId,
    clearOrgContext
  }

  return (
    <OrganizationContext.Provider value={contextValue}>
      {children}
    </OrganizationContext.Provider>
  )
}

/**
 * Hook to access organization context
 */
export function useOrganization() {
  const context = useContext(OrganizationContext)
  if (!context) {
    throw new Error('useOrganization must be used within OrganizationProvider')
  }
  return context
}

/**
 * Hook to get current org ID (convenience)
 */
export function useCurrentOrgId(): string | null {
  const { currentOrg } = useOrganization()
  return currentOrg?.id || null
}
