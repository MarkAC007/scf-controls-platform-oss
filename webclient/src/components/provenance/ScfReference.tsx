import React from 'react'

interface ScfReferenceProps {
  /** Kicker label rendered above the content, e.g. "SCF Reference". Omit for unlabeled bedrock. */
  source?: string
  /** Brass catalog tag rendered top-right, e.g. "SCF Catalog". */
  tag?: string
  className?: string
  children: React.ReactNode
}

/**
 * Wraps immutable SCF catalog content — renders flat ("bedrock"): no card
 * chrome, serif prose, provenance kicker. See docs/design/bedrock-and-ledger.md
 * Never place editable fields inside.
 */
export function ScfReference({ source, tag, className, children }: ScfReferenceProps) {
  return (
    <section
      className={`surface-bedrock${className ? ` ${className}` : ''}`}
      {...(source ? { 'data-source': source } : {})}
    >
      {tag && <span className="scf-source-tag">{tag}</span>}
      {children}
    </section>
  )
}
