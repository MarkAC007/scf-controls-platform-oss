/**
 * ThemeMenu — the single look-and-feel control, in the header on every page.
 * Lists built-in and installed themes, and installs new ones from a JSON file.
 */
import { useEffect, useRef, useState } from 'react'
import { toast } from 'react-hot-toast'
import { useTheme } from '../contexts/ThemeContext'
import { builtinThemes } from '../themes'
import type { ThemeDefinition } from '../themes'

const SWATCH_KEYS = ['--bg', '--card', '--primary', '--accent', '--success'] as const

const BASE_SWATCHES: Record<'light' | 'dark', Record<string, string>> = {
  light: {
    '--bg': 'hsl(0, 0%, 100%)',
    '--card': 'hsl(0, 0%, 100%)',
    '--primary': '#1976d2',
    '--accent': 'hsl(222.2, 47.4%, 11.2%)',
    '--success': 'hsl(142, 76%, 36%)',
  },
  dark: {
    '--bg': '#0b1326',
    '--card': '#171f33',
    '--primary': '#42a5f5',
    '--accent': '#dae2fd',
    '--success': '#4ade80',
  },
}

const builtinIds = new Set(builtinThemes.map(t => t.id))

function swatchColors(theme: ThemeDefinition): string[] {
  return SWATCH_KEYS.map(key => theme.variables[key] ?? BASE_SWATCHES[theme.base][key])
}

export default function ThemeMenu() {
  const { themeId, availableThemes, setThemeId, installTheme, removeCustomTheme } = useTheme()
  const [isOpen, setIsOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  useEffect(() => {
    function handleEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setIsOpen(false)
    }
    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [])

  const handleImport = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    try {
      const text = await file.text()
      const theme = installTheme(JSON.parse(text))
      toast.success(`Theme "${theme.name}" installed`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Invalid theme file')
    }
  }

  const handleRemove = (theme: ThemeDefinition, e: React.MouseEvent) => {
    e.stopPropagation()
    removeCustomTheme(theme.id)
    toast.success(`Theme "${theme.name}" removed`)
  }

  return (
    <div className="theme-menu" ref={menuRef}>
      <button
        onClick={() => setIsOpen(open => !open)}
        className="theme-toggle"
        aria-label="Choose theme"
        aria-expanded={isOpen}
        title="Choose theme"
      >
        {/* palette icon */}
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <circle cx="13.5" cy="6.5" r=".5" />
          <circle cx="17.5" cy="10.5" r=".5" />
          <circle cx="8.5" cy="7.5" r=".5" />
          <circle cx="6.5" cy="12.5" r=".5" />
          <path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z" />
        </svg>
      </button>

      {isOpen && (
        <div className="theme-menu-panel" role="menu">
          <div className="theme-menu-heading">Theme</div>
          {availableThemes.map(theme => (
            <button
              key={theme.id}
              role="menuitemradio"
              aria-checked={theme.id === themeId}
              className={`theme-menu-item${theme.id === themeId ? ' theme-menu-item-active' : ''}`}
              title={theme.description}
              onClick={() => { setThemeId(theme.id); setIsOpen(false) }}
            >
              <span className="theme-menu-swatches">
                {swatchColors(theme).map((color, i) => (
                  <span key={i} className="theme-menu-swatch" style={{ background: color }} />
                ))}
              </span>
              <span className="theme-menu-name">{theme.name}</span>
              <span className={`theme-base-badge theme-base-${theme.base}`}>{theme.base}</span>
              {!builtinIds.has(theme.id) && (
                <span
                  className="theme-menu-remove"
                  title="Remove installed theme"
                  onClick={e => handleRemove(theme, e)}
                >
                  ✕
                </span>
              )}
              {theme.id === themeId && <span className="theme-menu-check">✓</span>}
            </button>
          ))}
          <div className="theme-menu-divider" />
          <button
            className="theme-menu-item theme-menu-install"
            onClick={() => fileInputRef.current?.click()}
          >
            Install theme from file…
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".json,application/json"
            style={{ display: 'none' }}
            onChange={handleImport}
          />
        </div>
      )}
    </div>
  )
}
