import { useState } from 'react'
import type { RiskThreatMapping } from '../types'
import riskCodesData from '../data/risk_codes.json'
import threatCodesData from '../data/threat_codes.json'

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
}

function getRiskInfo(code: string): CodeInfo | null {
  const riskCode = riskCodesData.codes[code as keyof typeof riskCodesData.codes]
  if (!riskCode) return null

  const category = riskCodesData.categories[riskCode.category as keyof typeof riskCodesData.categories]
  return {
    code,
    category: riskCode.category,
    categoryName: category?.name || riskCode.category,
    title: riskCode.title,
    description: riskCode.description,
    color: category?.color || '#6b7280'
  }
}

function getThreatInfo(code: string): CodeInfo | null {
  const threatCode = threatCodesData.codes[code as keyof typeof threatCodesData.codes]
  if (!threatCode) return null

  const category = threatCodesData.categories[threatCode.category as keyof typeof threatCodesData.categories]
  return {
    code,
    category: threatCode.category,
    categoryName: category?.name || threatCode.category,
    title: threatCode.title,
    description: threatCode.description,
    color: category?.color || '#6b7280'
  }
}

function CodeBadge({ info, isExpanded, onToggle }: { info: CodeInfo; isExpanded: boolean; onToggle: () => void }) {
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
        </div>
      )}
    </div>
  )
}

export default function RiskThreatContext({ mapping }: Props) {
  const [expandedCode, setExpandedCode] = useState<string | null>(null)

  if (!mapping) {
    return null
  }

  const riskCodes = (mapping.risk_codes || [])
    .map(getRiskInfo)
    .filter((info): info is CodeInfo => info !== null)

  const threatCodes = (mapping.threat_codes || [])
    .map(getThreatInfo)
    .filter((info): info is CodeInfo => info !== null)

  if (riskCodes.length === 0 && threatCodes.length === 0) {
    return null
  }

  const handleToggle = (code: string) => {
    setExpandedCode(expandedCode === code ? null : code)
  }

  return (
    <div className="detail-section-container">
      <div className="container-header">
        <span className="container-icon">⚠️</span>
        <span className="container-title">Risk & Threat Context</span>
      </div>
      <div className="container-content">
        {riskCodes.length > 0 && (
          <div className="risk-threat-section">
            <div className="risk-threat-section-label">Risk Codes:</div>
            <div className="risk-threat-badges">
              {riskCodes.map(info => (
                <CodeBadge
                  key={info.code}
                  info={info}
                  isExpanded={expandedCode === info.code}
                  onToggle={() => handleToggle(info.code)}
                />
              ))}
            </div>
          </div>
        )}
        {threatCodes.length > 0 && (
          <div className="risk-threat-section">
            <div className="risk-threat-section-label">Threat Codes:</div>
            <div className="risk-threat-badges">
              {threatCodes.map(info => (
                <CodeBadge
                  key={info.code}
                  info={info}
                  isExpanded={expandedCode === info.code}
                  onToggle={() => handleToggle(info.code)}
                />
              ))}
            </div>
          </div>
        )}
        <div className="risk-threat-hint">
          Click a badge to see details
        </div>
      </div>
    </div>
  )
}
