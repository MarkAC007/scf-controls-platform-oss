import type { JSX, ReactNode } from 'react'

export interface ListToolbarProps {
  search: string
  onSearchChange: (value: string) => void
  searchPlaceholder: string
  count?: ReactNode
  actions?: ReactNode
}

export default function ListToolbar({
  search,
  onSearchChange,
  searchPlaceholder,
  count,
  actions,
}: ListToolbarProps): JSX.Element {
  return (
    <div className="explorer-toolbar">
      <input
        className="explorer-toolbar-search"
        type="search"
        aria-label={searchPlaceholder}
        placeholder={searchPlaceholder}
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
      />
      {count !== undefined && (
        <span className="explorer-toolbar-count">{count}</span>
      )}
      {actions !== undefined && (
        <div className="explorer-toolbar-actions">{actions}</div>
      )}
    </div>
  )
}
