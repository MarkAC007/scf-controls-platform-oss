import { useRef, type JSX, type KeyboardEvent } from 'react'

export interface TabRowItem {
  id: string
  label: string
  count?: number
}

export interface TabRowProps {
  tabs: TabRowItem[]
  activeId: string
  onSelect: (id: string) => void
  'aria-label': string
}

export default function TabRow({
  tabs,
  activeId,
  onSelect,
  'aria-label': ariaLabel,
}: TabRowProps): JSX.Element {
  const buttonRefs = useRef<(HTMLButtonElement | null)[]>([])

  function handleKeyDown(e: KeyboardEvent<HTMLButtonElement>, index: number): void {
    if (e.key === 'ArrowRight') {
      const next = (index + 1) % tabs.length
      onSelect(tabs[next].id)
      buttonRefs.current[next]?.focus()
    } else if (e.key === 'ArrowLeft') {
      const prev = (index - 1 + tabs.length) % tabs.length
      onSelect(tabs[prev].id)
      buttonRefs.current[prev]?.focus()
    }
  }

  return (
    <div className="explorer-tabs" role="tablist" aria-label={ariaLabel}>
      {tabs.map((tab, index) => {
        const isActive = tab.id === activeId
        return (
          <button
            key={tab.id}
            ref={(el) => {
              buttonRefs.current[index] = el
            }}
            role="tab"
            aria-selected={isActive}
            className={`explorer-tab${isActive ? ' explorer-tab--active' : ''}`}
            onClick={() => onSelect(tab.id)}
            onKeyDown={(e) => handleKeyDown(e, index)}
            tabIndex={isActive ? 0 : -1}
          >
            {tab.label}
            {tab.count !== undefined && (
              <span className="explorer-tab-count">{tab.count}</span>
            )}
          </button>
        )
      })}
    </div>
  )
}
