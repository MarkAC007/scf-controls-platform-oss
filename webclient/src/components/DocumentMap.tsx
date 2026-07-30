import { useMemo, useState } from 'react'
import { useDocumentMap } from '../hooks/useDocumentMap'
import DomainCard from './document-map/DomainCard'
import DomainDetailPanel from './document-map/DomainDetailPanel'
import MapLegend from './document-map/MapLegend'
import OrphanRail from './document-map/OrphanRail'

/**
 * Document Map — where an organisation's control documents sit against the
 * SCF catalogue, and where nothing sits at all.
 *
 * The grid holds catalogue order and is never sorted by status: the whole
 * value of returning to this screen over a multi-quarter adoption is that a
 * domain is always in the same place. Absence is a first-class object here —
 * a gap gets a tile of its own rather than being whitespace.
 */

interface DocumentMapProps {
  organizationId: string
  /** Navigate to the Control Documents workspace, where uploads and reviews happen. */
  onOpenDocuments: () => void
}

export default function DocumentMap({ organizationId, onOpenDocuments }: DocumentMapProps) {
  const { data, isLoading, error } = useDocumentMap(organizationId)
  const [selectedDomain, setSelectedDomain] = useState<string | null>(null)

  const domains = useMemo(
    () => [...(data?.domains ?? [])].sort((a, b) => a.display_order - b.display_order),
    [data]
  )

  const summary = data?.coverage_summary
  const inScope = summary ? summary.covered + summary.claimed + summary.gap : 0
  const withDocumentation = summary ? summary.covered + summary.claimed : 0
  const isDayOne = !!summary && summary.documents_total === 0

  const mappingTotals = useMemo(() => {
    let accepted = 0
    let proposed = 0
    for (const d of domains) {
      accepted += d.totals.controls_with_accepted_mapping
      proposed += d.totals.controls_with_proposed_mapping
    }
    return { accepted, proposed }
  }, [domains])

  const selectedDomainData = useMemo(
    () => domains.find((d) => d.domain === selectedDomain) ?? null,
    [domains, selectedDomain]
  )

  return (
    <div className="dm-container">
      <header className="dm-header">
        <nav className="page-breadcrumb">
          <span>Knowledge Base</span>
          <span className="breadcrumb-separator">&rsaquo;</span>
          <span className="breadcrumb-active">Document Map</span>
        </nav>
        <h1 className="page-title">Document Map</h1>

        {summary && (
          <p className="dm-summary">
            {isDayOne ? (
              <>
                <strong>0 of {inScope}</strong> in-scope domains have documentation. Your framework
                scope is set — the map is waiting for its first document.
              </>
            ) : (
              <>
                <strong>
                  {withDocumentation} of {inScope}
                </strong>{' '}
                in-scope domains have documentation · <strong>{summary.covered}</strong> confirmed
                {summary.documents_orphaned > 0 && (
                  <>
                    {' '}
                    · <strong>{summary.documents_orphaned}</strong> document
                    {summary.documents_orphaned === 1 ? '' : 's'} unmapped
                  </>
                )}
              </>
            )}
          </p>
        )}

        {isDayOne && (
          <div className="dm-dayone">
            <div>
              <h3>No documents yet — start with one policy</h3>
              <p>
                Upload any policy or procedure you already have. It will appear on the map shortly
                with a suggested placement against the domains it seems to cover, and you confirm
                every placement. Most organisations start with their information security policy and
                work outward one domain at a time.
              </p>
            </div>
            <button type="button" className="dm-btn-primary" onClick={onOpenDocuments}>
              Upload your first document
            </button>
          </div>
        )}

        {summary && (
          <div className="kpi-row dm-kpi-row">
            <div className="kpi-card dm-kpi">
              <div className="kpi-card-header">
                <span className="kpi-label">Documents on the map</span>
              </div>
              <div className="kpi-value">{summary.documents_total}</div>
              <span className="dm-kpi-sub">
                {isDayOne
                  ? 'nothing uploaded yet'
                  : summary.documents_orphaned > 0
                    ? `${summary.documents_orphaned} not yet placed`
                    : 'all placed on the map'}
              </span>
            </div>
            {/*
              Confirmed domains only. The summary sentence above carries the
              sanctioned dual phrasing with its qualifier attached; a KPI is
              read — and screenshotted — on its own, so the number under the
              record word has to be the one an export would print.
            */}
            <div className="kpi-card dm-kpi">
              <div className="kpi-card-header">
                <span className="kpi-label">Domains covered</span>
              </div>
              <div className="kpi-value">
                {summary.covered}
                <span className="dm-kpi-denominator">/{inScope}</span>
              </div>
              <span className="dm-kpi-sub">
                {summary.claimed > 0
                  ? `${summary.claimed} more suggested · ${summary.gap} gap${summary.gap === 1 ? '' : 's'} remaining`
                  : `${summary.gap} gap${summary.gap === 1 ? '' : 's'} remaining`}
              </span>
            </div>
            <div className="kpi-card dm-kpi">
              <div className="kpi-card-header">
                <span className="kpi-label">Confirmed mappings</span>
              </div>
              <div className="kpi-value">{mappingTotals.accepted}</div>
              <span className="dm-kpi-sub">accepted by a person</span>
            </div>
            <div className="kpi-card dm-kpi">
              <div className="kpi-card-header">
                <span className="kpi-label">Suggested mappings</span>
              </div>
              <div className="kpi-value">{mappingTotals.proposed}</div>
              <span className="dm-kpi-sub">awaiting your review</span>
            </div>
          </div>
        )}
      </header>

      {isLoading ? (
        <div className="dm-grid" aria-busy="true">
          {Array.from({ length: 12 }).map((_, i) => (
            <div key={i} className="dm-tile dm-tile-skeleton">
              <span className="dm-skeleton-line dm-skeleton-code" />
              <span className="dm-skeleton-line dm-skeleton-name" />
              <span className="dm-skeleton-line dm-skeleton-block" />
            </div>
          ))}
        </div>
      ) : error ? (
        <div className="dm-error">
          <p>Failed to load the document map: {(error as Error).message}</p>
        </div>
      ) : domains.length === 0 ? (
        <div className="dm-empty">
          <h3>No framework scope yet</h3>
          <p>
            Scope controls in the Control Scoping view and the map will show which domains your
            documents need to cover.
          </p>
        </div>
      ) : (
        <>
          <MapLegend />
          <div className="dm-layout">
            <main>
              <div className="dm-section-head">
                <span className="dm-kicker">SCF Reference · {domains.length} domains</span>
                <span className="dm-scf-tag">SCF catalog</span>
              </div>
              <div className="dm-grid">
                {domains.map((domain) => (
                  <DomainCard key={domain.domain} domain={domain} onSelect={setSelectedDomain} />
                ))}
              </div>
            </main>
            <OrphanRail
              orphans={data?.orphan_documents ?? []}
              awaitingClassification={summary?.documents_awaiting_classification ?? 0}
              isDayOne={isDayOne}
              onOpenDocuments={onOpenDocuments}
            />
          </div>
        </>
      )}

      <DomainDetailPanel
        domain={selectedDomainData}
        onClose={() => setSelectedDomain(null)}
        onOpenDocuments={onOpenDocuments}
      />
    </div>
  )
}
