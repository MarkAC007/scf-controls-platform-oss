import React from 'react'

interface WorkspaceRecordProps {
  /** Bench header, always phrased as the organization's own record, e.g. "Your Implementation Record". */
  title?: string
  className?: string
  children: React.ReactNode
}

/**
 * Wraps the organization's editable record — renders as a raised "workbench"
 * card with upgraded field affordances. See docs/design/bedrock-and-ledger.md
 */
export function WorkspaceRecord({ title = 'Your Record', className, children }: WorkspaceRecordProps) {
  return (
    <div className={`detail-section-container surface-bench${className ? ` ${className}` : ''}`}>
      <div className="container-header bench-header">
        <span className="container-title">{title}</span>
      </div>
      <div className="container-content">{children}</div>
    </div>
  )
}
