import { useEffect, useRef, useState, type ReactNode } from 'react'

export interface SettingsSectionLink {
  /** Element id of the section this link scrolls to. */
  id: string
  label: string
}

interface SettingsSectionNavProps {
  sections: SettingsSectionLink[]
  note?: ReactNode
}

/** How far below the pane's top edge a section heading counts as "current". */
const ACTIVE_OFFSET_PX = 96

/**
 * Settings' left index. It stays in view while the settings pane scrolls
 * (sticky, see `.settings-section-nav` in styles.css) and marks the section
 * currently at the top of that pane (UIP-018).
 *
 * The pane that scrolls is `.app-content`, not the window, so the scroll-spy
 * listens there; it falls back to the window where no such ancestor exists.
 */
export default function SettingsSectionNav({ sections, note }: SettingsSectionNavProps) {
  const navRef = useRef<HTMLElement>(null)
  const [activeId, setActiveId] = useState<string | null>(sections[0]?.id ?? null)
  const sectionKey = sections.map(s => s.id).join('|')

  useEffect(() => {
    const scroller: HTMLElement | null = navRef.current?.closest('.app-content') ?? null
    const target: HTMLElement | Window = scroller ?? window

    const update = () => {
      const top = scroller ? scroller.getBoundingClientRect().top : 0
      // Only a pane that actually scrolls can be "at the bottom".
      const doc = document.documentElement
      const atBottom = scroller
        ? scroller.scrollHeight > scroller.clientHeight &&
          scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 2
        : doc.scrollHeight > window.innerHeight &&
          window.innerHeight + window.scrollY >= doc.scrollHeight - 2
      let current: string | null = sections[0]?.id ?? null
      for (const { id } of sections) {
        const el = document.getElementById(id)
        if (!el) continue
        if (el.getBoundingClientRect().top - top <= ACTIVE_OFFSET_PX) current = id
      }
      // A short final section can never reach the top of the pane; at the
      // very bottom, the last section is the one being read.
      if (atBottom && sections.length > 0) current = sections[sections.length - 1].id
      setActiveId(current)
    }

    update()
    target.addEventListener('scroll', update, { passive: true })
    window.addEventListener('resize', update)
    return () => {
      target.removeEventListener('scroll', update)
      window.removeEventListener('resize', update)
    }
    // sectionKey captures the only change to `sections` that matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sectionKey])

  return (
    <nav ref={navRef} className="settings-section-nav" aria-label="Settings sections">
      {sections.map(({ id, label }) => (
        <a
          key={id}
          className={`settings-section-nav-item${activeId === id ? ' active' : ''}`}
          href={`#${id}`}
          aria-current={activeId === id ? 'location' : undefined}
          onClick={() => setActiveId(id)}
        >
          {label}
        </a>
      ))}
      {note && <p className="settings-section-nav-note">{note}</p>}
    </nav>
  )
}
