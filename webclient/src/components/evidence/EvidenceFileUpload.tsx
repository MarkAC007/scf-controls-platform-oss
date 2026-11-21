// SPDX-License-Identifier: AGPL-3.0-only
import { useState, useRef, useCallback, useMemo, DragEvent, ChangeEvent } from 'react'
import {
  getEvidenceUploadUrl,
  confirmEvidenceUpload,
  type EvidenceFileUploadUrlResponse,
  type EvidenceFileResponse,
  type EvidenceFileTypesResponse,
} from '../../data/apiClient'
import { loadEvidenceFileTypes, useEvidenceFileTypes } from '../../hooks/useEvidenceFileTypes'
import { acceptAttribute, listedTypeOfFile, uniqueLabels } from './evidenceFileTypes'
import {
  PreparerAssertionForm,
  EMPTY_ASSERTIONS,
  assertionErrors,
  toAssertionPayload,
  type AssertionFormValues,
} from './PreparerAssertionForm'

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
  /**
   * Narrows the server's list to these content types. Omit to accept
   * everything the server lists. It can only narrow: a type the server does
   * not list is not made acceptable by naming it here.
   */
  acceptedTypes?: string[]
  /** Used only until the server's list has loaded, which states its own limit. */
  maxSizeMB?: number
}

// ---- Helpers ----

async function computeSha256(file: File): Promise<string> {
  const buffer = await file.arrayBuffer()
  const hashBuffer = await crypto.subtle.digest('SHA-256', buffer)
  const hashArray = Array.from(new Uint8Array(hashBuffer))
  return hashArray.map(b => b.toString(16).padStart(2, '0')).join('')
}

function formatMaxSize(bytes: number): string {
  const mb = bytes / (1024 * 1024)
  return `${Number.isInteger(mb) ? mb : mb.toFixed(1)} MB`
}

/**
 * What the upload control enforces, derived from the server's list.
 *
 * Null when the list has not arrived (still loading, or the request failed).
 * The control then checks nothing but size and sends the browser's type: the
 * server is the authority on what it accepts, and a guessed list here is how
 * the old copy came to refuse files the server would have taken. A file the
 * browser gives no type for cannot be sent that way and is held back with a
 * reason.
 */
interface UploadRules {
  accept: string
  labels: string[]
  maxSizeBytes: number
  /** The canonical content type for a file, or null when it is not accepted. */
  contentTypeOf: (file: File) => string | null
}

function buildRules(
  fileTypes: EvidenceFileTypesResponse | null,
  acceptedTypes: string[] | undefined,
): UploadRules | null {
  if (!fileTypes) return null
  const allowed = acceptedTypes
    ? fileTypes.types.filter(t => acceptedTypes.includes(t.content_type))
    : fileTypes.types
  return {
    accept: acceptAttribute(allowed),
    labels: uniqueLabels(allowed),
    maxSizeBytes: fileTypes.max_file_size_bytes,
    contentTypeOf: (file) => {
      // Identified against the whole list, then checked against the narrowed
      // one, so a caller's narrowing cannot change what a file *is*.
      const listed = listedTypeOfFile(fileTypes.types, file)
      return listed && allowed.includes(listed) ? listed.content_type : null
    },
  }
}

const LIST_UNAVAILABLE =
  'The list of accepted file types could not be loaded, and the browser does not say what kind of ' +
  'file this is. Reload the page and try again.'


// ---- Component ----

export function EvidenceFileUpload({
  orgId,
  evidenceId,
  onUploadComplete,
  acceptedTypes,
  maxSizeMB = 50,
}: EvidenceFileUploadProps) {
  const { fileTypes } = useEvidenceFileTypes()
  // Keyed on the joined list so a caller passing a fresh array literal on every
  // render does not rebuild the rules (and the upload callback) each time.
  const acceptedKey = acceptedTypes?.join('\n')
  const rules = useMemo(
    () => buildRules(fileTypes, acceptedTypes),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [fileTypes, acceptedKey],
  )
  const [state, setState] = useState<UploadState>({ phase: 'idle' })
  // Preparer assertions (#786, #802) live *beside* the upload state machine, not
  // in it. Uploads start the instant a file is dropped with no interaction in
  // between, so a form inside the phase union would have to interrupt the drop.
  // Held here, the values apply to whatever is dropped next — which is also how
  // batches behave in practice: files dropped together share a period, a
  // population and an extract.
  const [assertions, setAssertions] = useState<AssertionFormValues>(EMPTY_ASSERTIONS)
  const [assertionsExpanded, setAssertionsExpanded] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const xhrRef = useRef<XMLHttpRequest | null>(null)
  const dragCounterRef = useRef(0)
  // Tracks whether the current upload was aborted mid-flight (cancel or error set before confirm)
  const uploadAbortedRef = useRef(false)

  const maxSizeBytes = rules?.maxSizeBytes ?? maxSizeMB * 1024 * 1024

  // ---- Validation ----

  /** The content type to upload as, or the reason the file is refused. */
  function checkFile(
    file: File,
    active: UploadRules | null,
  ): { contentType: string } | { error: string } {
    if (!active && !file.type) {
      // No list and no type from the browser (.yaml, .md, .log): there is
      // nothing true to send, and a made-up type is refused by the server.
      return { error: LIST_UNAVAILABLE }
    }
    const contentType = active ? active.contentTypeOf(file) : file.type
    if (contentType === null) {
      return { error: `File type not accepted. Allowed: ${active!.labels.join(', ')}` }
    }
    const limit = active?.maxSizeBytes ?? maxSizeBytes
    if (file.size > limit) {
      return { error: `File exceeds maximum size of ${formatMaxSize(limit)}` }
    }
    return { contentType }
  }

  // ---- Upload flow ----

  const processFile = useCallback(async (file: File, resetAfterComplete = true): Promise<boolean> => {
    uploadAbortedRef.current = false
    setState({ phase: 'validating' })

    // A file dropped before the list has arrived waits for it, so a `.yaml` or
    // `.log` the browser gives no type for is still sent under its real one.
    // If the list cannot be had, the server decides.
    let active = rules
    if (!active) {
      try {
        active = buildRules(await loadEvidenceFileTypes(), acceptedTypes)
      } catch {
        active = null
      }
    }

    const checked = checkFile(file, active)
    if ('error' in checked) {
      setState({ phase: 'error', message: checked.error, file })
      return false
    }
    const contentType = checked.contentType

    // Checked before the bytes move. The backend enforces the same two rules and
    // remains the authority; catching them here means a preparer is told while
    // the form is still in front of them, rather than after a 50 MB upload has
    // completed and the confirm has come back 422.
    const badAssertions = assertionErrors(assertions)
    if (badAssertions.length > 0) {
      setAssertionsExpanded(true)
      setState({
        phase: 'error',
        message: `Fix the audit assertions first: ${badAssertions.join(' ')}`,
        file,
      })
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
        content_type: contentType,
        file_size_bytes: file.size,
      }, orgId)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to get upload URL'
      setState({ phase: 'error', message, file })
      return false
    }

    // The backend says which verb it signed. It used to be inferred from
    // `fields` being empty — a stand-in for "this is Azure" that is only right
    // by coincidence, since a presigned POST with no extra fields is a legal
    // reply from an S3-compatible store and would have been uploaded as a PUT.
    // An unrecognised verb is refused here rather than guessed: a backend that
    // grows a third upload shape must not have this client silently pick one.
    const method = uploadInfo.method
    if (method !== 'POST' && method !== 'PUT') {
      setState({
        phase: 'error',
        message:
          'This server asked for an upload method this app does not support. Update the app, or report this to your administrator.',
        file,
      })
      return false
    }

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

      if (method === 'PUT') {
        // Self-contained signed URL: the file is the request body. The blob
        // type header is Azure's alone, so it is keyed off the provider rather
        // than off the verb — another store signing a PUT must not be sent it.
        xhr.open('PUT', uploadInfo.url)
        if (uploadInfo.provider === 'azure_blob') {
          xhr.setRequestHeader('x-ms-blob-type', 'BlockBlob')
        }
        xhr.setRequestHeader('Content-Type', contentType)
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
        // Proves this key is the one the backend just handed *this* user for
        // *this* evidence item. Without it the confirm is refused.
        upload_ticket: uploadInfo.upload_ticket,
        // Whatever the preparer asserted, if anything. Blank fields are omitted
        // rather than sent as empty strings — "not asserted" is a state the
        // column records, and an empty string is not that state.
        ...toAssertionPayload(assertions),
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
  }, [orgId, evidenceId, rules, maxSizeBytes, onUploadComplete, assertions])

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

  // Omitted, not empty, until the list arrives: an empty accept would still be
  // an attribute, and the picker must not filter on a list it does not have.
  const acceptAttr = rules?.accept

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

      <PreparerAssertionForm
        values={assertions}
        onChange={setAssertions}
        expanded={assertionsExpanded}
        onToggle={() => setAssertionsExpanded(prev => !prev)}
        disabled={isActive}
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
              {rules && rules.labels.length > 0 && (
                <>
                  Accepted: {rules.labels.join(', ')}
                  {' \u00B7 '}
                </>
              )}
              Max {formatMaxSize(maxSizeBytes)}
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
