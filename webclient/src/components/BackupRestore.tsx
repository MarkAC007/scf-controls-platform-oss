import { useState, useRef } from 'react'
import { apiClient } from '../data/apiClient'

interface BackupMetadata {
  version: string
  created_at: string
  created_by?: string
  table_counts: Record<string, number>
}

interface BackupData {
  metadata: BackupMetadata
  data: Record<string, unknown[]>
}

interface RestoreResult {
  status: string
  message: string
  restored_counts?: Record<string, number>
}

interface BackupRestoreProps {
  organizationId: string
}

export default function BackupRestore({ organizationId }: BackupRestoreProps) {
  const [backupLoading, setBackupLoading] = useState(false)
  const [restoreLoading, setRestoreLoading] = useState(false)
  const [message, setMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null)
  const [restorePreview, setRestorePreview] = useState<BackupMetadata | null>(null)
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

      const totalRestored = result.restored_counts
        ? Object.values(result.restored_counts).reduce((a, b) => a + b, 0)
        : 0

      setMessage({
        type: 'success',
        text: `Database restored successfully! ${totalRestored} records imported. Reload the page to see updated data.`
      })
      setRestorePreview(null)
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
    setPendingRestoreData(null)
  }

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
            This will replace ALL existing data with the backup contents.
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '12px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', color: 'var(--muted)' }}>
              <span>Backup created:</span>
              <span style={{ color: 'var(--text)' }}>{new Date(restorePreview.created_at).toLocaleString()}</span>
            </div>
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
              disabled={restoreLoading}
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
