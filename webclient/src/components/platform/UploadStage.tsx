/**
 * UploadStage — pick a new SCF .xlsx and start an upgrade run
 * (multipart POST /api/admin/catalog/upgrade → 202, staging enqueued).
 */
import { useRef, useState } from 'react'

interface UploadStageProps {
  /** Disabled while another run is mid-flight (staging/applying). */
  disabled?: boolean
  onUpload: (file: File) => Promise<void>
}

export default function UploadStage({ disabled = false, onUpload }: UploadStageProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)

  const handleUpload = async () => {
    if (!file) return
    setUploading(true)
    try {
      await onUpload(file)
      setFile(null)
      if (inputRef.current) inputRef.current.value = ''
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="surface-bench" style={{ padding: '1.25rem 1.5rem' }}>
      <h3 className="bench-header">
        <span className="container-title">Upgrade catalog</span>
      </h3>
      <p style={{ color: 'var(--muted)', marginBottom: '1rem' }}>
        Upload a newer SCF workbook (.xlsx). Staging computes a full diff against the live
        catalog — nothing changes until you review the diff and confirm the apply.
      </p>
      <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          ref={inputRef}
          type="file"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          aria-label="SCF workbook file"
          disabled={disabled || uploading}
          onChange={e => setFile(e.target.files?.[0] ?? null)}
        />
        <button
          className="btn btn-primary"
          disabled={disabled || uploading || !file}
          onClick={handleUpload}
        >
          {uploading ? 'Uploading…' : 'Upload & stage'}
        </button>
      </div>
      {disabled && (
        <p style={{ color: 'var(--muted)', fontSize: '0.85rem', marginTop: '0.5rem' }}>
          Another run is in progress — wait for it to finish before starting a new upgrade.
        </p>
      )}
    </div>
  )
}
