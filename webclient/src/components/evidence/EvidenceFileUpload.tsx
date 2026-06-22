import { useState, useRef, useCallback, DragEvent, ChangeEvent } from 'react'
import {
  getEvidenceUploadUrl,
  confirmEvidenceUpload,
  type EvidenceFileUploadUrlResponse,
  type EvidenceFileResponse,
} from '../../data/apiClient'

// ---- Upload state machine ----

type UploadState =
  | { phase: 'idle' }
  | { phase: 'dragging' }
  | { phase: 'validating' }
  | { phase: 'uploading'; progress: number; filename: string }
  | { phase: 'confirming'; filename: string }
  | { phase: 'complete'; filename: string }
  | { phase: 'error'; message: string; file?: File }

// ---- Props ----

interface EvidenceFileUploadProps {
  orgId: string
  evidenceId: string
  onUploadComplete: () => void
  acceptedTypes?: string[]
  maxSizeMB?: number
}

const DEFAULT_ACCEPTED_TYPES = [
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'text/csv',
  'image/png',
  'image/jpeg',
  'application/json',
  'text/yaml',
]

// ---- Helpers ----

async function computeSha256(file: File): Promise<string> {
  const buffer = await file.arrayBuffer()
  const hashBuffer = await crypto.subtle.digest('SHA-256', buffer)
  const hashArray = Array.from(new Uint8Array(hashBuffer))
  return hashArray.map(b => b.toString(16).padStart(2, '0')).join('')
}

function formatMaxSize(mb: number): string {
  return `${mb} MB`
}

function friendlyType(mimeType: string): string {
  if (mimeType.includes('pdf')) return 'PDF'
  if (mimeType.includes('spreadsheet') || mimeType === 'text/csv') return 'spreadsheet'
  if (mimeType.includes('word') || mimeType.includes('document')) return 'Word document'
  if (mimeType.includes('image')) return 'image'
  if (mimeType === 'application/json') return 'JSON'
  if (mimeType === 'text/yaml') return 'YAML'
  return mimeType
}

function normalizeContentType(file: File): string {
  const ext = file.name.toLowerCase().split('.').pop()
  if (ext === 'yaml' || ext === 'yml') return 'text/yaml'
  if (ext === 'json' && !file.type) return 'application/json'
  return file.type
}

// ---- Component ----

export function EvidenceFileUpload({
  orgId,
  evidenceId,
  onUploadComplete,
  acceptedTypes = DEFAULT_ACCEPTED_TYPES,
  maxSizeMB = 50,
}: EvidenceFileUploadProps) {
  const [state, setState] = useState<UploadState>({ phase: 'idle' })
  const fileInputRef = useRef<HTMLInputElement>(null)
  const xhrRef = useRef<XMLHttpRequest | null>(null)
  const dragCounterRef = useRef(0)
  // Tracks whether the current upload was aborted mid-flight (cancel or error set before confirm)
  const uploadAbortedRef = useRef(false)

  const maxSizeBytes = maxSizeMB * 1024 * 1024

  // ---- Validation ----

  function validateFile(file: File): string | null {
    if (!acceptedTypes.includes(normalizeContentType(file))) {
      const friendly = acceptedTypes.map(friendlyType).join(', ')
      return `File type not accepted. Allowed: ${friendly}`
    }
    if (file.size > maxSizeBytes) {
      return `File exceeds maximum size of ${formatMaxSize(maxSizeMB)}`
    }
    return null
  }

  // ---- Upload flow ----

  const processFile = useCallback(async (file: File, resetAfterComplete = true): Promise<boolean> => {
    uploadAbortedRef.current = false
    setState({ phase: 'validating' })

    const validationError = validateFile(file)
    if (validationError) {
      setState({ phase: 'error', message: validationError, file })
      return false
    }

    let sha256: string
    try {
      sha256 = await computeSha256(file)
    } catch {
      setState({ phase: 'error', message: 'Failed to compute file hash. Please try again.', file })
      return false
    }

    let uploadInfo: EvidenceFileUploadUrlResponse
    try {
      uploadInfo = await getEvidenceUploadUrl(evidenceId, {
        filename: file.name,
        content_type: normalizeContentType(file),
        file_size_bytes: file.size,
      }, orgId)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to get upload URL'
      setState({ phase: 'error', message, file })
      return false
    }

    // Detect upload method: Azure SAS URLs have empty fields; S3 presigned POST has fields
    const isAzureSas = Object.keys(uploadInfo.fields).length === 0

    // XHR upload with progress tracking
    await new Promise<void>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhrRef.current = xhr

      xhr.upload.addEventListener('progress', (event) => {
        if (event.lengthComputable) {
          const progress = Math.round((event.loaded / event.total) * 100)
          setState({ phase: 'uploading', progress, filename: file.name })
        }
      })

      xhr.addEventListener('load', () => {
        xhrRef.current = null
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve()
        } else {
          reject(new Error(`Upload failed with status ${xhr.status}`))
        }
      })

      xhr.addEventListener('error', () => {
        xhrRef.current = null
        reject(new Error('Network error during upload'))
      })

      xhr.addEventListener('abort', () => {
        xhrRef.current = null
        reject(new Error('Upload cancelled'))
      })

      setState({ phase: 'uploading', progress: 0, filename: file.name })

      if (isAzureSas) {
        // Azure Blob Storage: PUT raw file body with required headers
        xhr.open('PUT', uploadInfo.url)
        xhr.setRequestHeader('x-ms-blob-type', 'BlockBlob')
        xhr.setRequestHeader('Content-Type', normalizeContentType(file))
        xhr.send(file)
      } else {
        // S3 presigned POST: multipart FormData with fields + file
        const formData = new FormData()
        Object.entries(uploadInfo.fields).forEach(([key, value]) => {
          formData.append(key, value)
        })
        formData.append('file', file)
        xhr.open('POST', uploadInfo.url)
        xhr.send(formData)
      }
    }).catch((err: unknown) => {
      uploadAbortedRef.current = true
      const message = err instanceof Error ? err.message : 'Upload failed'
      if (message === 'Upload cancelled') {
        setState({ phase: 'idle' })
      } else {
        setState({ phase: 'error', message, file })
      }
    })

    // If the upload was cancelled or errored, stop before confirming
    if (uploadAbortedRef.current) {
      return false
    }

    // Confirm upload with backend
    setState({ phase: 'confirming', filename: file.name })
    let confirmed: EvidenceFileResponse
    try {
      confirmed = await confirmEvidenceUpload(evidenceId, {
        s3_key: uploadInfo.s3_key,
        sha256_hash: sha256,
      }, orgId)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to confirm upload'
      setState({ phase: 'error', message, file })
      return false
    }

    // Suppress unused variable lint — confirmed used for side-effect typing
    void confirmed

    setState({ phase: 'complete', filename: file.name })
    onUploadComplete()

    // Reset to idle after brief success display — suppressed during batch
    // uploads so the next file's state isn't clobbered mid-flight.
    if (resetAfterComplete) {
      setTimeout(() => {
        setState({ phase: 'idle' })
      }, 2500)
    }
    return true
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgId, evidenceId, acceptedTypes, maxSizeBytes, onUploadComplete])

  const processFiles = useCallback(async (files: File[]) => {
    for (let i = 0; i < files.length; i++) {
      const isLast = i === files.length - 1
      const ok = await processFile(files[i], isLast)
      if (!ok) break
    }
  }, [processFile])

  // ---- Cancel ----

  function handleCancel() {
    uploadAbortedRef.current = true
    if (xhrRef.current) {
      xhrRef.current.abort()
    }
    setState({ phase: 'idle' })
  }

  // ---- Retry ----

  function handleRetry() {
    if (state.phase === 'error' && state.file) {
      processFile(state.file)
    } else {
      setState({ phase: 'idle' })
    }
  }

  // ---- Drag-and-drop ----

  function handleDragEnter(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current += 1
    if (state.phase === 'idle') {
      setState({ phase: 'dragging' })
    }
  }

  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    e.stopPropagation()
    e.dataTransfer.dropEffect = 'copy'
  }

  function handleDragLeave(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current -= 1
    if (dragCounterRef.current === 0 && state.phase === 'dragging') {
      setState({ phase: 'idle' })
    }
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current = 0

    const files = Array.from(e.dataTransfer.files)
    if (files.length === 0) {
      setState({ phase: 'idle' })
      return
    }

    processFiles(files)
  }

  // ---- Click to browse ----

  function handleZoneClick() {
    if (state.phase === 'idle' || state.phase === 'dragging') {
      fileInputRef.current?.click()
    }
  }

  function handleFileInputChange(e: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? [])
    if (files.length === 0) return
    processFiles(files)
    // Reset input so the same file can be re-selected after an error
    e.target.value = ''
  }

  // ---- Derived ----

  const isActive = state.phase === 'uploading' || state.phase === 'confirming' || state.phase === 'validating'
  const isDragging = state.phase === 'dragging'
  const isError = state.phase === 'error'
  const isComplete = state.phase === 'complete'

  const acceptAttr = [...acceptedTypes, '.yml', '.yaml', '.json'].join(',')

  // ---- Render ----

  return (
    <div className="evidence-upload-root">
      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={acceptAttr}
        className="evidence-upload-hidden-input"
        tabIndex={-1}
        aria-hidden="true"
        onChange={handleFileInputChange}
      />

      {/* Drop zone */}
      <div
        className={[
          'evidence-upload-zone',
          isDragging ? 'evidence-upload-zone--dragging' : '',
          isActive ? 'evidence-upload-zone--active' : '',
          isError ? 'evidence-upload-zone--error' : '',
          isComplete ? 'evidence-upload-zone--complete' : '',
        ]
          .filter(Boolean)
          .join(' ')}
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={!isActive && !isError && !isComplete ? handleZoneClick : undefined}
        role={!isActive ? 'button' : undefined}
        tabIndex={!isActive ? 0 : undefined}
        aria-label={!isActive ? 'Upload evidence file — click or drag and drop' : undefined}
        onKeyDown={
          !isActive
            ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  handleZoneClick()
                }
              }
            : undefined
        }
      >
        {/* Idle / Dragging */}
        {(state.phase === 'idle' || state.phase === 'dragging') && (
          <div className="evidence-upload-prompt">
            <div className="evidence-upload-icon" aria-hidden="true">
              {isDragging ? '\u2B07\uFE0F' : '\u2B06\uFE0F'}
            </div>
            <div className="evidence-upload-headline">
              {isDragging ? 'Drop to upload' : 'Drag and drop a file, or click to browse'}
            </div>
            <div className="evidence-upload-constraints">
              Accepted: PDF, DOCX, XLSX, CSV, PNG, JPEG, JSON, YAML
              {' \u00B7 '}
              Max {formatMaxSize(maxSizeMB)}
            </div>
          </div>
        )}

        {/* Validating */}
        {state.phase === 'validating' && (
          <div className="evidence-upload-status">
            <div className="evidence-upload-spinner" aria-hidden="true" />
            <span className="evidence-upload-status-label">Validating file...</span>
          </div>
        )}

        {/* Uploading */}
        {state.phase === 'uploading' && (
          <div className="evidence-upload-progress-container">
            <div className="evidence-upload-progress-filename">{state.filename}</div>
            <div className="evidence-upload-progress-bar-track">
              <div
                className="evidence-upload-progress-bar-fill"
                style={{ width: `${state.progress}%` }}
                role="progressbar"
                aria-valuenow={state.progress}
                aria-valuemin={0}
                aria-valuemax={100}
              />
            </div>
            <div className="evidence-upload-progress-footer">
              <span className="evidence-upload-progress-pct">{state.progress}%</span>
              <button
                type="button"
                className="evidence-upload-cancel-btn"
                onClick={(e) => {
                  e.stopPropagation()
                  handleCancel()
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Confirming */}
        {state.phase === 'confirming' && (
          <div className="evidence-upload-status">
            <div className="evidence-upload-spinner" aria-hidden="true" />
            <span className="evidence-upload-status-label">
              Confirming upload for {state.filename}...
            </span>
          </div>
        )}

        {/* Complete */}
        {state.phase === 'complete' && (
          <div className="evidence-upload-complete">
            <span className="evidence-upload-complete-icon" aria-hidden="true">
              {'\u2714\uFE0F'}
            </span>
            <span className="evidence-upload-complete-label">
              {state.filename} uploaded successfully
            </span>
          </div>
        )}

        {/* Error */}
        {state.phase === 'error' && (
          <div
            className="evidence-upload-error"
            onClick={(e) => e.stopPropagation()}
          >
            <span className="evidence-upload-error-icon" aria-hidden="true">
              {'\u26A0\uFE0F'}
            </span>
            <span className="evidence-upload-error-message">{state.message}</span>
            <div className="evidence-upload-error-actions">
              {state.file && (
                <button
                  type="button"
                  className="evidence-upload-retry-btn"
                  onClick={(e) => {
                    e.stopPropagation()
                    handleRetry()
                  }}
                >
                  Retry
                </button>
              )}
              <button
                type="button"
                className="evidence-upload-dismiss-btn"
                onClick={(e) => {
                  e.stopPropagation()
                  setState({ phase: 'idle' })
                }}
              >
                Dismiss
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default EvidenceFileUpload
