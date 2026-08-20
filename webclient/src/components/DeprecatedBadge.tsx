/**
 * DeprecatedBadge — shared lifecycle badge for catalog rows (plan §4.4.12).
 *
 * Renders nothing unless the row's catalog_status is 'deprecated', so it can
 * be dropped unconditionally into any row render. The badge fields arrive
 * from the WP3a/WP3b read-path sweep and are absent on older payloads —
 * ``getCatalogLifecycle`` reads them defensively from any row shape.
 */
import type { CSSProperties } from 'react'
import type { CatalogLifecycleFields } from '../types/catalogUpgrade'

/**
 * Safely extract the lifecycle badge fields from an arbitrary API row.
 * Rows from endpoints not yet swept simply yield an empty object.
 */
export function getCatalogLifecycle(row: unknown): CatalogLifecycleFields {
  if (!row || typeof row !== 'object') return {}
  const r = row as Record<string, unknown>
  return {
    catalog_status: typeof r.catalog_status === 'string' ? r.catalog_status : undefined,
    retired_in_version: typeof r.retired_in_version === 'string' ? r.retired_in_version : undefined,
    superseded_by: typeof r.superseded_by === 'string' ? r.superseded_by : undefined,
  }
}

/** True when the row is a deprecated catalog entity. */
export function isDeprecated(lifecycle: CatalogLifecycleFields | null | undefined): boolean {
  return lifecycle?.catalog_status === 'deprecated'
}

interface DeprecatedBadgeProps extends CatalogLifecycleFields {
  /** Compact rendering for dense rows (smaller font, no margins). */
  compact?: boolean
}

const badgeStyle: CSSProperties = {
  display: 'inline-block',
  padding: '0.15rem 0.5rem',
  borderRadius: 4,
  fontSize: '0.7rem',
  fontWeight: 600,
  letterSpacing: '0.03em',
  textTransform: 'uppercase',
  background: 'rgba(245, 158, 11, 0.15)',
  color: '#b45309',
  whiteSpace: 'nowrap',
  cursor: 'default',
}

export default function DeprecatedBadge({
  catalog_status,
  retired_in_version,
  superseded_by,
  compact = false,
}: DeprecatedBadgeProps) {
  if (catalog_status !== 'deprecated') return null

  const hints: string[] = []
  if (retired_in_version) hints.push(`Retired in catalog ${retired_in_version}`)
  if (superseded_by) hints.push(`Superseded by ${superseded_by}`)
  const title = hints.length > 0
    ? hints.join(' · ')
    : 'This control has been retired from the SCF catalog'

  return (
    <span
      className="deprecated-badge"
      style={compact ? { ...badgeStyle, fontSize: '0.62rem', padding: '0.1rem 0.35rem' } : badgeStyle}
      title={title}
    >
      Deprecated{superseded_by && !compact ? ` → ${superseded_by}` : ''}
    </span>
  )
}
