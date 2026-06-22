import { useEffect, useMemo, useState } from 'react'
import { getEvidenceHealth, type EvidenceHealthResponse } from '../../data/apiClient'
import { HealthCard } from '../evidence/EvidenceDashboardTab'

interface ThemeEvidenceCardsProps {
  organizationId: string
  themeScfIds: string[]
  onNavigateToEvidence?: (evidenceId: string) => void
}

export default function ThemeEvidenceCards({
  organizationId,
  themeScfIds,
  onNavigateToEvidence,
}: ThemeEvidenceCardsProps) {
  const [data, setData] = useState<EvidenceHealthResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getEvidenceHealth(organizationId)
      .then((result) => {
        if (!cancelled) setData(result)
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message || 'Failed to load evidence')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [organizationId])

  const filteredItems = useMemo(() => {
    if (!data || themeScfIds.length === 0) return []
    const scopeSet = new Set(themeScfIds)
    return data.items.filter((item) =>
      item.control_mappings.some((id) => scopeSet.has(id))
    )
  }, [data, themeScfIds])

  if (loading) {
    return (
      <div className="cp-detail-loading">
        <div className="loading-spinner" />
      </div>
    )
  }

  if (error) {
    return <p className="cp-detail-empty">Failed to load evidence records: {error}</p>
  }

  if (filteredItems.length === 0) {
    return (
      <p className="cp-detail-empty">
        No tracked evidence records mapped to this theme's controls.
      </p>
    )
  }

  return (
    <div className="cp-detail-evidence-cards">
      <h3 className="cp-detail-section-title">
        Evidence Records ({filteredItems.length})
      </h3>
      <div className="ehd-grid">
        {filteredItems.map((item) => (
          <HealthCard
            key={item.evidence_id}
            item={item}
            onNavigateToEvidence={onNavigateToEvidence}
          />
        ))}
      </div>
    </div>
  )
}
