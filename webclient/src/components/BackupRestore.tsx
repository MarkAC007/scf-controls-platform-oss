import { useState, useRef } from 'react'
import { apiClient } from '../data/apiClient'

interface BackupMetadata {
  version: string
  created_at: string
  created_by?: string
  accessible_organizations?: string[]
  table_counts: Record<string, number>
}

interface BackupData {
  metadata: BackupMetadata
  data: Record<string, unknown[]>
}

// Server-side preview (confirm_clear=false). The counts the server returns are
// the only trustworthy ones: the file says what will be written, but only the
// database knows what is currently in scope and what a restore would remove.
interface RestorePreview {
  status: string
  backup_metadata: BackupMetadata
  records_to_restore: Record<string, number>
  target_organizations?: string[]
  existing_counts?: Record<string, number>
  would_delete_counts?: Record<string, number>
}

interface RestoreResult {
  status: string
  message: string
  restored_counts?: Record<string, number>
  upserted_counts?: Record<string, number>
  deleted_counts?: Record<string, number>
}

interface BackupRestoreProps {
  organizationId: string
}

export default function BackupRestore({ organizationId }: BackupRestoreProps) {
  const [backupLoading, setBackupLoading] = useState(false)
  const [restoreLoading, setRestoreLoading] = useState(false)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [message, setMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null)
  const [restorePreview, setRestorePreview] = useState<BackupMetadata | null>(null)
  const [serverPreview, setServerPreview] = useState<RestorePreview | null>(null)
  const [pendingRestoreData, setPendingRestoreData] = useState<BackupData | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleBackup = async () => {
    setBackupLoading(true)
    setMessage(null)
    try {
      const backupData = await apiClient.get<BackupData>('/database/backup')

      const blob = new Blob([JSON.stringify(backupData, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
      const filename = `scf-backup-${timestamp}.json`

      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)

      const totalRecords = Object.values(backupData.metadata.table_counts).reduce((a, b) => a + b, 0)
      setMessage({
        type: 'success',
        text: `Backup downloaded: ${filename} (${totalRecords} records)`
      })
    } catch (err) {
      setMessage({
        type: 'error',
        text: `Backup failed: ${err instanceof Error ? err.message : 'Unknown error'}`
      })
    } finally {
      setBackupLoading(false)
    }
  }

  const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    setMessage(null)
    setRestorePreview(null)
    setServerPreview(null)
    setPendingRestoreData(null)

    try {
      const text = await file.text()
      const backupData = JSON.parse(text) as BackupData

      if (!backupData.metadata || !backupData.data) {
        throw new Error('Invalid backup file: missing metadata or data sections')
      }

      if (!['1.0', '1.1'].includes(backupData.metadata.version)) {
        throw new Error(`Unsupported backup version: ${backupData.metadata.version}. Expected: 1.0 or 1.1`)
      }

      setRestorePreview(backupData.metadata)
      setPendingRestoreData(backupData)

      // Ask the server what this file would actually do. confirm_clear=false is
      // non-destructive; it resolves the target organisations and reports how
      // many in-scope rows would be removed, which the file alone cannot say.
      setPreviewLoading(true)
      try {
        const preview = await apiClient.post<RestorePreview>('/database/restore', {
          backup_data: backupData,
          confirm_clear: false
        })
        setServerPreview(preview)
      } catch (previewErr) {
        // A failed preview is not a failed file — surface it but still let the
        // operator decide, since older backends do not return the extra fields.
        setMessage({
          type: 'error',
          text: `Could not preview restore: ${previewErr instanceof Error ? previewErr.message : 'Unknown error'}`
        })
      } finally {
        setPreviewLoading(false)
      }
    } catch (err) {
      setMessage({
        type: 'error',
        text: `Invalid backup file: ${err instanceof Error ? err.message : 'Unknown error'}`
      })
    }

    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const handleConfirmRestore = async () => {
    if (!pendingRestoreData) return

    setRestoreLoading(true)
    setMessage(null)

    try {
      const result = await apiClient.post<RestoreResult>('/database/restore', {
        backup_data: pendingRestoreData,
        confirm_clear: true
      })

      const sum = (counts?: Record<string, number>) =>
        counts ? Object.values(counts).reduce((a, b) => a + b, 0) : 0

      const totalWritten = sum(result.upserted_counts ?? result.restored_counts)
      const totalDeleted = sum(result.deleted_counts)
      const removedNote = totalDeleted > 0 ? `, ${totalDeleted} stale records removed` : ''

      setMessage({
        type: 'success',
        text: `Restore complete: ${totalWritten} records written${removedNote}. Reload the page to see updated data.`
      })
      setRestorePreview(null)
      setServerPreview(null)
      setPendingRestoreData(null)
    } catch (err) {
      setMessage({
        type: 'error',
        text: `Restore failed: ${err instanceof Error ? err.message : 'Unknown error'}`
      })
    } finally {
      setRestoreLoading(false)
    }
  }

  const handleCancelRestore = () => {
    setRestorePreview(null)
    setServerPreview(null)
    setPendingRestoreData(null)
  }

  // Organisations this file will replace. The server's resolved list wins; the
  // file's own metadata is the fallback for backups taken before the server
  // started echoing it back.
  const targetOrgs =
    serverPreview?.target_organizations ?? restorePreview?.accessible_organizations ?? []
  const currentOrgIncluded = targetOrgs.length === 0 || targetOrgs.includes(organizationId)
  const totalToDelete = serverPreview?.would_delete_counts
    ? Object.values(serverPreview.would_delete_counts).reduce((a, b) => a + b, 0)
    : null

  return (
    <div className="settings-group backup-restore-section surface-bench">
      <h2 className="bench-header"><span className="container-title">Your Backups</span></h2>
      <p style={{ color: 'var(--muted)', fontSize: '14px', marginBottom: '16px' }}>
        Download a complete backup of your organization's data or restore from a previous backup.
      </p>

      {message && (
        <div
          style={{
            padding: '12px 16px',
            borderRadius: '8px',
            marginBottom: '16px',
            background: message.type === 'success' ? 'var(--success-bg)' : 'rgba(239, 68, 68, 0.1)',
            border: `1px solid ${message.type === 'success' ? 'var(--success)' : 'var(--destructive)'}`,
            color: message.type === 'success' ? 'var(--success)' : 'var(--destructive)',
            fontSize: '13px',
          }}
        >
          {message.type === 'success' ? '✓' : '✕'} {message.text}
        </div>
      )}

      {restorePreview && (
        <div
          style={{
            padding: '16px',
            borderRadius: '8px',
            marginBottom: '16px',
            background: 'var(--secondary)',
            border: '1px solid var(--border)',
          }}
        >
          <h3 style={{ margin: '0 0 8px', fontSize: '15px', color: 'var(--text)' }}>Restore Preview</h3>
          <p style={{ color: 'var(--destructive)', fontSize: '13px', marginBottom: '12px', fontWeight: 600 }}>
            {targetOrgs.length > 0
              ? `This will replace data for ${targetOrgs.length} organisation${targetOrgs.length === 1 ? '' : 's'}. Other organisations are not affected.`
              : 'This will replace the data covered by this backup.'}
          </p>

          {!currentOrgIncluded && (
            <p style={{ color: 'var(--destructive)', fontSize: '13px', marginBottom: '12px' }}>
              ⚠ This backup does not contain the organisation you are currently viewing.
            </p>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '12px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', color: 'var(--muted)' }}>
              <span>Backup created:</span>
              <span style={{ color: 'var(--text)' }}>{new Date(restorePreview.created_at).toLocaleString()}</span>
            </div>
            {targetOrgs.length > 0 && (
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', color: 'var(--muted)', gap: '16px' }}>
                <span>Organisations replaced:</span>
                <span style={{ color: 'var(--text)', textAlign: 'right', wordBreak: 'break-all' }}>
                  {targetOrgs.join(', ')}
                </span>
              </div>
            )}
            {previewLoading && (
              <div style={{ fontSize: '13px', color: 'var(--muted)' }}>Checking what this would change…</div>
            )}
            {totalToDelete !== null && (
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', color: 'var(--muted)' }}>
                <span>Existing records removed:</span>
                <span style={{ color: totalToDelete > 0 ? 'var(--destructive)' : 'var(--text)', fontWeight: 600 }}>
                  {totalToDelete}
                </span>
              </div>
            )}
            {restorePreview.created_by && (
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', color: 'var(--muted)' }}>
                <span>Created by:</span>
                <span style={{ color: 'var(--text)' }}>{restorePreview.created_by}</span>
              </div>
            )}
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', color: 'var(--muted)' }}>
              <span>Total records:</span>
              <span style={{ color: 'var(--text)', fontWeight: 600 }}>
                {Object.values(restorePreview.table_counts).reduce((a, b) => a + b, 0)}
              </span>
            </div>
          </div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr auto',
              gap: '4px 16px',
              fontSize: '12px',
              color: 'var(--muted)',
              padding: '8px 12px',
              background: 'var(--card)',
              borderRadius: '6px',
              marginBottom: '12px',
            }}
          >
            {Object.entries(restorePreview.table_counts).map(([table, count]) => (
              <div key={table} style={{ display: 'contents' }}>
                <span>{table.replace(/_/g, ' ')}</span>
                <span style={{ textAlign: 'right', fontWeight: 500, color: 'var(--text)' }}>{count}</span>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
            <button
              onClick={handleCancelRestore}
              className="btn btn-secondary"
              disabled={restoreLoading}
            >
              Cancel
            </button>
            <button
              onClick={handleConfirmRestore}
              className="btn btn-danger"
              disabled={restoreLoading || previewLoading}
            >
              {restoreLoading ? 'Restoring...' : 'Confirm Restore'}
            </button>
          </div>
        </div>
      )}

      {!restorePreview && (
        <div style={{ display: 'flex', gap: '12px' }}>
          <button
            onClick={handleBackup}
            className="btn btn-primary"
            disabled={backupLoading || restoreLoading}
          >
            {backupLoading ? 'Creating Backup...' : 'Download Backup'}
          </button>
          <button
            onClick={() => fileInputRef.current?.click()}
            className="btn btn-secondary"
            disabled={backupLoading || restoreLoading}
          >
            Restore from Backup
          </button>
          <input
            type="file"
            ref={fileInputRef}
            onChange={handleFileSelect}
            accept=".json"
            style={{ display: 'none' }}
          />
        </div>
      )}
    </div>
  )
}
