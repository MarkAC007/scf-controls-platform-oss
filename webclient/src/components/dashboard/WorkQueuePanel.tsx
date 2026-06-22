import { useWorkQueue } from '../../hooks/useWorkQueue'
import type {
  OverdueEvidenceItem,
  BlockingControlItem,
  StaleCollectionItem,
} from '../../data/apiClient'

interface WorkQueuePanelProps {
  orgId?: string
  onNavigateToEvidence?: (evidenceId: string) => void
  onNavigateToControl?: (scfId: string) => void
}

export default function WorkQueuePanel({
  orgId,
  onNavigateToEvidence,
  onNavigateToControl,
}: WorkQueuePanelProps) {
  const { data, isLoading } = useWorkQueue(orgId)

  const totalItems = data?.total_items ?? 0

  if (isLoading) {
    return (
      <div className="work-queue-panel">
        <div className="wq-header">
          <h3>Work Queue</h3>
        </div>
        <div className="wq-loading">Loading work queue...</div>
      </div>
    )
  }

  const hasItems =
    (data?.overdue_evidence?.length ?? 0) > 0 ||
    (data?.blocking_controls?.length ?? 0) > 0 ||
    (data?.stale_collections?.length ?? 0) > 0

  return (
    <div className="work-queue-panel">
      <div className="wq-header">
        <h3>Work Queue</h3>
        <span className={`wq-badge${totalItems === 0 ? ' wq-badge-zero' : ''}`}>
          {totalItems}
        </span>
      </div>
      <div className="wq-body">
        {!hasItems ? (
          <div className="wq-empty">
            <span className="wq-empty-icon">&#10003;</span>
            <span>No items requiring attention</span>
          </div>
        ) : (
          <>
            {(data?.overdue_evidence?.length ?? 0) > 0 && (
              <div className="wq-section">
                <div className="wq-section-title wq-overdue">
                  Overdue Evidence
                  <span>({data!.overdue_evidence.length})</span>
                </div>
                {data!.overdue_evidence.map((item: OverdueEvidenceItem) => (
                  <div
                    key={item.task_id}
                    className="wq-item"
                    onClick={() => onNavigateToEvidence?.(item.evidence_id)}
                  >
                    <div className="wq-item-left">
                      <span className="wq-item-id">{item.evidence_id}</span>
                      {item.title && (
                        <span className="wq-item-title">{item.title}</span>
                      )}
                    </div>
                    <span className="wq-item-days wq-days-overdue">
                      {item.days_overdue}d overdue
                    </span>
                  </div>
                ))}
              </div>
            )}

            {(data?.blocking_controls?.length ?? 0) > 0 && (
              <div className="wq-section">
                <div className="wq-section-title wq-blocking">
                  Blocking Controls
                  <span>({data!.blocking_controls.length})</span>
                </div>
                {data!.blocking_controls.map((item: BlockingControlItem) => (
                  <div
                    key={item.scf_id}
                    className="wq-item"
                    onClick={() => onNavigateToControl?.(item.scf_id)}
                  >
                    <div className="wq-item-left">
                      <span className="wq-item-id">{item.scf_id}</span>
                      <span className="wq-item-title">
                        {item.implementation_status.replace(/_/g, ' ')}
                      </span>
                    </div>
                    <span className="wq-item-days wq-days-stale">
                      {item.days_stale}d stale
                    </span>
                  </div>
                ))}
              </div>
            )}

            {(data?.stale_collections?.length ?? 0) > 0 && (
              <div className="wq-section">
                <div className="wq-section-title wq-stale">
                  Stale Collections
                  <span>({data!.stale_collections.length})</span>
                </div>
                {data!.stale_collections.map((item: StaleCollectionItem) => (
                  <div
                    key={item.evidence_id}
                    className="wq-item"
                    onClick={() => onNavigateToEvidence?.(item.evidence_id)}
                  >
                    <div className="wq-item-left">
                      <span className="wq-item-id">{item.evidence_id}</span>
                      <span className="wq-item-title">
                        Collection due {item.next_collection_date}
                      </span>
                    </div>
                    <span className="wq-item-days wq-days-stale">
                      {item.days_overdue}d overdue
                    </span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
