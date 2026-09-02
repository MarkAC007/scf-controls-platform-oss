import type { JSX, KeyboardEvent, ReactNode } from 'react'

export interface FilterSidebarProps {
  children: ReactNode
  collapsed: boolean
  onToggleCollapsed: () => void
  'aria-label'?: string
}

export default function FilterSidebar({
  children,
  collapsed,
  onToggleCollapsed,
  'aria-label': ariaLabel = 'Filters',
}: FilterSidebarProps): JSX.Element {
  return (
    <aside
      className={`explorer-filters${collapsed ? ' explorer-filters--collapsed' : ''}`}
      aria-label={ariaLabel}
    >
      <button
        className="explorer-filters-toggle"
        type="button"
        aria-label={collapsed ? 'Expand filters' : 'Collapse filters'}
        aria-expanded={!collapsed}
        onClick={onToggleCollapsed}
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 16 16"
          fill="none"
          aria-hidden="true"
        >
          {collapsed ? (
            <path
              d="M6 3l5 5-5 5"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ) : (
            <path
              d="M10 3L5 8l5 5"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )}
        </svg>
      </button>
      {!collapsed && (
        <div className="explorer-filters-content">{children}</div>
      )}
    </aside>
  )
}

// Phones default the filter rail closed — expanded it eats over half the
// viewport. The toggle still opens it; this only sets the initial state.
// jsdom does not implement matchMedia, so the capability check is not
// redundant with the window check — without it every test rendering an
// explorer page throws.
export function defaultFiltersCollapsed(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(max-width: 700px)').matches
  )
}

export function FilterGroup({
  label,
  children,
}: {
  label: string
  children: ReactNode
}): JSX.Element {
  return (
    <div className="explorer-filter-group">
      <div className="explorer-filter-label">{label}</div>
      {children}
    </div>
  )
}

export function FilterCheckbox({
  label,
  checked,
  onChange,
  count,
}: {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
  count?: number
}): JSX.Element {
  return (
    <label className="explorer-filter-check">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="explorer-filter-check-box" aria-hidden="true" />
      <span className="explorer-filter-check-label">{label}</span>
      {count !== undefined && (
        <span className="explorer-filter-check-count">{count}</span>
      )}
    </label>
  )
}

export function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label?: string
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
}): JSX.Element {
  return (
    <div className="explorer-filter-select-wrap">
      {label !== undefined && (
        <div className="explorer-filter-label">{label}</div>
      )}
      <div className="explorer-filter-select-chrome">
        <select
          className="explorer-filter-select"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        >
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <svg
          className="explorer-filter-select-arrow"
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          aria-hidden="true"
        >
          <path
            d="M2 4l4 4 4-4"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
      </div>
    </div>
  )
}
