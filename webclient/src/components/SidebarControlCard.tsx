import React, { memo } from 'react'

interface SidebarControlCardProps {
  scfId: string
  controlName: string
  isSelected: boolean
  onSelect: () => void
  style?: React.CSSProperties
  /** Optional checkbox for scoping pages */
  checkbox?: {
    checked: boolean
    onChange: () => void
  }
  /** Optional scope badge (IN SCOPE / OUT) */
  scopeBadge?: {
    inScope: boolean
  }
  /** Optional implementation status badge */
  statusBadge?: string | null
}

function SidebarControlCardComponent({
  scfId,
  controlName,
  isSelected,
  onSelect,
  style,
  checkbox,
  scopeBadge,
  statusBadge,
}: SidebarControlCardProps) {
  return (
    <div style={style}>
      <div
        className={`sidebar-control-card ${isSelected ? 'active' : ''}`}
        onClick={onSelect}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onSelect() }}
      >
        {checkbox && (
          <div className="sidebar-card-checkbox">
            <input
              type="checkbox"
              checked={checkbox.checked}
              onChange={(e) => {
                e.stopPropagation()
                checkbox.onChange()
              }}
              className="modern-checkbox"
            />
          </div>
        )}
        <div className="sidebar-card-content">
          <div className="sidebar-card-header">
            <span className="badge-modern">{scfId}</span>
            {scopeBadge && (
              <span className={`scope-badge-compact${scopeBadge.inScope ? '' : ' out'}`}>
                {scopeBadge.inScope ? 'IN SCOPE' : 'OUT'}
              </span>
            )}
            {statusBadge && (
              <span className={`status-badge-compact status-${statusBadge}`}>
                {statusBadge.replace('_', ' ')}
              </span>
            )}
          </div>
          <div className="sidebar-card-name">{controlName}</div>
        </div>
      </div>
    </div>
  )
}

export const SidebarControlCard = memo(SidebarControlCardComponent, (prev, next) => {
  return (
    prev.scfId === next.scfId &&
    prev.controlName === next.controlName &&
    prev.isSelected === next.isSelected &&
    prev.style?.top === next.style?.top &&
    prev.checkbox?.checked === next.checkbox?.checked &&
    prev.scopeBadge?.inScope === next.scopeBadge?.inScope &&
    prev.statusBadge === next.statusBadge
  )
})

export default SidebarControlCard
