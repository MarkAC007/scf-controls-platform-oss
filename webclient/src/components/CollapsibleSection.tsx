import { useState, ReactNode } from 'react'

interface Props {
  title: string
  icon: string
  children: ReactNode
  defaultCollapsed?: boolean
  count?: number
  badge?: ReactNode
}

export default function CollapsibleSection({
  title,
  icon,
  children,
  defaultCollapsed = false,
  count,
  badge
}: Props) {
  const [isCollapsed, setIsCollapsed] = useState(defaultCollapsed)

  return (
    <div className={`detail-section-container ${isCollapsed ? 'collapsed' : ''}`}>
      <div
        className="container-header collapsible"
        onClick={() => setIsCollapsed(!isCollapsed)}
      >
        <span className="container-icon">{icon}</span>
        <span className="container-title">{title}</span>
        {count !== undefined && <span className="container-count">{count}</span>}
        {badge}
        <span className="collapse-indicator">{isCollapsed ? '▶' : '▼'}</span>
      </div>
      {!isCollapsed && (
        <div className="container-content">
          {children}
        </div>
      )}
    </div>
  )
}
