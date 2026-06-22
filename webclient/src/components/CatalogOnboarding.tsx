import { useCallback, useRef, useState } from 'react'
import {
  uploadCatalogExcel,
  getCatalogImportStatus,
  type CatalogImportStatus,
} from '../data/apiClient'

interface Props {
  /** Called once the catalogue has been seeded so the app can continue loading. */
  onSeeded: () => void
}

type Phase = 'idle' | 'uploading' | 'processing' | 'done' | 'error'

const STEP_LABEL: Record<string, string> = {
  downloading: 'Reading your workbook…',
  extracting: 'Extracting the SCF catalogue…',
  seeding: 'Seeding the database…',
}

/**
 * First-run onboarding gate for self-hosted (OSS) deployments. The SCF
 * catalogue is licensed and never shipped, so a fresh install has an empty
 * catalogue. This screen lets the operator upload their own SCF .xlsx, which
 * the backend extracts and seeds live — replacing the old
 * `docker compose --profile init run` step.
 */
export default function CatalogOnboarding({ onSeeded }: Props) {
  const [file, setFile] = useState<File | null>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [step, setStep] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<number | null>(null)

  const poll = useCallback(
    (taskId: string) => {
      const tick = async () => {
        let status: CatalogImportStatus
        try {
          status = await getCatalogImportStatus(taskId)
        } catch (e) {
          setPhase('error')
          setError(e instanceof Error ? e.message : 'Failed to check import status')
          return
        }
        if (status.state === 'SUCCESS') {
          setStep('seeding')
          setPhase('done')
          // Brief beat so the operator sees the success state before reload.
          window.setTimeout(onSeeded, 800)
          return
        }
        if (status.state === 'FAILURE') {
          setPhase('error')
          setError(status.error || 'Import failed. Check the workbook and try again.')
          return
        }
        setStep(status.step)
        pollRef.current = window.setTimeout(tick, 1500)
      }
      tick()
    },
    [onSeeded],
  )

  const onUpload = useCallback(async () => {
    if (!file) return
    setError(null)
    setPhase('uploading')
    try {
      const { task_id } = await uploadCatalogExcel(file)
      setPhase('processing')
      poll(task_id)
    } catch (e) {
      setPhase('error')
      setError(e instanceof Error ? e.message : 'Upload failed')
    }
  }, [file, poll])

  const busy = phase === 'uploading' || phase === 'processing'

  const inputRef = useRef<HTMLInputElement | null>(null)
  const [dragOver, setDragOver] = useState(false)

  const pickFile = useCallback((f: File | null | undefined) => {
    if (!f) return
    setFile(f)
    setError(null)
  }, [])

  const ctaLabel =
    phase === 'uploading'
      ? 'Uploading…'
      : phase === 'processing'
        ? STEP_LABEL[step ?? ''] ?? 'Importing…'
        : 'Upload and seed catalogue'

  return (
    <div className="loading-screen">
      <div className="catalog-onboarding">
        {/* Branded emblem: shield + upload arrow */}
        <div className="co-emblem" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path
              d="M12 2.5l7 2.6v5.4c0 4.6-3 8.4-7 9.6-4-1.2-7-5-7-9.6V5.1l7-2.6z"
              fill="currentColor"
              opacity="0.16"
            />
            <path
              d="M12 2.5l7 2.6v5.4c0 4.6-3 8.4-7 9.6-4-1.2-7-5-7-9.6V5.1l7-2.6z"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinejoin="round"
            />
            <path
              d="M12 14.5V8.7m0 0L9.6 11.1M12 8.7l2.4 2.4"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>

        <h1 className="co-title">Welcome — let’s load your SCF catalogue</h1>
        <p className="co-subtitle">
          The Secure Controls Framework catalogue is licensed, so it isn’t bundled with
          this platform. Upload your own SCF Excel workbook (<code>.xlsx</code>) to seed
          it — a one-time setup step.
        </p>

        {phase === 'done' ? (
          <div className="co-success">
            <div className="co-success-check" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path
                  d="M5 12.8l4.2 4.2L19 7.2"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
            <div className="co-success-text">Catalogue seeded. Loading your workspace…</div>
          </div>
        ) : (
          <>
            {!busy && (
              <>
                <div
                  className={`co-dropzone${dragOver ? ' co-dragover' : ''}`}
                  onDragOver={(e) => {
                    e.preventDefault()
                    setDragOver(true)
                  }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={(e) => {
                    e.preventDefault()
                    setDragOver(false)
                    pickFile(e.dataTransfer.files?.[0])
                  }}
                >
                  <input
                    ref={inputRef}
                    className="co-file-input"
                    type="file"
                    accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    disabled={busy}
                    aria-label="Choose your SCF workbook (.xlsx)"
                    onChange={(e) => pickFile(e.target.files?.[0])}
                  />
                  <span className="co-drop-glyph" aria-hidden="true">
                    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                      <path
                        d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5"
                        stroke="currentColor"
                        strokeWidth="1.8"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                      <path
                        d="M4 15v2.5A2.5 2.5 0 006.5 20h11a2.5 2.5 0 002.5-2.5V15"
                        stroke="currentColor"
                        strokeWidth="1.8"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </span>
                  <span className="co-drop-prompt">Choose your SCF workbook (.xlsx)</span>
                  <span className="co-drop-hint">Click to browse, or drag &amp; drop it here</span>
                </div>

                {file && (
                  <div className="co-file-pill">
                    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                      <path
                        d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8l-5-5z"
                        stroke="currentColor"
                        strokeWidth="1.6"
                        strokeLinejoin="round"
                      />
                      <path d="M14 3v5h5" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
                    </svg>
                    <span className="co-file-name">{file.name}</span>
                    <span className="co-file-size">{(file.size / 1024 / 1024).toFixed(1)} MB</span>
                  </div>
                )}
              </>
            )}

            {!busy && (
              <button
                type="button"
                className="btn btn-primary co-cta"
                disabled={!file || busy}
                onClick={onUpload}
              >
                {ctaLabel}
              </button>
            )}

            {busy && (
              <div className="co-progress" role="status" aria-live="polite">
                <div className="loading-spinner co-spinner" />
                <div className="co-progress-label">{ctaLabel}</div>
              </div>
            )}

            {error && (
              <div className="co-error" role="alert">
                <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                  <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.7" />
                  <path
                    d="M12 7.5v5.5M12 16.2h.01"
                    stroke="currentColor"
                    strokeWidth="1.9"
                    strokeLinecap="round"
                  />
                </svg>
                <span>{error}</span>
              </div>
            )}

            {!busy && (
              <a
                className="co-repo-link"
                href="https://github.com/securecontrolsframework/securecontrolsframework"
                target="_blank"
                rel="noopener noreferrer"
              >
                Don’t have the workbook? Get the Secure Controls Framework →
              </a>
            )}
          </>
        )}
      </div>
    </div>
  )
}
