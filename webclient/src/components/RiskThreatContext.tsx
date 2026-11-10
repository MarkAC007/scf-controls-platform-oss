import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { RiskCodesFile, RiskThreatMapping } from '../types'
import { getRiskAssessments } from '../data/apiClient'
import { useOrganization } from '../contexts/OrganizationContext'
import { useRiskCatalog, useThreatCatalog } from '../hooks/useRiskCatalog'
import AppLink from './AppLink'

interface Props {
  mapping?: RiskThreatMapping
}

interface CodeInfo {
  code: string
  category: string
  categoryName: string
  title: string
  description: string
  color: string
  /** The publisher's rating of this mapping on a 2026.3+ catalogue. */
  rating: 'likely' | 'possible' | null
}

function describe(code: string, catalog: RiskCodesFile, ratings?: Record<string, string> | null): CodeInfo | null {
  const entry = catalog.codes[code]
  if (!entry) return null
  const category = catalog.categories[entry.category]
  const rating = String(ratings?.[code] ?? '').toLowerCase()
  return {
    code,
    category: entry.category,
    categoryName: category?.name || entry.category,
    title: entry.title,
    description: entry.description,
    color: category?.color || '#6b7280',
    rating: rating === 'likely' || rating === 'possible' ? rating : null,
  }
}

/**
 * Likely-rated codes (and every code of an unrated catalogue) show open; the
 * merely Possible ones sit behind a count, since a 2026.3 control maps to a
 * median of thirteen risks and the page is about the ones that matter.
 */
function split(infos: CodeInfo[]): { open: CodeInfo[]; possible: CodeInfo[] } {
  return {
    open: infos.filter((i) => i.rating !== 'possible'),
    possible: infos.filter((i) => i.rating === 'possible'),
  }
}

/**
 * The popover describes the SCF catalogue definition of a risk code. Whether
 * this organisation holds its own record for that code is a separate fact, so
 * say which: link to the record when it exists, and say plainly when it does
 * not. Following the link never creates an assessment.
 */
function RiskRecordLink({ code }: { code: string }) {
  const { currentOrg } = useOrganization()
  const orgId = currentOrg?.id
  const { data, isLoading, isError } = useQuery({
    queryKey: ['risk-assessments', orgId],
    queryFn: () => getRiskAssessments(undefined, orgId),
    enabled: !!orgId,
    staleTime: 60_000,
  })
  if (!orgId || isLoading) {
    return <div className="popover-record popover-record-muted">Checking risk register…</div>
  }
  if (isError) {
    return <div className="popover-record popover-record-muted">Risk register could not be checked.</div>
  }
  if (!data?.some((a) => a.risk_code === code)) {
    return (
      <div className="popover-record popover-record-muted">
        Catalogue definition only — no {code} record in this organisation's risk register.
      </div>
    )
  }
  return (
    <div className="popover-record">
      <AppLink to={{ kind: 'risk', id: code }}>Open risk record</AppLink>
    </div>
  )
}

function CodeBadge({ info, isExpanded, onToggle, isRisk = false }: { info: CodeInfo; isExpanded: boolean; onToggle: () => void; isRisk?: boolean }) {
  return (
    <div className="risk-threat-badge-container">
      <button
        className={`risk-threat-badge ${isExpanded ? 'expanded' : ''}`}
        style={{ '--badge-color': info.color } as React.CSSProperties}
        onClick={onToggle}
        title={`${info.title}: ${info.description}`}
      >
        <span className="badge-code">{info.code}</span>
      </button>
      {isExpanded && (
        <div className="risk-threat-popover" style={{ '--badge-color': info.color } as React.CSSProperties}>
          <div className="popover-header">
            <span className="popover-code">{info.code}</span>
            <span className="popover-category">{info.categoryName}</span>
          </div>
          <div className="popover-title">{info.title}</div>
          <div className="popover-description">{info.description}</div>
          {isRisk && <RiskRecordLink code={info.code} />}
        </div>
      )}
    </div>
  )
}

export default function RiskThreatContext({ mapping }: Props) {
  const [expandedCode, setExpandedCode] = useState<string | null>(null)
  const [showPossible, setShowPossible] = useState<{ risks: boolean; threats: boolean }>({ risks: false, threats: false })
  const riskCatalog = useRiskCatalog()
  const threatCatalog = useThreatCatalog()

  if (!mapping) {
    return null
  }

  const risks = split(
    (mapping.risk_codes || [])
      .map((code) => describe(code, riskCatalog.catalog, mapping.risk_ratings))
      .filter((info): info is CodeInfo => info !== null),
  )
  const threats = split(
    (mapping.threat_codes || [])
      .map((code) => describe(code, threatCatalog.catalog, mapping.threat_ratings))
      .filter((info): info is CodeInfo => info !== null),
  )

  if (risks.open.length + risks.possible.length + threats.open.length + threats.possible.length === 0) {
    return null
  }

  const handleToggle = (code: string) => {
    setExpandedCode(expandedCode === code ? null : code)
  }

  const renderSection = (
    label: string,
    group: { open: CodeInfo[]; possible: CodeInfo[] },
    key: 'risks' | 'threats',
    isRisk: boolean,
  ) => {
    if (group.open.length + group.possible.length === 0) return null
    const shown = showPossible[key] ? [...group.open, ...group.possible] : group.open
    return (
      <div className="risk-threat-section">
        <div className="risk-threat-section-label">{label}</div>
        <div className="risk-threat-badges">
          {shown.map(info => (
            <CodeBadge
              key={info.code}
              info={info}
              isExpanded={expandedCode === info.code}
              onToggle={() => handleToggle(info.code)}
              isRisk={isRisk}
            />
          ))}
          {group.possible.length > 0 && (
            <button
              type="button"
              className="risk-threat-possible-toggle"
              aria-expanded={showPossible[key]}
              onClick={() => setShowPossible((s) => ({ ...s, [key]: !s[key] }))}
              title="Codes the SCF rates only Possible for this control"
            >
              {showPossible[key] ? 'hide possible' : `+${group.possible.length} possible`}
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="detail-section-container">
      <div className="container-header">
        <span className="container-icon">⚠️</span>
        <span className="container-title">Risk & Threat Context</span>
      </div>
      <div className="container-content">
        {renderSection('Risk Codes:', risks, 'risks', true)}
        {renderSection('Threat Codes:', threats, 'threats', false)}
        <div className="risk-threat-hint">
          Click a badge to see details
        </div>
      </div>
    </div>
  )
}
