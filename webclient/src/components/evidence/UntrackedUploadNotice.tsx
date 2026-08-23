/**
 * Says what an upload to an untracked evidence item will and will not do.
 *
 * The review filed this as "upload is gated behind tracking". Read literally it
 * is not: neither `EvidenceFileUpload` nor the two upload endpoints
 * (`evidence_files.get_upload_url`, `confirm_upload`) look at `is_tracked`, and
 * the file lands in S3 with a record against it either way.
 *
 * What is true is worse than a gate, because a gate at least announces itself.
 * Every surface the upload was *for* filters on `is_tracked == True`:
 *
 *   - evidence health / freshness (`api/evidence_health.py`)
 *   - the dashboard work queue (`api/dashboard.py`)
 *   - collection-task generation (`services/task_generator.py`)
 *   - collection maturity, which returns L0 outright (`services/maturity.py`)
 *
 * So the file uploads, the panel says it uploaded, and nothing anywhere counts
 * it. The person has done the work and the product records none of it, with no
 * error to explain the absence — the epic's first thesis in miniature.
 *
 * This is deliberately a notice and not a gate. Blocking the upload would be a
 * second wrong answer: capturing evidence before deciding how it will be
 * collected is a legitimate order to work in, and a product that refuses it is
 * the "blocked at the point of action" complaint this component exists to end.
 * State the consequence, offer the one click that removes it, and let the
 * upload proceed regardless (#789).
 */
interface UntrackedUploadNoticeProps {
  onStartTracking: () => void
}

export function UntrackedUploadNotice({ onStartTracking }: UntrackedUploadNoticeProps) {
  return (
    <div className="untracked-upload-notice" role="status">
      <div className="untracked-upload-notice-body">
        <strong className="untracked-upload-notice-title">
          This evidence item is not being tracked
        </strong>
        <p className="untracked-upload-notice-text">
          You can upload files now, but until collection is active they will not
          appear in evidence health, will not count towards freshness, will not
          generate collection tasks, and this item stays at maturity L0.
        </p>
      </div>
      <button
        type="button"
        className="btn btn-primary btn-sm untracked-upload-notice-action"
        onClick={onStartTracking}
      >
        Start tracking this item
      </button>
    </div>
  )
}

export default UntrackedUploadNotice
