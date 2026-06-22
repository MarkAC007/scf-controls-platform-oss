import { useState, useEffect, useRef } from 'react'
import { apiClient } from '../data/apiClient'

interface DatabaseStatsProps {
  isOpen: boolean
  onClose: () => void
}

interface VersionInfo {
  platform: {
    version: string
    api_version: string
    git_commit: string | null
  }
  catalog: {
    version: string
    controls_count: number
    evidence_count: number
    interface_count: number
  }
  environment: string
}

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

interface DatabaseHealth {
  status: string
  database: {
    organizations: number
    scoped_controls: number
    evidence_tracking: number
    users: number
    organization_members: number
    assignments: number
    comments: number
    evidence_collection_tasks: number
    notifications: number
    total_records: number
  }
  statistics: {
    selected_controls: number
    implemented_controls: number
    at_risk_controls: number
    tracked_evidence: number
    active_users: number
    pending_tasks: number
    completed_tasks: number
    overdue_tasks: number
    unread_notifications: number
    total_comments: number
  }
  by_status: Record<string, number>
  by_maturity: Record<string, number>
  tasks_by_status: Record<string, number>
  tasks_by_type: Record<string, number>
  recent_updates: {
    last_control_update: string | null
    last_evidence_update: string | null
    last_comment_update: string | null
    last_task_update: string | null
  }
}

export default function DatabaseStats({ isOpen, onClose }: DatabaseStatsProps) {
  const [stats, setStats] = useState<DatabaseHealth | null>(null)
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Backup/Restore state
  const [backupLoading, setBackupLoading] = useState(false)
  const [restoreLoading, setRestoreLoading] = useState(false)
  const [backupRestoreMessage, setBackupRestoreMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null)
  const [restorePreview, setRestorePreview] = useState<BackupMetadata | null>(null)
  const [pendingRestoreData, setPendingRestoreData] = useState<BackupData | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (isOpen) {
      fetchStats()
      // Clear any previous messages when reopening
      setBackupRestoreMessage(null)
      setRestorePreview(null)
      setPendingRestoreData(null)
    }
  }, [isOpen])

  const fetchStats = async () => {
    setLoading(true)
    setError(null)
    try {
      // Fetch both stats and version info in parallel
      const [statsData, versionData] = await Promise.all([
        apiClient.get<DatabaseHealth>('/database/stats'),
        apiClient.get<VersionInfo>('/version').catch(() => null)
      ])
      setStats(statsData)
      setVersionInfo(versionData)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  const handleBackup = async () => {
    setBackupLoading(true)
    setBackupRestoreMessage(null)
    try {
      const backupData = await apiClient.get<BackupData>('/database/backup')

      // Create and download the file
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
      setBackupRestoreMessage({
        type: 'success',
        text: `Backup downloaded: ${filename} (${totalRecords} records)`
      })
    } catch (err) {
      setBackupRestoreMessage({
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

    setBackupRestoreMessage(null)
    setRestorePreview(null)
    setPendingRestoreData(null)

    try {
      const text = await file.text()
      const backupData = JSON.parse(text) as BackupData

      // Validate structure
      if (!backupData.metadata || !backupData.data) {
        throw new Error('Invalid backup file: missing metadata or data sections')
      }

      if (!['1.0', '1.1'].includes(backupData.metadata.version)) {
        throw new Error(`Unsupported backup version: ${backupData.metadata.version}. Expected: 1.0 or 1.1`)
      }

      // Show preview for confirmation
      setRestorePreview(backupData.metadata)
      setPendingRestoreData(backupData)
    } catch (err) {
      setBackupRestoreMessage({
        type: 'error',
        text: `Invalid backup file: ${err instanceof Error ? err.message : 'Unknown error'}`
      })
    }

    // Reset file input so same file can be selected again
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const handleConfirmRestore = async () => {
    if (!pendingRestoreData) return

    setRestoreLoading(true)
    setBackupRestoreMessage(null)

    try {
      const result = await apiClient.post<RestoreResult>('/database/restore', {
        backup_data: pendingRestoreData,
        confirm_clear: true
      })

      const totalRestored = result.restored_counts
        ? Object.values(result.restored_counts).reduce((a, b) => a + b, 0)
        : 0

      setBackupRestoreMessage({
        type: 'success',
        text: `Database restored successfully! ${totalRestored} records imported.`
      })
      setRestorePreview(null)
      setPendingRestoreData(null)

      // Refresh stats to show new data
      await fetchStats()
    } catch (err) {
      setBackupRestoreMessage({
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

  if (!isOpen) return null

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>📊 Database Health & Statistics</h2>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        <div className="modal-body">
          {loading && (
            <div className="loading-state">
              <div className="spinner"></div>
              <p>Loading statistics...</p>
            </div>
          )}

          {error && (
            <div className="error-state">
              <p>❌ {error}</p>
              <button onClick={fetchStats} className="btn btn-small">Retry</button>
            </div>
          )}

          {stats && !loading && (
            <>
              {/* Version Info */}
              {versionInfo && (
                <div className="stats-section version-section">
                  <h3>🏷️ Version Information</h3>
                  <div className="version-grid">
                    <div className="version-card">
                      <div className="version-label">Platform</div>
                      <div className="version-value">v{versionInfo.platform.version}</div>
                      <div className="version-detail">
                        API {versionInfo.platform.api_version}
                        {versionInfo.platform.git_commit && (
                          <span className="git-commit"> ({versionInfo.platform.git_commit})</span>
                        )}
                      </div>
                    </div>
                    <div className="version-card">
                      <div className="version-label">Catalog</div>
                      <div className="version-value">{versionInfo.catalog.version}</div>
                      <div className="version-detail">
                        {versionInfo.catalog.controls_count} controls
                      </div>
                    </div>
                    <div className="version-card">
                      <div className="version-label">Evidence</div>
                      <div className="version-value">{versionInfo.catalog.evidence_count}</div>
                      <div className="version-detail">requirements</div>
                    </div>
                    <div className="version-card">
                      <div className="version-label">Interfaces</div>
                      <div className="version-value">{versionInfo.catalog.interface_count}</div>
                      <div className="version-detail">collection methods</div>
                    </div>
                  </div>
                  <div className="environment-badge">
                    <span className={`env-tag env-${versionInfo.environment}`}>
                      {versionInfo.environment}
                    </span>
                  </div>
                </div>
              )}

              {/* Health Status */}
              <div className="stats-section">
                <h3>🏥 Health Status</h3>
                <div className="health-indicator">
                  <span className={`health-badge ${stats.status === 'healthy' ? 'healthy' : 'unhealthy'}`}>
                    {stats.status === 'healthy' ? '✓ Healthy' : '⚠ Issues Detected'}
                  </span>
                </div>
              </div>

              {/* Database Tables */}
              <div className="stats-section">
                <h3>💾 Database Tables</h3>
                <div className="stats-grid">
                  <div className="stat-card">
                    <div className="stat-label">Organizations</div>
                    <div className="stat-value">{stats.database.organizations}</div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-label">Scoped Controls</div>
                    <div className="stat-value">{stats.database.scoped_controls}</div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-label">Evidence Tracking</div>
                    <div className="stat-value">{stats.database.evidence_tracking}</div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-label">Users</div>
                    <div className="stat-value">{stats.database.users}</div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-label">Organization Members</div>
                    <div className="stat-value">{stats.database.organization_members}</div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-label">Assignments</div>
                    <div className="stat-value">{stats.database.assignments}</div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-label">Comments</div>
                    <div className="stat-value">{stats.database.comments}</div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-label">Evidence Tasks</div>
                    <div className="stat-value">{stats.database.evidence_collection_tasks}</div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-label">Notifications</div>
                    <div className="stat-value">{stats.database.notifications}</div>
                  </div>
                  <div className="stat-card highlight-blue">
                    <div className="stat-label">Total Records</div>
                    <div className="stat-value">{stats.database.total_records}</div>
                  </div>
                </div>
              </div>

              {/* Control Statistics */}
              <div className="stats-section">
                <h3>📋 Control Statistics</h3>
                <div className="stats-grid">
                  <div className="stat-card">
                    <div className="stat-label">Selected</div>
                    <div className="stat-value">{stats.statistics.selected_controls}</div>
                  </div>
                  <div className="stat-card highlight-green">
                    <div className="stat-label">Implemented</div>
                    <div className="stat-value">{stats.statistics.implemented_controls}</div>
                  </div>
                  <div className="stat-card highlight-red">
                    <div className="stat-label">At Risk</div>
                    <div className="stat-value">{stats.statistics.at_risk_controls}</div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-label">Evidence Tracked</div>
                    <div className="stat-value">{stats.statistics.tracked_evidence}</div>
                  </div>
                </div>
              </div>

              {/* User & Task Statistics */}
              <div className="stats-section">
                <h3>👥 User & Task Statistics</h3>
                <div className="stats-grid">
                  <div className="stat-card highlight-blue">
                    <div className="stat-label">Active Users</div>
                    <div className="stat-value">{stats.statistics.active_users}</div>
                  </div>
                  <div className="stat-card highlight-orange">
                    <div className="stat-label">Pending Tasks</div>
                    <div className="stat-value">{stats.statistics.pending_tasks}</div>
                  </div>
                  <div className="stat-card highlight-green">
                    <div className="stat-label">Completed Tasks</div>
                    <div className="stat-value">{stats.statistics.completed_tasks}</div>
                  </div>
                  <div className="stat-card highlight-red">
                    <div className="stat-label">Overdue Tasks</div>
                    <div className="stat-value">{stats.statistics.overdue_tasks}</div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-label">Unread Notifications</div>
                    <div className="stat-value">{stats.statistics.unread_notifications}</div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-label">Total Comments</div>
                    <div className="stat-value">{stats.statistics.total_comments}</div>
                  </div>
                </div>
              </div>

              {/* By Status */}
              {Object.keys(stats.by_status).length > 0 && (
                <div className="stats-section">
                  <h3>📊 By Implementation Status</h3>
                  <div className="stats-list">
                    {Object.entries(stats.by_status)
                      .sort(([,a], [,b]) => b - a)
                      .map(([status, count]) => (
                        <div key={status} className="stats-row">
                          <span className={`status-badge status-${status}`}>
                            {status.replace('_', ' ')}
                          </span>
                          <span className="stats-count">{count}</span>
                        </div>
                      ))}
                  </div>
                </div>
              )}

              {/* By Maturity */}
              {Object.keys(stats.by_maturity).length > 0 && (
                <div className="stats-section">
                  <h3>📈 By Maturity Level</h3>
                  <div className="stats-list">
                    {Object.entries(stats.by_maturity)
                      .sort(([,a], [,b]) => b - a)
                      .map(([maturity, count]) => (
                        <div key={maturity} className="stats-row">
                          <span className="maturity-label">{maturity}</span>
                          <span className="stats-count">{count}</span>
                        </div>
                      ))}
                  </div>
                </div>
              )}

              {/* Tasks By Status */}
              {Object.keys(stats.tasks_by_status || {}).length > 0 && (
                <div className="stats-section">
                  <h3>✅ Tasks By Status</h3>
                  <div className="stats-list">
                    {Object.entries(stats.tasks_by_status)
                      .sort(([,a], [,b]) => b - a)
                      .map(([status, count]) => (
                        <div key={status} className="stats-row">
                          <span className={`status-badge status-${status}`}>
                            {status.replace('_', ' ')}
                          </span>
                          <span className="stats-count">{count}</span>
                        </div>
                      ))}
                  </div>
                </div>
              )}

              {/* Tasks By Type */}
              {Object.keys(stats.tasks_by_type || {}).length > 0 && (
                <div className="stats-section">
                  <h3>📝 Tasks By Type</h3>
                  <div className="stats-list">
                    {Object.entries(stats.tasks_by_type)
                      .sort(([,a], [,b]) => b - a)
                      .map(([type, count]) => (
                        <div key={type} className="stats-row">
                          <span className="maturity-label">{type}</span>
                          <span className="stats-count">{count}</span>
                        </div>
                      ))}
                  </div>
                </div>
              )}

              {/* Recent Activity */}
              <div className="stats-section">
                <h3>🕒 Recent Activity</h3>
                <div className="stats-list">
                  <div className="stats-row">
                    <span className="activity-label">Last Control Update</span>
                    <span className="activity-time">
                      {stats.recent_updates.last_control_update
                        ? new Date(stats.recent_updates.last_control_update).toLocaleString()
                        : 'No updates yet'}
                    </span>
                  </div>
                  <div className="stats-row">
                    <span className="activity-label">Last Evidence Update</span>
                    <span className="activity-time">
                      {stats.recent_updates.last_evidence_update
                        ? new Date(stats.recent_updates.last_evidence_update).toLocaleString()
                        : 'No updates yet'}
                    </span>
                  </div>
                  <div className="stats-row">
                    <span className="activity-label">Last Comment</span>
                    <span className="activity-time">
                      {stats.recent_updates.last_comment_update
                        ? new Date(stats.recent_updates.last_comment_update).toLocaleString()
                        : 'No comments yet'}
                    </span>
                  </div>
                  <div className="stats-row">
                    <span className="activity-label">Last Task Update</span>
                    <span className="activity-time">
                      {stats.recent_updates.last_task_update
                        ? new Date(stats.recent_updates.last_task_update).toLocaleString()
                        : 'No task updates yet'}
                    </span>
                  </div>
                </div>
              </div>

              {/* Data Management */}
              <div className="stats-section">
                <h3>💾 Data Management</h3>
                <p className="data-management-description">
                  Backup and restore database for environment migration or disaster recovery.
                </p>

                {/* Status Messages */}
                {backupRestoreMessage && (
                  <div className={`backup-restore-message ${backupRestoreMessage.type}`}>
                    {backupRestoreMessage.type === 'success' ? '✓' : '✕'} {backupRestoreMessage.text}
                  </div>
                )}

                {/* Restore Preview/Confirmation */}
                {restorePreview && (
                  <div className="restore-preview">
                    <h4>Restore Preview</h4>
                    <p className="restore-warning">
                      ⚠️ This will replace ALL existing data with the backup contents.
                    </p>
                    <div className="restore-details">
                      <div className="restore-meta">
                        <span>Backup created:</span>
                        <span>{new Date(restorePreview.created_at).toLocaleString()}</span>
                      </div>
                      {restorePreview.created_by && (
                        <div className="restore-meta">
                          <span>Created by:</span>
                          <span>{restorePreview.created_by}</span>
                        </div>
                      )}
                      <div className="restore-meta">
                        <span>Total records:</span>
                        <span>{Object.values(restorePreview.table_counts).reduce((a, b) => a + b, 0)}</span>
                      </div>
                    </div>
                    <div className="restore-table-counts">
                      {Object.entries(restorePreview.table_counts).map(([table, count]) => (
                        <div key={table} className="restore-table-row">
                          <span>{table.replace(/_/g, ' ')}</span>
                          <span>{count}</span>
                        </div>
                      ))}
                    </div>
                    <div className="restore-confirm-actions">
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

                {/* Backup/Restore Buttons */}
                {!restorePreview && (
                  <div className="backup-restore-actions">
                    <button
                      onClick={handleBackup}
                      className="btn btn-backup"
                      disabled={backupLoading || restoreLoading}
                    >
                      {backupLoading ? '⏳ Creating Backup...' : '⬇️ Download Backup'}
                    </button>
                    <button
                      onClick={() => fileInputRef.current?.click()}
                      className="btn btn-restore"
                      disabled={backupLoading || restoreLoading}
                    >
                      ⬆️ Restore from Backup
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

              {/* Action Buttons */}
              <div className="modal-actions">
                <button onClick={fetchStats} className="btn btn-secondary">
                  🔄 Refresh Stats
                </button>
                <button onClick={onClose} className="btn btn-primary">
                  Close
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
