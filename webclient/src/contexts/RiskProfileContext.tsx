/**
 * RiskProfileContext - Provides organisation risk profile and thresholds
 *
 * Fetches the risk profile on org change and exposes:
 * - riskProfile: the full profile object
 * - riskThresholds: convenience { lowMax, mediumMax, highMax }
 * - isLoading: loading state
 * - updateProfile: save changes
 * - resetProfile: reset to defaults
 */
import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react'
import { useOrganization } from './OrganizationContext'
import { getRiskProfile, updateRiskProfile, resetRiskProfile } from '../data/apiClient'
import type { RiskProfile, RiskProfileUpdate, RiskThresholds } from '../types'
import { DEFAULT_RISK_THRESHOLDS } from '../types'

interface RiskProfileContextType {
  riskProfile: RiskProfile | null
  riskThresholds: RiskThresholds
  isLoading: boolean
  error: string | null
  updateProfile: (data: RiskProfileUpdate) => Promise<void>
  resetProfile: () => Promise<void>
  refreshProfile: () => Promise<void>
}

const RiskProfileContext = createContext<RiskProfileContextType | undefined>(undefined)

export function RiskProfileProvider({ children }: { children: ReactNode }) {
  const { currentOrg } = useOrganization()
  const [riskProfile, setRiskProfile] = useState<RiskProfile | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const riskThresholds: RiskThresholds = riskProfile
    ? { lowMax: riskProfile.low_max, mediumMax: riskProfile.medium_max, highMax: riskProfile.high_max }
    : DEFAULT_RISK_THRESHOLDS

  const fetchProfile = useCallback(async () => {
    if (!currentOrg) return
    setIsLoading(true)
    setError(null)
    try {
      const profile = await getRiskProfile(currentOrg.id)
      setRiskProfile(profile)
    } catch (err: any) {
      console.error('Failed to load risk profile:', err)
      setError(err.message || 'Failed to load risk profile')
    } finally {
      setIsLoading(false)
    }
  }, [currentOrg])

  // Fetch profile when org changes
  useEffect(() => {
    fetchProfile()
  }, [fetchProfile])

  const handleUpdate = useCallback(async (data: RiskProfileUpdate) => {
    if (!currentOrg) return
    const updated = await updateRiskProfile(data, currentOrg.id)
    setRiskProfile(updated)
  }, [currentOrg])

  const handleReset = useCallback(async () => {
    if (!currentOrg) return
    const updated = await resetRiskProfile(currentOrg.id)
    setRiskProfile(updated)
  }, [currentOrg])

  return (
    <RiskProfileContext.Provider value={{
      riskProfile,
      riskThresholds,
      isLoading,
      error,
      updateProfile: handleUpdate,
      resetProfile: handleReset,
      refreshProfile: fetchProfile,
    }}>
      {children}
    </RiskProfileContext.Provider>
  )
}

export function useRiskProfile() {
  const context = useContext(RiskProfileContext)
  if (context === undefined) {
    throw new Error('useRiskProfile must be used within a RiskProfileProvider')
  }
  return context
}
