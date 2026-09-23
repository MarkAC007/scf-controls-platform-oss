/**
 * PublisherChangesPanel — what SCF itself says changed in the new release.
 *
 * The counts come free on the staged run's ``diff_summary.publisher_changes``
 * (parsed from the workbook's own change sheets). The itemised lists are a
 * second request, so they are fetched only when the admin asks for them.
 *
 * This is the publisher's declaration, not the platform's computed diff — it
 * sits above DiffPreview so the admin reads "what SCF changed" before "what
 * that does to this platform".
 */
import { useState } from 'react'
import { getPublisherChanges } from '../../data/catalogUpgradeApi'
import type { PublisherChanges, PublisherChangesSummary } from '../../types/catalogUpgrade'

/**
 * Publisher control-change categories in the order an admin cares about, with
 * the phrasing used in the count row. Keys the publisher did not report, or
 * reported as zero, are omitted.
 *
 * ``merged`` counts the surviving controls whose Change Overview errata says
 * they absorbed another control. The itemised list below counts the controls
 * that went away, one row each, so the two numbers legitimately differ when a
 * survivor absorbed more than one control (2026.3: 22 survivors, 23 merged).
 */
const CONTROL_COUNT_LABELS: ReadonlyArray<readonly [string, string]> = [
  ['renumbered', 'controls renumbered'],
  ['new_control', 'new controls'],
  ['merged', 'controls absorbed a merge'],
  ['renamed', 'controls renamed'],
  ['wordsmithed', 'controls wordsmithed'],
  ['moved_domains', 'controls moved to another domain'],
]

function CountChip({ text }: { text: string }) {
  return <span className="badge badge-viewer">{text}</span>
}

interface PublisherChangesPanelProps {
  runId: string
  /** The run's to_version — the release these changes describe. */
  toVersion?: string | null
  summary: PublisherChangesSummary
}

export default function PublisherChangesPanel({
  runId,
  toVersion,
  summary,
}: PublisherChangesPanelProps) {
  const [expanded, setExpanded] = useState(false)
  const [details, setDetails] = useState<PublisherChanges | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleToggle = async () => {
    if (expanded) {
      setExpanded(false)
      return
    }
    setExpanded(true)
    if (details || loading) return
    setLoading(true)
    setError(null)
    try {
      setDetails(await getPublisherChanges(runId))
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load publisher changes')
    } finally {
      setLoading(false)
    }
  }

  const chips: string[] = []
  if (summary.frameworks_added) chips.push(`${summary.frameworks_added} frameworks added`)
  if (summary.frameworks_removed) chips.push(`${summary.frameworks_removed} frameworks removed`)
  if (summary.mapping_errata) chips.push(`${summary.mapping_errata} mapping errata`)
  for (const [key, label] of CONTROL_COUNT_LABELS) {
    const count = summary.controls?.[key]
    if (count) chips.push(`${count} ${label}`)
  }

  return (
    <div
      style={{
        padding: '1rem 1.25rem',
        marginBottom: '1.25rem',
        background: 'var(--card)',
        border: '1px solid var(--border)',
        borderRadius: '10px',
      }}
    >
      <h4 style={{ margin: '0 0 0.5rem' }}>
        What the publisher changed in {toVersion || 'this release'}
      </h4>

      {summary.summary && (
        <blockquote
          style={{
            margin: '0 0 0.75rem',
            paddingLeft: '0.75rem',
            borderLeft: '3px solid var(--border)',
            color: 'var(--muted)',
          }}
        >
          {summary.summary}
        </blockquote>
      )}

      {chips.length > 0 ? (
        <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', marginBottom: '0.75rem' }}>
          {chips.map(chip => (
            <CountChip key={chip} text={chip} />
          ))}
        </div>
      ) : (
        <p style={{ color: 'var(--muted)', marginBottom: '0.75rem' }}>
          The publisher reported no counted changes in this release.
        </p>
      )}

      <button
        type="button"
        className="btn btn-secondary btn-sm"
        aria-expanded={expanded}
        onClick={handleToggle}
      >
        {expanded ? 'Hide details' : 'Show details'}
      </button>

      {expanded && (
        <div style={{ marginTop: '0.75rem' }}>
          {loading && <div className="loading-spinner" />}
          {error && (
            <p role="alert" style={{ color: 'var(--danger)', margin: 0 }}>
              {error}
            </p>
          )}
          {details && !loading && (
            <>
              <PublisherFrameworkList
                heading="Frameworks removed by the publisher"
                items={details.frameworks.removed.map(f => `${f.name} (${f.fdi})`)}
                emptyText="None."
              />
              <PublisherFrameworkList
                heading="Frameworks added by the publisher"
                items={details.frameworks.added.map(f => `${f.name} (${f.fdi})`)}
                emptyText="None."
              />
              <PublisherFrameworkList
                heading="Controls merged into another control"
                items={details.controls.merged.map(
                  c => `${c.legacy_scf_id} (${c.legacy_name}) → ${c.merged_into}`
                )}
                emptyText="None."
              />
            </>
          )}
        </div>
      )}
    </div>
  )
}

function PublisherFrameworkList({
  heading,
  items,
  emptyText,
}: {
  heading: string
  items: string[]
  emptyText: string
}) {
  return (
    <div style={{ marginBottom: '0.75rem' }}>
      <div className="platform-stat-label">{heading}</div>
      {items.length === 0 ? (
        <p style={{ color: 'var(--muted)', margin: '0.25rem 0 0' }}>{emptyText}</p>
      ) : (
        <ul style={{ margin: '0.25rem 0 0', paddingLeft: '1.25rem' }}>
          {items.map(item => (
            <li key={item} style={{ marginBottom: '0.2rem' }}>
              {item}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
